"""Unit tests for WebChunker."""

from __future__ import annotations

import ipaddress
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


class TestWebChunkerRedirectValidation:
    """A public URL must not be able to redirect us into the local network."""

    def _resp(
        self,
        status: int = 200,
        text: str = "<html><body>Hi</body></html>",
        **headers: str,
    ) -> mock.MagicMock:
        r = mock.MagicMock()
        r.status_code = status
        r.text = text
        r.content = text.encode()
        r.headers = headers
        return r

    def test_redirect_into_private_network_is_rejected(self) -> None:
        from obsidian_search.ingestion.url_guard import UrlNotAllowedError

        httpx_mock = mock.MagicMock()
        httpx_mock.get.return_value = self._resp(
            302, location="http://169.254.169.254/latest/meta-data/"
        )
        with (
            mock.patch.dict(sys.modules, {"httpx": httpx_mock, "trafilatura": mock.MagicMock()}),
            mock.patch(
                "obsidian_search.ingestion.url_guard._resolved_addresses",
                side_effect=[
                    [ipaddress.ip_address("93.184.216.34")],
                    [ipaddress.ip_address("169.254.169.254")],
                ],
            ),
            pytest.raises(UrlNotAllowedError, match="local network"),
        ):
            WebChunker(min_tokens=1).chunk("https://example.com/redirector")

    def test_redirect_to_public_target_is_followed(self) -> None:
        httpx_mock = mock.MagicMock()
        httpx_mock.get.side_effect = [
            self._resp(302, location="https://elsewhere.example.com/real"),
            self._resp(200),
        ]
        trafilatura_mock = mock.MagicMock()
        trafilatura_mock.extract.return_value = "# Title\n\nSome body text for the chunker."
        with mock.patch.dict(sys.modules, {"httpx": httpx_mock, "trafilatura": trafilatura_mock}):
            chunker = WebChunker(min_tokens=1)
            chunks = chunker.chunk("https://example.com/redirector")
        assert len(chunks) > 0
        assert httpx_mock.get.call_count == 2

    def test_redirect_loop_is_capped(self) -> None:
        from obsidian_search.ingestion.url_guard import MAX_REDIRECTS, UrlNotAllowedError

        httpx_mock = mock.MagicMock()
        httpx_mock.get.return_value = self._resp(302, location="https://example.com/again")
        with mock.patch.dict(sys.modules, {"httpx": httpx_mock, "trafilatura": mock.MagicMock()}):
            chunker = WebChunker(min_tokens=1)
            with pytest.raises(UrlNotAllowedError, match="redirects"):
                chunker.chunk("https://example.com/loop")
        assert httpx_mock.get.call_count == MAX_REDIRECTS + 1

    def test_redirect_without_location_is_rejected(self) -> None:
        from obsidian_search.ingestion.url_guard import UrlNotAllowedError

        httpx_mock = mock.MagicMock()
        httpx_mock.get.return_value = self._resp(302)
        with mock.patch.dict(sys.modules, {"httpx": httpx_mock, "trafilatura": mock.MagicMock()}):
            chunker = WebChunker(min_tokens=1)
            with pytest.raises(UrlNotAllowedError, match="no Location"):
                chunker.chunk("https://example.com/bad-redirect")

    def test_oversized_body_is_rejected(self) -> None:
        from obsidian_search.ingestion.url_guard import MAX_RESPONSE_BYTES, UrlNotAllowedError

        httpx_mock = mock.MagicMock()
        httpx_mock.get.return_value = self._resp(200, text="x" * (MAX_RESPONSE_BYTES + 1))
        with mock.patch.dict(sys.modules, {"httpx": httpx_mock, "trafilatura": mock.MagicMock()}):
            chunker = WebChunker(min_tokens=1)
            with pytest.raises(UrlNotAllowedError, match="over the"):
                chunker.chunk("https://example.com/huge")

    def test_private_target_rejected_before_any_request(self) -> None:
        from obsidian_search.ingestion.url_guard import UrlNotAllowedError

        httpx_mock = mock.MagicMock()
        with (
            mock.patch.dict(sys.modules, {"httpx": httpx_mock, "trafilatura": mock.MagicMock()}),
            mock.patch(
                "obsidian_search.ingestion.url_guard._resolved_addresses",
                return_value=[ipaddress.ip_address("127.0.0.1")],
            ),
            pytest.raises(UrlNotAllowedError),
        ):
            WebChunker(min_tokens=1).chunk("http://localhost:51234/status")
        httpx_mock.get.assert_not_called()
