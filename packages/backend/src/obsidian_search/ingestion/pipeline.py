"""Indexing pipeline — orchestrates chunking, embedding, and storage."""

from __future__ import annotations

from pathlib import Path

from obsidian_search.config import Settings
from obsidian_search.embedding.embedder import Embedder
from obsidian_search.ingestion.chunker_markdown import MarkdownChunker
from obsidian_search.models import IngestResult
from obsidian_search.store.vector_store import VectorStore


class IndexingPipeline:
    def __init__(
        self,
        settings: Settings,
        store: VectorStore,
        embedder: Embedder,
    ) -> None:
        self.settings = settings
        self.store = store
        self.embedder = embedder
        self._md_chunker = MarkdownChunker(
            max_tokens=settings.chunk_max_tokens,
            min_tokens=settings.chunk_min_tokens,
            overlap_tokens=settings.chunk_overlap_tokens,
        )

    def index_file(self, path: Path) -> IngestResult:
        """Chunk, embed, and store a single file (markdown or PDF)."""
        if not path.exists():
            return IngestResult(chunks_added=0, status="not_found")

        mtime = path.stat().st_mtime
        file_path = str(path)

        # Dedup: skip if mtime unchanged
        stored_mtime = self.store.get_mtime(file_path)
        if stored_mtime is not None and abs(stored_mtime - mtime) < 0.01:
            return IngestResult(chunks_added=0, status="ok")

        suffix = path.suffix.lower()
        if suffix == ".md":
            chunks = self._md_chunker.chunk(
                content=path.read_text(encoding="utf-8"),
                file_path=file_path,
                mtime=mtime,
            )
        elif suffix == ".pdf":
            from obsidian_search.ingestion.chunker_pdf import PDFChunker

            pdf_chunker = PDFChunker(
                max_tokens=self.settings.chunk_max_tokens,
                min_tokens=self.settings.chunk_min_tokens,
                overlap_tokens=self.settings.chunk_overlap_tokens,
            )
            chunks = pdf_chunker.chunk(path, mtime)
        else:
            return IngestResult(chunks_added=0, status="unsupported")

        if not chunks:
            return IngestResult(chunks_added=0, status="ok")

        # Remove stale chunks for this file before upserting
        removed = self.store.delete_by_file(file_path)

        # Embed in batches
        texts = [c.content for c in chunks]
        embeddings = self.embedder.encode_documents(texts)

        self.store.upsert_chunks(chunks, embeddings)
        return IngestResult(chunks_added=len(chunks), chunks_removed=removed, status="ok")

    def index_url(self, url: str, tags: list[str] | None = None) -> IngestResult:
        """Fetch, extract, chunk, embed, and store content from a URL."""
        from obsidian_search.ingestion.chunker_web import WebChunker

        web_chunker = WebChunker(
            max_tokens=self.settings.chunk_max_tokens,
            min_tokens=self.settings.chunk_min_tokens,
            overlap_tokens=self.settings.chunk_overlap_tokens,
        )
        chunks = web_chunker.chunk(url, tags=tags)
        if not chunks:
            return IngestResult(chunks_added=0, status="failed")

        removed = self.store.delete_by_file(url)
        embeddings = self.embedder.encode_documents([c.content for c in chunks])
        self.store.upsert_chunks(chunks, embeddings)
        return IngestResult(chunks_added=len(chunks), chunks_removed=removed, status="ok")
