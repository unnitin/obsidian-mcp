"""Web chunker — fetches a URL, extracts readable text, and chunks it."""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import urljoin

from obsidian_search.ingestion.chunker_markdown import MarkdownChunker
from obsidian_search.ingestion.url_guard import (
    MAX_REDIRECTS,
    MAX_RESPONSE_BYTES,
    UrlNotAllowedError,
    check_url,
)
from obsidian_search.models import Chunk, ChunkId, SourceType

#: Status codes we follow by hand, re-validating the target each time.
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


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
        allow_private_urls: bool = False,
    ) -> None:
        self._md_chunker = MarkdownChunker(
            max_tokens=max_tokens,
            min_tokens=min_tokens,
            overlap_tokens=overlap_tokens,
        )
        self._timeout = timeout
        self._allow_private_urls = allow_private_urls

    def _fetch(self, url: str, httpx: Any) -> str:  # noqa: ANN401
        """Fetch *url*, validating every redirect hop and capping the body size.

        Redirects are followed by hand: httpx's follow_redirects would send us
        to whatever Location says, and a public URL may redirect into the local
        network.
        """
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            check_url(current, allow_private=self._allow_private_urls)
            response = httpx.get(
                current,
                follow_redirects=False,
                timeout=self._timeout,
                headers={"User-Agent": "obsidian-search/0.1 (+https://github.com/obsidian-search)"},
            )
            if response.status_code in _REDIRECT_STATUSES:
                location = response.headers.get("location")
                if not location:
                    raise UrlNotAllowedError(f"Redirect from {current!r} with no Location header")
                current = str(urljoin(current, location))
                continue

            response.raise_for_status()
            body: bytes = response.content
            if len(body) > MAX_RESPONSE_BYTES:
                raise UrlNotAllowedError(
                    f"Response from {current!r} is {len(body)} bytes, over the "
                    f"{MAX_RESPONSE_BYTES}-byte limit"
                )
            text: str = response.text
            return text

        raise UrlNotAllowedError(f"More than {MAX_REDIRECTS} redirects starting at {url!r}")

    def chunk(self, url: str, tags: list[str] | None = None) -> list[Chunk]:
        """Fetch *url*, extract text, and return chunks.

        Returns an empty list if fetching or extraction fails.

        Raises:
            UrlNotAllowedError: if the URL, or any redirect hop, targets the
                local network or is otherwise not fetchable by policy.
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

        # Fetch page. A blocked URL is a caller error worth reporting, so it
        # propagates; transport failures stay a silent empty result as before.
        try:
            html = self._fetch(url, httpx)
        except UrlNotAllowedError:
            raise
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
