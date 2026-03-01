"""Unit tests for WebChunker."""

from __future__ import annotations

import sys
import unittest.mock as mock

import pytest
from obsidian_search.ingestion.chunker_web import WebChunker
from obsidian_search.models import SourceType


class TestWebChunkerImportErrors:
    def test_raises_import_error_when_httpx_missing(self) -> None:
        with mock.patch.dict(sys.modules, {"httpx": None}):
            chunker = WebChunker(min_tokens=1)
            with pytest.raises(ImportError, match="httpx"):
                chunker.chunk("https://example.com")

    def test_raises_import_error_when_trafilatura_missing(self) -> None:
        httpx_mock = mock.MagicMock()
        resp = mock.MagicMock()
        resp.ok = True
        resp.text = "<html><body>Hello</body></html>"
        httpx_mock.get.return_value = resp
        with mock.patch.dict(sys.modules, {"httpx": httpx_mock, "trafilatura": None}):
            chunker = WebChunker(min_tokens=1)
            with pytest.raises(ImportError, match="trafilatura"):
                chunker.chunk("https://example.com")


class TestWebChunkerFetchFailure:
    def test_http_error_returns_empty(self) -> None:
        """covers the except around httpx.get()."""
        httpx_mock = mock.MagicMock()
        httpx_mock.get.side_effect = RuntimeError("connection refused")
        trafilatura_mock = mock.MagicMock()
        with mock.patch.dict(sys.modules, {"httpx": httpx_mock, "trafilatura": trafilatura_mock}):
            chunker = WebChunker(min_tokens=1)
            result = chunker.chunk("https://example.com")
        assert result == []

    def test_bad_status_returns_empty(self) -> None:
        """covers raise_for_status raising."""
        httpx_mock = mock.MagicMock()
        resp = mock.MagicMock()
        resp.raise_for_status.side_effect = RuntimeError("404")
        httpx_mock.get.return_value = resp
        trafilatura_mock = mock.MagicMock()
        with mock.patch.dict(sys.modules, {"httpx": httpx_mock, "trafilatura": trafilatura_mock}):
            chunker = WebChunker(min_tokens=1)
            result = chunker.chunk("https://example.com")
        assert result == []

    def test_empty_extraction_returns_empty(self) -> None:
        """covers `not extracted or not extracted.strip()`."""
        httpx_mock = mock.MagicMock()
        resp = mock.MagicMock()
        resp.text = "<html></html>"
        httpx_mock.get.return_value = resp
        trafilatura_mock = mock.MagicMock()
        trafilatura_mock.extract.return_value = None
        with mock.patch.dict(sys.modules, {"httpx": httpx_mock, "trafilatura": trafilatura_mock}):
            chunker = WebChunker(min_tokens=1)
            result = chunker.chunk("https://example.com")
        assert result == []

    def test_whitespace_only_extraction_returns_empty(self) -> None:
        httpx_mock = mock.MagicMock()
        resp = mock.MagicMock()
        resp.text = "<html></html>"
        httpx_mock.get.return_value = resp
        trafilatura_mock = mock.MagicMock()
        trafilatura_mock.extract.return_value = "   "
        with mock.patch.dict(sys.modules, {"httpx": httpx_mock, "trafilatura": trafilatura_mock}):
            chunker = WebChunker(min_tokens=1)
            result = chunker.chunk("https://example.com")
        assert result == []


class TestWebChunkerSuccess:
    def _make_mocks(self, body: str) -> tuple[object, object]:
        httpx_mock = mock.MagicMock()
        resp = mock.MagicMock()
        resp.text = f"<html><body>{body}</body></html>"
        httpx_mock.get.return_value = resp

        trafilatura_mock = mock.MagicMock()
        trafilatura_mock.extract.return_value = body
        return httpx_mock, trafilatura_mock

    def test_valid_page_produces_web_chunks(self) -> None:
        body = "# Article\n\nThis is a test article with meaningful content."
        httpx_mock, trafilatura_mock = self._make_mocks(body)
        with mock.patch.dict(sys.modules, {"httpx": httpx_mock, "trafilatura": trafilatura_mock}):
            chunker = WebChunker(min_tokens=1)
            result = chunker.chunk("https://example.com/article")
        assert len(result) >= 1
        assert all(c.source_type == SourceType.WEB for c in result)

    def test_url_stored_in_chunks(self) -> None:
        url = "https://example.com/page"
        body = "# Page\n\nSome content here."
        httpx_mock, trafilatura_mock = self._make_mocks(body)
        with mock.patch.dict(sys.modules, {"httpx": httpx_mock, "trafilatura": trafilatura_mock}):
            chunker = WebChunker(min_tokens=1)
            result = chunker.chunk(url)
        assert all(c.url == url for c in result)
        assert all(c.file_path == url for c in result)

    def test_tags_stored_in_metadata(self) -> None:
        body = "# Page\n\nSome content here."
        httpx_mock, trafilatura_mock = self._make_mocks(body)
        with mock.patch.dict(sys.modules, {"httpx": httpx_mock, "trafilatura": trafilatura_mock}):
            chunker = WebChunker(min_tokens=1)
            result = chunker.chunk("https://example.com", tags=["reference", "ai"])
        assert all("reference" in c.metadata.get("tags", []) for c in result)

    def test_sequential_chunk_indices(self) -> None:
        body = "# A\n\nContent A.\n\n# B\n\nContent B."
        httpx_mock, trafilatura_mock = self._make_mocks(body)
        with mock.patch.dict(sys.modules, {"httpx": httpx_mock, "trafilatura": trafilatura_mock}):
            chunker = WebChunker(min_tokens=1)
            result = chunker.chunk("https://example.com")
        assert [c.chunk_index for c in result] == list(range(len(result)))
