"""Unit tests for MCP server tools via FastMCP async API."""

from __future__ import annotations

import json
import unittest.mock as mock
from pathlib import Path

import numpy as np
import pytest
from obsidian_search.config import Settings
from obsidian_search.embedding.embedder import Embedder
from obsidian_search.models import Chunk, ChunkId, IngestResult, SourceType
from obsidian_search.store.vector_store import VectorStore

DIMS = 8


def _fake_encode(texts: list[str]) -> np.ndarray:
    vecs = np.random.rand(len(texts), DIMS).astype(np.float32)
    return vecs / np.linalg.norm(vecs, axis=1, keepdims=True)


def _setup(tmp_path: Path) -> tuple[object, VectorStore]:
    from obsidian_search.mcp.server import _build_mcp_server

    settings = Settings(vault_path=str(tmp_path))
    store = VectorStore(tmp_path / "test.db")
    store.initialize(dims=DIMS)
    embedder = Embedder.__new__(Embedder)
    embedder.encode = _fake_encode  # type: ignore[method-assign]
    embedder.dims = DIMS
    mcp = _build_mcp_server(settings=settings, store=store, embedder=embedder)
    return mcp, store


def _insert_chunk(
    store: VectorStore,
    content: str = "Test content here.",
    file_path: str = "a.md",
    tags: list[str] | None = None,
) -> Chunk:
    chunk = Chunk(
        id=ChunkId.generate(file_path, 0),
        source_type=SourceType.MARKDOWN,
        file_path=file_path,
        content=content,
        mtime=1_700_000_000.0,
        chunk_index=0,
        metadata={"tags": tags or ["physics"]},
    )
    store.upsert_chunks([chunk], _fake_encode([content]))
    return chunk


async def _call(mcp: object, tool: str, **kwargs: object) -> object:
    """Call a FastMCP tool and return the unwrapped result."""
    result = await mcp.call_tool(tool, kwargs)  # type: ignore[attr-defined]
    sc = result.structured_content
    if sc is not None:
        # FastMCP wraps list returns in {"result": [...]}
        if isinstance(sc, dict) and list(sc.keys()) == ["result"]:
            return sc["result"]
        return sc
    text = result.content[0].text
    return json.loads(text)


class TestMcpSearchNotes:
    @pytest.mark.asyncio
    async def test_empty_store_returns_empty(self, tmp_path: Path) -> None:
        mcp, store = _setup(tmp_path)
        result = await _call(mcp, "search_notes", query="quantum computing", top_k=5)
        assert result == []
        store.close()

    @pytest.mark.asyncio
    async def test_returns_results_for_matching_query(self, tmp_path: Path) -> None:
        mcp, store = _setup(tmp_path)
        _insert_chunk(store, "Test content here about quantum physics.")
        result = await _call(mcp, "search_notes", query="quantum", top_k=5)
        assert isinstance(result, list)
        assert len(result) >= 1
        assert "score" in result[0]
        store.close()

    @pytest.mark.asyncio
    async def test_source_type_filter(self, tmp_path: Path) -> None:
        mcp, store = _setup(tmp_path)
        _insert_chunk(store)
        result = await _call(mcp, "search_notes", query="test", top_k=5, source_types=["web"])
        assert result == []
        store.close()

    @pytest.mark.asyncio
    async def test_tag_filter(self, tmp_path: Path) -> None:
        mcp, store = _setup(tmp_path)
        _insert_chunk(store)
        result = await _call(mcp, "search_notes", query="test", top_k=5, tags=["cooking"])
        assert result == []
        store.close()


class TestMcpGetNoteContent:
    @pytest.mark.asyncio
    async def test_existing_file_returned(self, tmp_path: Path) -> None:
        note = tmp_path / "note.md"
        note.write_text("# Hello\n\nWorld.")
        mcp, store = _setup(tmp_path)
        result = await mcp.call_tool("get_note_content", {"file_path": str(note)})  # type: ignore[attr-defined]
        text = result.content[0].text
        assert "Hello" in text
        store.close()

    @pytest.mark.asyncio
    async def test_relative_path_resolved_against_vault(self, tmp_path: Path) -> None:
        note = tmp_path / "note.md"
        note.write_text("# Relative")
        mcp, store = _setup(tmp_path)
        result = await mcp.call_tool("get_note_content", {"file_path": "note.md"})  # type: ignore[attr-defined]
        text = result.content[0].text
        assert "Relative" in text
        store.close()

    @pytest.mark.asyncio
    async def test_nonexistent_file_returns_error_string(self, tmp_path: Path) -> None:
        mcp, store = _setup(tmp_path)
        result = await mcp.call_tool("get_note_content", {"file_path": "/nonexistent/ghost.md"})  # type: ignore[attr-defined]
        text = result.content[0].text
        assert "Error" in text
        store.close()


class TestMcpIndexUrl:
    @pytest.mark.asyncio
    async def test_successful_url_index(self, tmp_path: Path) -> None:
        mcp, store = _setup(tmp_path)
        with mock.patch(
            "obsidian_search.ingestion.chunker_web.WebChunker.chunk",
            return_value=[
                Chunk(
                    id=ChunkId.generate("https://example.com", 0),
                    source_type=SourceType.WEB,
                    file_path="https://example.com",
                    url="https://example.com",
                    content="Web content here with enough words.",
                    mtime=1_700_000_000.0,
                    chunk_index=0,
                    metadata={"tags": []},
                )
            ],
        ):
            result = await _call(mcp, "index_url", url="https://example.com")
        assert result["chunks_added"] == 1  # type: ignore[index]
        assert result["status"] == "ok"  # type: ignore[index]
        store.close()

    @pytest.mark.asyncio
    async def test_failed_url_returns_failed_status(self, tmp_path: Path) -> None:
        mcp, store = _setup(tmp_path)
        with mock.patch(
            "obsidian_search.ingestion.chunker_web.WebChunker.chunk",
            return_value=[],
        ):
            result = await _call(mcp, "index_url", url="https://unreachable.invalid")
        assert result["status"] == "failed"  # type: ignore[index]
        store.close()


class TestMcpIndexPdf:
    @pytest.mark.asyncio
    async def test_pdf_index_via_pipeline(self, tmp_path: Path) -> None:
        pdf = tmp_path / "doc.pdf"
        pdf.write_bytes(b"fake")
        mcp, store = _setup(tmp_path)
        with mock.patch(
            "obsidian_search.ingestion.pipeline.IndexingPipeline.index_file",
            return_value=IngestResult(chunks_added=4, status="ok"),
        ):
            result = await _call(mcp, "index_pdf", file_path=str(pdf))
        assert result["chunks_added"] == 4  # type: ignore[index]
        store.close()


class TestMcpGetIndexStatus:
    @pytest.mark.asyncio
    async def test_returns_stats_dict(self, tmp_path: Path) -> None:
        mcp, store = _setup(tmp_path)
        result = await _call(mcp, "get_index_status")
        assert "total_chunks" in result  # type: ignore[operator]
        assert "total_documents" in result  # type: ignore[operator]
        store.close()

    @pytest.mark.asyncio
    async def test_stats_after_insert(self, tmp_path: Path) -> None:
        mcp, store = _setup(tmp_path)
        _insert_chunk(store)
        result = await _call(mcp, "get_index_status")
        assert result["total_chunks"] == 1  # type: ignore[index]
        store.close()


class TestMcpListIndexedFiles:
    @pytest.mark.asyncio
    async def test_empty_store_returns_empty(self, tmp_path: Path) -> None:
        mcp, store = _setup(tmp_path)
        result = await _call(mcp, "list_indexed_files")
        assert result == []
        store.close()

    @pytest.mark.asyncio
    async def test_returns_indexed_files(self, tmp_path: Path) -> None:
        mcp, store = _setup(tmp_path)
        _insert_chunk(store)
        result = await _call(mcp, "list_indexed_files")
        assert len(result) == 1  # type: ignore[arg-type]
        assert result[0]["file_path"] == "a.md"  # type: ignore[index]
        assert result[0]["chunk_count"] == 1  # type: ignore[index]
        store.close()

    @pytest.mark.asyncio
    async def test_source_type_filter(self, tmp_path: Path) -> None:
        mcp, store = _setup(tmp_path)
        _insert_chunk(store)
        result = await _call(mcp, "list_indexed_files", source_type="web")
        assert result == []
        store.close()


class TestMcpRemoveFromIndex:
    @pytest.mark.asyncio
    async def test_removes_existing_document(self, tmp_path: Path) -> None:
        mcp, store = _setup(tmp_path)
        _insert_chunk(store)
        result = await _call(mcp, "remove_from_index", file_path="a.md")
        assert result["chunks_removed"] == 1  # type: ignore[index]
        store.close()

    @pytest.mark.asyncio
    async def test_remove_nonexistent_returns_zero(self, tmp_path: Path) -> None:
        mcp, store = _setup(tmp_path)
        result = await _call(mcp, "remove_from_index", file_path="ghost.md")
        assert result["chunks_removed"] == 0  # type: ignore[index]
        store.close()
