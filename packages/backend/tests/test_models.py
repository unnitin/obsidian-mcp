"""Tests for core data models."""

from datetime import UTC, datetime

import pytest
from obsidian_search.models import (
    Chunk,
    ChunkId,
    IndexedFile,
    IndexStatus,
    IngestResult,
    SearchResult,
    SourceType,
)
from pydantic import ValidationError


class TestSourceType:
    def test_markdown_value(self) -> None:
        assert SourceType.MARKDOWN == "markdown"

    def test_pdf_value(self) -> None:
        assert SourceType.PDF == "pdf"

    def test_web_value(self) -> None:
        assert SourceType.WEB == "web"


class TestChunkId:
    def test_generate_deterministic(self) -> None:
        id1 = ChunkId.generate("Notes/hello.md", 0)
        id2 = ChunkId.generate("Notes/hello.md", 0)
        assert id1 == id2

    def test_generate_different_index(self) -> None:
        id1 = ChunkId.generate("Notes/hello.md", 0)
        id2 = ChunkId.generate("Notes/hello.md", 1)
        assert id1 != id2

    def test_generate_different_path(self) -> None:
        id1 = ChunkId.generate("Notes/a.md", 0)
        id2 = ChunkId.generate("Notes/b.md", 0)
        assert id1 != id2

    def test_generate_is_hex_string(self) -> None:
        chunk_id = ChunkId.generate("Notes/hello.md", 0)
        int(chunk_id, 16)  # raises ValueError if not hex

    def test_generate_length(self) -> None:
        chunk_id = ChunkId.generate("Notes/hello.md", 0)
        assert len(chunk_id) == 64  # sha256 hex = 64 chars


class TestChunk:
    def test_create_markdown_chunk(self) -> None:
        chunk = Chunk(
            id=ChunkId.generate("Notes/hello.md", 0),
            source_type=SourceType.MARKDOWN,
            file_path="Notes/hello.md",
            content="Quantum Computing > Qubits\n\nQubits are the basic unit...",
            mtime=1234567890.0,
            chunk_index=0,
        )
        assert chunk.source_type == SourceType.MARKDOWN
        assert chunk.file_path == "Notes/hello.md"
        assert chunk.chunk_index == 0

    def test_create_web_chunk_requires_url(self) -> None:
        chunk = Chunk(
            id=ChunkId.generate("https://example.com", 0),
            source_type=SourceType.WEB,
            file_path="https://example.com",
            url="https://example.com",
            content="Some web content",
            mtime=1234567890.0,
            chunk_index=0,
        )
        assert chunk.url == "https://example.com"

    def test_header_path_optional(self) -> None:
        chunk = Chunk(
            id=ChunkId.generate("Notes/hello.md", 0),
            source_type=SourceType.MARKDOWN,
            file_path="Notes/hello.md",
            content="content",
            mtime=1234567890.0,
            chunk_index=0,
        )
        assert chunk.header_path is None

    def test_metadata_defaults_empty(self) -> None:
        chunk = Chunk(
            id=ChunkId.generate("Notes/hello.md", 0),
            source_type=SourceType.MARKDOWN,
            file_path="Notes/hello.md",
            content="content",
            mtime=1234567890.0,
            chunk_index=0,
        )
        assert chunk.metadata == {}

    def test_tags_in_metadata(self) -> None:
        chunk = Chunk(
            id=ChunkId.generate("Notes/hello.md", 0),
            source_type=SourceType.MARKDOWN,
            file_path="Notes/hello.md",
            content="content",
            mtime=1234567890.0,
            chunk_index=0,
            metadata={"tags": ["physics", "quantum"]},
        )
        assert chunk.metadata["tags"] == ["physics", "quantum"]

    def test_content_cannot_be_empty(self) -> None:
        with pytest.raises(ValidationError):
            Chunk(
                id=ChunkId.generate("Notes/hello.md", 0),
                source_type=SourceType.MARKDOWN,
                file_path="Notes/hello.md",
                content="",
                mtime=1234567890.0,
                chunk_index=0,
            )

    def test_chunk_index_non_negative(self) -> None:
        with pytest.raises(ValidationError):
            Chunk(
                id=ChunkId.generate("Notes/hello.md", 0),
                source_type=SourceType.MARKDOWN,
                file_path="Notes/hello.md",
                content="content",
                mtime=1234567890.0,
                chunk_index=-1,
            )


class TestSearchResult:
    def test_create_search_result(self) -> None:
        result = SearchResult(
            chunk_id=ChunkId.generate("Notes/hello.md", 0),
            content="Some matched content",
            score=0.92,
            source_type=SourceType.MARKDOWN,
            file_path="Notes/hello.md",
        )
        assert result.score == pytest.approx(0.92)

    def test_score_between_0_and_1(self) -> None:
        with pytest.raises(ValidationError):
            SearchResult(
                chunk_id="abc",
                content="content",
                score=1.5,
                source_type=SourceType.MARKDOWN,
                file_path="Notes/hello.md",
            )

    def test_score_cannot_be_negative(self) -> None:
        with pytest.raises(ValidationError):
            SearchResult(
                chunk_id="abc",
                content="content",
                score=-0.1,
                source_type=SourceType.MARKDOWN,
                file_path="Notes/hello.md",
            )

    def test_header_path_optional(self) -> None:
        result = SearchResult(
            chunk_id="abc",
            content="content",
            score=0.5,
            source_type=SourceType.MARKDOWN,
            file_path="Notes/hello.md",
        )
        assert result.header_path is None

    def test_url_optional(self) -> None:
        result = SearchResult(
            chunk_id="abc",
            content="content",
            score=0.5,
            source_type=SourceType.MARKDOWN,
            file_path="Notes/hello.md",
        )
        assert result.url is None


class TestIndexStatus:
    def test_create_status(self) -> None:
        now = datetime.now(UTC)
        status = IndexStatus(
            total_chunks=1000,
            total_documents=50,
            last_indexed_at=now,
            index_size_bytes=204800,
            is_watching=True,
        )
        assert status.total_chunks == 1000
        assert status.total_documents == 50
        assert status.is_watching is True

    def test_never_indexed_has_none_timestamp(self) -> None:
        status = IndexStatus(
            total_chunks=0,
            total_documents=0,
            last_indexed_at=None,
            index_size_bytes=0,
            is_watching=False,
        )
        assert status.last_indexed_at is None


class TestIndexedFile:
    def test_create_indexed_file(self) -> None:
        now = datetime.now(UTC)
        f = IndexedFile(
            file_path="Notes/hello.md",
            source_type=SourceType.MARKDOWN,
            chunk_count=5,
            last_indexed=now,
        )
        assert f.chunk_count == 5


class TestIngestResult:
    def test_create_ingest_result(self) -> None:
        result = IngestResult(chunks_added=12, chunks_removed=3, status="ok")
        assert result.chunks_added == 12
        assert result.status == "ok"

    def test_default_chunks_removed(self) -> None:
        result = IngestResult(chunks_added=5, status="ok")
        assert result.chunks_removed == 0
