"""Application configuration via pydantic-settings."""

from pathlib import Path

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class VaultPathError(ValueError):
    """Raised when a caller-supplied path resolves outside the vault."""


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
    # BAAI/bge-small-en-v1.5 (~130 MB, 384 dims) is the default for low memory usage.
    # nomic-ai/nomic-embed-text-v1.5 (~1.5 GB, 768 dims) gives higher quality at the cost of RAM.
    # Changing models on an existing vault requires deleting the DB to rebuild the index.
    embedding_model: str = "BAAI/bge-small-en-v1.5"
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

    def resolve_in_vault(self, file_path: str | Path) -> Path:
        """Resolve *file_path* and confirm it stays inside the vault.

        Relative paths are resolved against the vault root.  Symlinks are
        followed on both sides before comparing, so a link inside the vault
        cannot be used to reach a file outside it.

        Raises:
            VaultPathError: if the resolved path escapes the vault.
        """
        path = Path(file_path).expanduser()
        if not path.is_absolute():
            path = self.vault_path / path

        resolved = path.resolve()
        vault = self.vault_path.resolve()
        if resolved != vault and not resolved.is_relative_to(vault):
            raise VaultPathError(f"Path is outside the vault: {str(file_path)!r}")
        return resolved

    def is_ignored_path(self, path: Path) -> bool:
        """Return True if path should be excluded from indexing."""
        parts = path.parts
        # Always ignore Obsidian system directories
        for system_dir in (".obsidian", ".obsidian-search"):
            if system_dir in parts:
                return True
        # Ignore user-configured excluded folders
        return any(folder in parts for folder in self.excluded_folders)
