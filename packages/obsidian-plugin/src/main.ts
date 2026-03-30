import { Notice, Plugin, TFile } from "obsidian";
import { ApiClient } from "./api-client";
import { SemanticSearchSettingTab } from "./settings";
import { createDebouncedModal } from "./search-modal";
import { DEFAULT_SETTINGS, type SemanticSearchSettings } from "./types";

export default class SemanticSearchPlugin extends Plugin {
  settings!: SemanticSearchSettings;
  private client!: ApiClient;
  private _indexTimer: ReturnType<typeof setTimeout> | null = null;

  async onload(): Promise<void> {
    await this.loadSettings();
    this.client = new ApiClient(this.settings);

    // ── Commands ───────────────────────────────────────────────────────────

    this.addCommand({
      id: "open-semantic-search",
      name: "Open semantic search",
      hotkeys: [{ modifiers: ["Mod", "Shift"], key: "f" }],
      callback: () => {
        const modal = createDebouncedModal(this.app, this.client, this.settings);
        modal.open();
      },
    });

    this.addCommand({
      id: "index-current-url",
      name: "Index URL from clipboard",
      callback: async () => {
        const url = await navigator.clipboard.readText();
        if (!url.startsWith("http")) {
          new Notice("Clipboard does not contain a valid URL");
          return;
        }
        try {
          const result = await this.client.ingestUrl(url);
          new Notice(`Indexed ${result.chunks_added} chunks from ${url}`);
        } catch (err) {
          new Notice(`Failed to index URL: ${String(err)}`);
        }
      },
    });

    this.addCommand({
      id: "index-pdf-file",
      name: "Index PDF file",
      callback: async () => {
        const files = this.app.vault.getFiles().filter((f) => f.extension === "pdf");
        if (files.length === 0) {
          new Notice("No PDF files found in vault");
          return;
        }
        const { SuggestModal } = await import("obsidian");
        class PdfPickerModal extends SuggestModal<TFile> {
          constructor(
            app: Parameters<typeof SuggestModal>[0],
            private onChoose: (file: TFile) => void
          ) {
            super(app);
            this.setPlaceholder("Select a PDF file to index…");
          }
          getSuggestions(query: string): TFile[] {
            return files.filter((f) =>
              f.path.toLowerCase().includes(query.toLowerCase())
            );
          }
          renderSuggestion(file: TFile, el: HTMLElement): void {
            el.createEl("div", { text: file.name });
            el.createEl("small", { text: file.path, cls: "u-muted" });
          }
          onChooseSuggestion(file: TFile): void {
            this.onChoose(file);
          }
        }
        new PdfPickerModal(this.app, async (file) => {
          try {
            const absPath = (this.app.vault.adapter as { basePath?: string }).basePath
              ? `${(this.app.vault.adapter as { basePath: string }).basePath}/${file.path}`
              : file.path;
            const result = await this.client.ingestPdf(absPath);
            new Notice(`Indexed ${result.chunks_added} chunks from ${file.name}`);
          } catch (err) {
            new Notice(`Failed to index PDF: ${String(err)}`);
          }
        }).open();
      },
    });

    this.addCommand({
      id: "reindex-current-note",
      name: "Reindex current note",
      callback: async () => {
        const file = this.app.workspace.getActiveFile();
        if (!file) {
          new Notice("No active note");
          return;
        }
        await this._indexFile(file, "Manual reindex");
      },
    });

    // ── File save hook ─────────────────────────────────────────────────────

    this.registerEvent(
      this.app.vault.on("modify", (file) => {
        if (!this.settings.indexOnSave) return;
        if (!(file instanceof TFile) || file.extension !== "md") return;

        // Debounce: wait 1.5 s after the last modify before indexing
        if (this._indexTimer !== null) clearTimeout(this._indexTimer);
        this._indexTimer = setTimeout(() => {
          this._indexTimer = null;
          void this._indexFile(file, "Auto-indexed after save");
        }, 1500);
      })
    );

    // ── Settings tab ───────────────────────────────────────────────────────

    this.addSettingTab(new SemanticSearchSettingTab(this.app, this));

    // ── Status bar ────────────────────────────────────────────────────────

    const statusItem = this.addStatusBarItem();
    statusItem.setText("🔍 Semantic Search");
    statusItem.onClickEvent(() => {
      const modal = createDebouncedModal(this.app, this.client, this.settings);
      modal.open();
    });

    this._checkBackendHealth();
  }

  onunload(): void {
    if (this._indexTimer !== null) {
      clearTimeout(this._indexTimer);
    }
  }

  async loadSettings(): Promise<void> {
    this.settings = Object.assign({}, DEFAULT_SETTINGS, await this.loadData());
  }

  async saveSettings(): Promise<void> {
    await this.saveData(this.settings);
    // Rebuild client with updated URL
    this.client = new ApiClient(this.settings);
  }

  private async _indexFile(file: TFile, successMsg: string): Promise<void> {
    try {
      const absPath = (this.app.vault.adapter as { basePath?: string }).basePath
        ? `${(this.app.vault.adapter as { basePath: string }).basePath}/${file.path}`
        : file.path;

      // POST to the search endpoint isn't the right one — use ingest URL endpoint
      // We send the absolute path; the backend uses pipeline.index_file(path)
      await this.client.ingestMarkdown(absPath);
      console.debug(`[SemanticSearch] ${successMsg}: ${file.path}`);
    } catch (err) {
      console.warn(`[SemanticSearch] Failed to index ${file.path}:`, err);
    }
  }

  private async _checkBackendHealth(): Promise<void> {
    const ok = await this.client.health();
    if (!ok) {
      new Notice(
        "Semantic Search: backend not reachable at " + this.settings.serverUrl,
        5000
      );
    }
  }
}
