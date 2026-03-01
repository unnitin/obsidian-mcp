import { App, PluginSettingTab, Setting } from "obsidian";
import type SemanticSearchPlugin from "./main";
import type { SemanticSearchSettings } from "./types";

export class SemanticSearchSettingTab extends PluginSettingTab {
  plugin: SemanticSearchPlugin;

  constructor(app: App, plugin: SemanticSearchPlugin) {
    super(app, plugin);
    this.plugin = plugin;
  }

  display(): void {
    const { containerEl } = this;
    containerEl.empty();

    containerEl.createEl("h2", { text: "Semantic Search Settings" });

    new Setting(containerEl)
      .setName("Backend URL")
      .setDesc("URL of the obsidian-search backend server.")
      .addText((text) =>
        text
          .setPlaceholder("http://127.0.0.1:51234")
          .setValue(this.plugin.settings.serverUrl)
          .onChange(async (value) => {
            this.plugin.settings.serverUrl = value.trim();
            await this.plugin.saveSettings();
          })
      );

    new Setting(containerEl)
      .setName("Default results count")
      .setDesc("How many search results to show by default.")
      .addSlider((slider) =>
        slider
          .setLimits(1, 50, 1)
          .setValue(this.plugin.settings.defaultTopK)
          .setDynamicTooltip()
          .onChange(async (value) => {
            this.plugin.settings.defaultTopK = value;
            await this.plugin.saveSettings();
          })
      );

    new Setting(containerEl)
      .setName("Index on save")
      .setDesc("Automatically reindex a note when you save it.")
      .addToggle((toggle) =>
        toggle.setValue(this.plugin.settings.indexOnSave).onChange(async (value) => {
          this.plugin.settings.indexOnSave = value;
          await this.plugin.saveSettings();
        })
      );

    new Setting(containerEl)
      .setName("Show relevance scores")
      .setDesc("Display the similarity score next to each search result.")
      .addToggle((toggle) =>
        toggle.setValue(this.plugin.settings.showScores).onChange(async (value) => {
          this.plugin.settings.showScores = value;
          await this.plugin.saveSettings();
        })
      );

    new Setting(containerEl)
      .setName("Excluded folders")
      .setDesc("Comma-separated list of folder names to skip during indexing.")
      .addText((text) =>
        text
          .setPlaceholder("Templates, Archive")
          .setValue(this.plugin.settings.excludedFolders.join(", "))
          .onChange(async (value) => {
            this.plugin.settings.excludedFolders = value
              .split(",")
              .map((s) => s.trim())
              .filter(Boolean);
            await this.plugin.saveSettings();
          })
      );
  }
}

export type { SemanticSearchSettings };
