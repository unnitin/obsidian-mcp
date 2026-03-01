/** Shared TypeScript interfaces for the Obsidian Semantic Search plugin. */

export interface SearchResult {
  chunk_id: string;
  content: string;
  score: number;
  source_type: "markdown" | "pdf" | "web";
  file_path: string;
  header_path: string | null;
  url: string | null;
}

export interface SearchResponse {
  results: SearchResult[];
  query_time_ms: number;
}

export interface StatusResponse {
  total_chunks: number;
  total_documents: number;
  last_indexed_at: number | null;
  index_size_bytes: number;
  is_watching: boolean;
}

export interface IngestResult {
  chunks_added: number;
  chunks_removed: number;
  status: string;
}

export interface SemanticSearchSettings {
  serverUrl: string;
  defaultTopK: number;
  excludedFolders: string[];
  indexOnSave: boolean;
  showScores: boolean;
}

export const DEFAULT_SETTINGS: SemanticSearchSettings = {
  serverUrl: "http://127.0.0.1:51234",
  defaultTopK: 10,
  excludedFolders: [],
  indexOnSave: true,
  showScores: false,
};
