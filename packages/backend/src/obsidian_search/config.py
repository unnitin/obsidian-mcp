"""Application configuration via pydantic-settings."""

from pathlib import Path

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="OBSIDIAN_SEARCH_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        populate_by_name=True,
    )

    # Core — accepts VAULT_PATH or OBSIDIAN_SEARCH_VAULT_PATH
    vault_path: Path = Field(
        validation_alias=AliasChoices("vault_path", "VAULT_PATH", "OBSIDIAN_SEARCH_VAULT_PATH"),
    )

    # Server
    host: str = "127.0.0.1"
    port: int = 51234

    # Embedding
    embedding_model: str = "nomic-ai/nomic-embed-text-v1.5"
    embedding_batch_size: int = 32

    # Reranking — disabled by default; ANN scores from nomic-embed-text are
    # more discriminative than cross-encoder logits for personal notes.
    # Set OBSIDIAN_SEARCH_RERANKER_ENABLED=true to enable.
    reranker_enabled: bool = False
    reranker_model: str = "BAAI/bge-reranker-base"

    # Chunking
    chunk_max_tokens: int = 512
    chunk_min_tokens: int = 64
    chunk_overlap_tokens: int = 50

    # Search
    default_top_k: int = 10
    rerank_candidates: int = 50

    # Watcher
    watcher_debounce_seconds: float = 2.0

    # Indexing
    excluded_folders: list[str] = []

    @field_validator("vault_path", mode="before")
    @classmethod
    def resolve_vault_path(cls, v: str | Path) -> Path:
        return Path(v)

    @property
    def db_dir(self) -> Path:
        return self.vault_path / ".obsidian-search"

    @property
    def db_path(self) -> Path:
        return self.db_dir / "semantic-search.db"

    def is_ignored_path(self, path: Path) -> bool:
        """Return True if path should be excluded from indexing."""
        parts = path.parts
        # Always ignore Obsidian system directories
        for system_dir in (".obsidian", ".obsidian-search"):
            if system_dir in parts:
                return True
        # Ignore user-configured excluded folders
        return any(folder in parts for folder in self.excluded_folders)
