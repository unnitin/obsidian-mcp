import { App, SuggestModal } from "obsidian";
import type { ApiClient } from "./api-client";
import type { SearchResult, SemanticSearchSettings } from "./types";

/** Debounce helper. */
function debounce<T extends (...args: Parameters<T>) => void>(
  fn: T,
  wait: number
): (...args: Parameters<T>) => void {
  let timer: ReturnType<typeof setTimeout>;
  return (...args: Parameters<T>) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), wait);
  };
}

export class SemanticSearchModal extends SuggestModal<SearchResult> {
  private client: ApiClient;
  private settings: SemanticSearchSettings;
  private _lastQuery = "";
  private _cachedResults: SearchResult[] = [];

  constructor(app: App, client: ApiClient, settings: SemanticSearchSettings) {
    super(app);
    this.client = client;
    this.settings = settings;
    this.setPlaceholder("Search your vault semantically…");
    this.setInstructions([
      { command: "↑↓", purpose: "navigate" },
      { command: "↵", purpose: "open" },
      { command: "esc", purpose: "close" },
    ]);
  }

  // Called on every keystroke; debounced internally via getSuggestions
  async getSuggestions(query: string): Promise<SearchResult[]> {
    if (!query.trim()) return [];
    if (query === this._lastQuery) return this._cachedResults;

    try {
      const resp = await this.client.search(query, this.settings.defaultTopK);
      this._lastQuery = query;
      this._cachedResults = resp.results;
      return resp.results;
    } catch {
      return [];
    }
  }

  renderSuggestion(result: SearchResult, el: HTMLElement): void {
    const title = result.file_path.split("/").pop() ?? result.file_path;
    const breadcrumb = result.header_path ?? "";
    const excerpt = result.content.replace(/\n/g, " ").trim().slice(0, 100);
    const badge = result.source_type !== "markdown" ? result.source_type : null;

    const container = el.createDiv({ cls: "semantic-search-result" });

    const titleLine = container.createDiv({ cls: "semantic-search-result-title" });
    titleLine.createSpan({ text: title });
    if (badge) {
      titleLine.createSpan({ text: badge, cls: "semantic-search-result-badge" });
    }
    if (this.settings.showScores) {
      titleLine.createSpan({
        text: ` ${(result.score * 100).toFixed(0)}%`,
        cls: "semantic-search-result-score",
      });
    }

    if (breadcrumb) {
      container.createDiv({ text: breadcrumb, cls: "semantic-search-result-breadcrumb" });
    }

    container.createDiv({ text: excerpt, cls: "semantic-search-result-excerpt" });
  }

  onChooseSuggestion(result: SearchResult, _evt: MouseEvent | KeyboardEvent): void {
    // For web/url results, open the URL externally
    if (result.source_type === "web" && result.url) {
      window.open(result.url, "_blank");
      return;
    }

    // Open the note in Obsidian and scroll to the heading if available
    const linkText = result.header_path
      ? `${result.file_path}#${result.header_path.split(" > ").pop()}`
      : result.file_path;

    this.app.workspace.openLinkText(linkText, "", false, { active: true });
  }
}

/** Create a debounced version of SemanticSearchModal.getSuggestions. */
export function createDebouncedModal(
  app: App,
  client: ApiClient,
  settings: SemanticSearchSettings
): SemanticSearchModal {
  const modal = new SemanticSearchModal(app, client, settings);

  // Override getSuggestions with a debounced variant
  const originalGet = modal.getSuggestions.bind(modal);
  const debouncedGet = debounce(originalGet, 300);
  modal.getSuggestions = debouncedGet as typeof modal.getSuggestions;

  return modal;
}
