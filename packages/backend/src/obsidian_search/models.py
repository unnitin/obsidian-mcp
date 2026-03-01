"""Core data models for obsidian-search."""

import hashlib
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class SourceType(StrEnum):
    MARKDOWN = "markdown"
    PDF = "pdf"
    WEB = "web"


class ChunkId:
    """Deterministic chunk identifier helpers."""

    @staticmethod
    def generate(file_path: str, chunk_index: int) -> str:
        """SHA-256 hex digest of file_path + chunk_index."""
        key = f"{file_path}:{chunk_index}"
        return hashlib.sha256(key.encode()).hexdigest()


class Chunk(BaseModel):
    """A single indexed chunk of content."""

    id: str
    source_type: SourceType
    file_path: str
    url: str | None = None
    header_path: str | None = None
    content: str
    mtime: float
    chunk_index: int = Field(ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("content")
    @classmethod
    def content_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("content must not be empty")
        return v


class SearchResult(BaseModel):
    """A single result returned from semantic search."""

    chunk_id: str
    content: str
    score: float = Field(ge=0.0, le=1.0)
    source_type: SourceType
    file_path: str
    header_path: str | None = None
    url: str | None = None


class IndexStatus(BaseModel):
    """Current state of the search index."""

    total_chunks: int
    total_documents: int
    last_indexed_at: datetime | None
    index_size_bytes: int
    is_watching: bool


class IndexedFile(BaseModel):
    """Metadata for a single indexed document."""

    file_path: str
    source_type: SourceType
    chunk_count: int
    last_indexed: datetime
    url: str | None = None


class IngestResult(BaseModel):
    """Result of an ingestion operation."""

    chunks_added: int
    chunks_removed: int = 0
    status: str
