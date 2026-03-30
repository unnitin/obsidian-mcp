/** Typed fetch() wrapper for the obsidian-search backend. */

import type {
  IngestResult,
  SearchResponse,
  SemanticSearchSettings,
  StatusResponse,
} from "./types";

export class ApiClient {
  private baseUrl: string;

  constructor(settings: SemanticSearchSettings) {
    this.baseUrl = settings.serverUrl.replace(/\/$/, "");
  }

  private async post<T>(path: string, body: unknown): Promise<T> {
    const res = await fetch(`${this.baseUrl}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      throw new Error(`Backend error ${res.status}: ${await res.text()}`);
    }
    return res.json() as Promise<T>;
  }

  private async delete<T>(path: string, body: unknown): Promise<T> {
    const res = await fetch(`${this.baseUrl}${path}`, {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      throw new Error(`Backend error ${res.status}: ${await res.text()}`);
    }
    return res.json() as Promise<T>;
  }

  async health(): Promise<boolean> {
    try {
      const res = await fetch(`${this.baseUrl}/health`);
      return res.ok;
    } catch {
      return false;
    }
  }

  async search(
    query: string,
    topK: number,
    sourcetypes?: string[],
    tags?: string[]
  ): Promise<SearchResponse> {
    return this.post<SearchResponse>("/search", {
      query,
      top_k: topK,
      source_types: sourcetypes ?? null,
      tags: tags ?? null,
    });
  }

  async status(): Promise<StatusResponse> {
    const res = await fetch(`${this.baseUrl}/status`);
    if (!res.ok) throw new Error(`Status error: ${res.status}`);
    return res.json() as Promise<StatusResponse>;
  }

  async ingestUrl(url: string, tags?: string[]): Promise<IngestResult> {
    return this.post<IngestResult>("/ingest/url", { url, tags: tags ?? null });
  }

  async ingestPdf(filePath: string): Promise<IngestResult> {
    return this.post<IngestResult>("/ingest/pdf", { file_path: filePath });
  }

  async ingestMarkdown(filePath: string): Promise<IngestResult> {
    return this.post<IngestResult>("/ingest/file", { file_path: filePath });
  }

  async removeDocument(filePath: string): Promise<IngestResult> {
    return this.delete<IngestResult>("/index/document", { file_path: filePath });
  }
}
