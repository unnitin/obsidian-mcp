"""PDF chunker — converts PDF pages to Markdown, then applies MarkdownChunker."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from obsidian_search.ingestion.chunker_markdown import MarkdownChunker
from obsidian_search.models import Chunk, ChunkId, SourceType


class PDFChunker:
    """Extract text from a PDF and chunk it using the Markdown chunker.

    Uses pymupdf4llm to convert PDF pages to structured Markdown, preserving
    tables, columns, and headings (inferred from font size). The resulting
    Markdown is fed into MarkdownChunker for consistent chunking behaviour.
    """

    def __init__(
        self,
        max_tokens: int = 512,
        min_tokens: int = 64,
        overlap_tokens: int = 50,
    ) -> None:
        self._md_chunker = MarkdownChunker(
            max_tokens=max_tokens,
            min_tokens=min_tokens,
            overlap_tokens=overlap_tokens,
        )

    def chunk(self, path: Path, mtime: float) -> list[Chunk]:
        """Return chunks for *path*; returns [] if the file is unreadable."""
        try:
            import pymupdf4llm
        except ImportError as exc:
            raise ImportError(
                "pymupdf4llm is required for PDF chunking: pip install pymupdf4llm"
            ) from exc

        file_path = str(path)
        try:
            md_text: str = pymupdf4llm.to_markdown(str(path))
        except Exception:  # noqa: BLE001
            return []

        if not md_text.strip():
            return []

        # Use MarkdownChunker on the converted text; override source_type afterwards
        raw_chunks = self._md_chunker.chunk(
            content=md_text,
            file_path=file_path,
            mtime=mtime,
        )

        # Re-stamp with PDF source type and regenerate stable IDs
        chunks: list[Chunk] = []
        for idx, c in enumerate(raw_chunks):
            meta: dict[str, Any] = {**c.metadata}
            chunks.append(
                Chunk(
                    id=ChunkId.generate(file_path, idx),
                    source_type=SourceType.PDF,
                    file_path=file_path,
                    header_path=c.header_path,
                    content=c.content,
                    mtime=mtime,
                    chunk_index=idx,
                    metadata=meta,
                )
            )
        return chunks
