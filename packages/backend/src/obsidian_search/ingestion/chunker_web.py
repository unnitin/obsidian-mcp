"""Web chunker — fetches a URL, extracts readable text, and chunks it."""

from __future__ import annotations

import time
from typing import Any

from obsidian_search.ingestion.chunker_markdown import MarkdownChunker
from obsidian_search.models import Chunk, ChunkId, SourceType


class WebChunker:
    """Fetch a URL, extract readable content via trafilatura, then chunk.

    Uses httpx for the HTTP request and trafilatura for boilerplate-free
    content extraction (strips navbars, ads, footers).  The extracted text
    is treated as Markdown so it reuses MarkdownChunker for consistent
    sentence-boundary splitting.
    """

    def __init__(
        self,
        max_tokens: int = 512,
        min_tokens: int = 64,
        overlap_tokens: int = 50,
        timeout: float = 30.0,
    ) -> None:
        self._md_chunker = MarkdownChunker(
            max_tokens=max_tokens,
            min_tokens=min_tokens,
            overlap_tokens=overlap_tokens,
        )
        self._timeout = timeout

    def chunk(self, url: str, tags: list[str] | None = None) -> list[Chunk]:
        """Fetch *url*, extract text, and return chunks.

        Returns an empty list if fetching or extraction fails.
        """
        try:
            import httpx
        except ImportError as exc:
            raise ImportError("httpx is required for web chunking: pip install httpx") from exc

        try:
            import trafilatura
        except ImportError as exc:
            raise ImportError(
                "trafilatura is required for web chunking: pip install trafilatura"
            ) from exc

        # Fetch page
        try:
            response = httpx.get(
                url,
                follow_redirects=True,
                timeout=self._timeout,
                headers={"User-Agent": "obsidian-search/0.1 (+https://github.com/obsidian-search)"},
            )
            response.raise_for_status()
            html = response.text
        except Exception:  # noqa: BLE001
            return []

        # Extract readable content
        extracted = trafilatura.extract(
            html,
            include_tables=True,
            include_links=False,
            output_format="markdown",
        )
        if not extracted or not extracted.strip():
            return []

        mtime = time.time()
        meta: dict[str, Any] = {"tags": tags or [], "url": url}

        raw_chunks = self._md_chunker.chunk(
            content=extracted,
            file_path=url,
            mtime=mtime,
        )

        chunks: list[Chunk] = []
        for idx, c in enumerate(raw_chunks):
            chunk_meta: dict[str, Any] = {**c.metadata, **meta}
            chunks.append(
                Chunk(
                    id=ChunkId.generate(url, idx),
                    source_type=SourceType.WEB,
                    file_path=url,
                    url=url,
                    header_path=c.header_path,
                    content=c.content,
                    mtime=mtime,
                    chunk_index=idx,
                    metadata=chunk_meta,
                )
            )
        return chunks
