"""Unit tests for PDFChunker."""

from __future__ import annotations

import sys
import unittest.mock as mock
from pathlib import Path

import pytest
from obsidian_search.ingestion.chunker_pdf import PDFChunker
from obsidian_search.models import SourceType

MTIME = 1_700_000_000.0


class TestPDFChunkerImportError:
    def test_raises_import_error_when_pymupdf4llm_missing(self, tmp_path: Path) -> None:
        with mock.patch.dict(sys.modules, {"pymupdf4llm": None}):
            chunker = PDFChunker(min_tokens=1)
            with pytest.raises(ImportError, match="pymupdf4llm"):
                chunker.chunk(tmp_path / "file.pdf", MTIME)


class TestPDFChunkerExtraction:
    def test_empty_extraction_returns_empty_list(self, tmp_path: Path) -> None:
        """covers the `not md_text.strip()` early return."""
        pdf = tmp_path / "empty.pdf"
        pdf.write_bytes(b"")
        pymupdf_mock = mock.MagicMock()
        pymupdf_mock.to_markdown.return_value = "   "  # whitespace only
        with mock.patch.dict(sys.modules, {"pymupdf4llm": pymupdf_mock}):
            chunker = PDFChunker(min_tokens=1)
            result = chunker.chunk(pdf, MTIME)
        assert result == []

    def test_extraction_exception_returns_empty_list(self, tmp_path: Path) -> None:
        """covers the except branch around to_markdown()."""
        pdf = tmp_path / "bad.pdf"
        pdf.write_bytes(b"not a real pdf")
        pymupdf_mock = mock.MagicMock()
        pymupdf_mock.to_markdown.side_effect = RuntimeError("corrupt")
        with mock.patch.dict(sys.modules, {"pymupdf4llm": pymupdf_mock}):
            chunker = PDFChunker(min_tokens=1)
            result = chunker.chunk(pdf, MTIME)
        assert result == []

    def test_valid_markdown_produces_chunks(self, tmp_path: Path) -> None:
        pdf = tmp_path / "paper.pdf"
        pdf.write_bytes(b"fake pdf")
        md = "# Introduction\n\nThis paper discusses a very important topic in detail."
        pymupdf_mock = mock.MagicMock()
        pymupdf_mock.to_markdown.return_value = md
        with mock.patch.dict(sys.modules, {"pymupdf4llm": pymupdf_mock}):
            chunker = PDFChunker(min_tokens=1)
            result = chunker.chunk(pdf, MTIME)
        assert len(result) >= 1
        assert all(c.source_type == SourceType.PDF for c in result)

    def test_chunk_ids_are_sequential(self, tmp_path: Path) -> None:
        pdf = tmp_path / "paper.pdf"
        pdf.write_bytes(b"fake")
        md = "# A\n\nContent A.\n\n# B\n\nContent B."
        pymupdf_mock = mock.MagicMock()
        pymupdf_mock.to_markdown.return_value = md
        with mock.patch.dict(sys.modules, {"pymupdf4llm": pymupdf_mock}):
            chunker = PDFChunker(min_tokens=1)
            result = chunker.chunk(pdf, MTIME)
        assert [c.chunk_index for c in result] == list(range(len(result)))

    def test_file_path_stored_in_chunks(self, tmp_path: Path) -> None:
        pdf = tmp_path / "paper.pdf"
        pdf.write_bytes(b"fake")
        pymupdf_mock = mock.MagicMock()
        pymupdf_mock.to_markdown.return_value = "# Title\n\nSome content here."
        with mock.patch.dict(sys.modules, {"pymupdf4llm": pymupdf_mock}):
            chunker = PDFChunker(min_tokens=1)
            result = chunker.chunk(pdf, MTIME)
        assert all(c.file_path == str(pdf) for c in result)

    def test_mtime_stored_in_chunks(self, tmp_path: Path) -> None:
        pdf = tmp_path / "paper.pdf"
        pdf.write_bytes(b"fake")
        pymupdf_mock = mock.MagicMock()
        pymupdf_mock.to_markdown.return_value = "# Title\n\nSome content here."
        with mock.patch.dict(sys.modules, {"pymupdf4llm": pymupdf_mock}):
            chunker = PDFChunker(min_tokens=1)
            result = chunker.chunk(pdf, MTIME)
        assert all(c.mtime == MTIME for c in result)
