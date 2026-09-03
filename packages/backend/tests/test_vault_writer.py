"""Tests for VaultWriter — create and append note operations."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from obsidian_search.config import Settings, VaultPathError
from obsidian_search.embedding.embedder import Embedder
from obsidian_search.ingestion.pipeline import IndexingPipeline
from obsidian_search.store.vector_store import VectorStore
from obsidian_search.vault.writer import VaultWriteError, VaultWriter

DIMS = 8


def _fake_encode(texts: list[str]) -> np.ndarray:
    vecs = np.random.rand(len(texts), DIMS).astype(np.float32)
    return vecs / np.linalg.norm(vecs, axis=1, keepdims=True)


@pytest.fixture()
def writer(tmp_path: Path) -> VaultWriter:
    settings = Settings(vault_path=str(tmp_path))
    store = VectorStore(tmp_path / ".index.db")
    store.initialize(dims=DIMS)
    embedder = Embedder.__new__(Embedder)
    embedder.encode = _fake_encode  # type: ignore[method-assign]
    embedder.dims = DIMS
    pipeline = IndexingPipeline(settings=settings, store=store, embedder=embedder)
    return VaultWriter(settings=settings, pipeline=pipeline)


class TestCreateNote:
    def test_creates_file_with_content(self, writer: VaultWriter, tmp_path: Path) -> None:
        result = writer.create_note("note.md", "# Title\n\nSome body text here.")
        assert result.action == "created"
        assert (tmp_path / "note.md").read_text() == "# Title\n\nSome body text here.\n"

    def test_indexes_the_new_note(self, writer: VaultWriter) -> None:
        result = writer.create_note("note.md", "# Title\n\nSearchable body text here.")
        assert result.chunks_indexed > 0

    def test_creates_parent_directories(self, writer: VaultWriter, tmp_path: Path) -> None:
        writer.create_note("Projects/2026/plan.md", "# Plan")
        assert (tmp_path / "Projects" / "2026" / "plan.md").exists()

    def test_adds_missing_md_suffix(self, writer: VaultWriter, tmp_path: Path) -> None:
        result = writer.create_note("untitled", "# Body")
        assert result.file_path.endswith("untitled.md")
        assert (tmp_path / "untitled.md").exists()

    def test_refuses_to_overwrite_existing_note(self, writer: VaultWriter, tmp_path: Path) -> None:
        (tmp_path / "note.md").write_text("original content")
        with pytest.raises(VaultWriteError, match="already exists"):
            writer.create_note("note.md", "replacement")
        assert (tmp_path / "note.md").read_text() == "original content"

    def test_rejects_path_outside_vault(self, writer: VaultWriter) -> None:
        with pytest.raises(VaultPathError, match="outside the vault"):
            writer.create_note("/tmp/escape.md", "x")

    def test_rejects_traversal(self, writer: VaultWriter) -> None:
        with pytest.raises(VaultPathError):
            writer.create_note("../../escape.md", "x")

    def test_rejects_non_markdown_suffix(self, writer: VaultWriter) -> None:
        with pytest.raises(VaultWriteError, match="Only markdown"):
            writer.create_note("script.sh", "rm -rf /")

    def test_rejects_obsidian_system_folder(self, writer: VaultWriter) -> None:
        with pytest.raises(VaultWriteError, match="excluded or system folder"):
            writer.create_note(".obsidian/plugins/evil.md", "x")

    def test_content_gets_trailing_newline(self, writer: VaultWriter, tmp_path: Path) -> None:
        writer.create_note("a.md", "no trailing newline")
        assert (tmp_path / "a.md").read_text().endswith("\n")


class TestAppendToNote:
    def test_appends_to_existing_note(self, writer: VaultWriter, tmp_path: Path) -> None:
        (tmp_path / "note.md").write_text("# Existing\n")
        result = writer.append_to_note("note.md", "## Added section")
        assert result.action == "appended"
        assert (tmp_path / "note.md").read_text() == "# Existing\n\n## Added section\n"

    def test_preserves_existing_content(self, writer: VaultWriter, tmp_path: Path) -> None:
        (tmp_path / "note.md").write_text("original body")
        writer.append_to_note("note.md", "new line")
        assert (tmp_path / "note.md").read_text().startswith("original body")

    def test_does_not_double_blank_line(self, writer: VaultWriter, tmp_path: Path) -> None:
        (tmp_path / "note.md").write_text("# Existing\n\n")
        writer.append_to_note("note.md", "more")
        assert (tmp_path / "note.md").read_text() == "# Existing\n\nmore\n"

    def test_handles_empty_file(self, writer: VaultWriter, tmp_path: Path) -> None:
        (tmp_path / "note.md").write_text("")
        writer.append_to_note("note.md", "first content")
        assert (tmp_path / "note.md").read_text() == "first content\n"

    def test_refuses_missing_note(self, writer: VaultWriter, tmp_path: Path) -> None:
        with pytest.raises(VaultWriteError, match="not found"):
            writer.append_to_note("ghost.md", "x")
        assert not (tmp_path / "ghost.md").exists()

    def test_rejects_path_outside_vault(self, writer: VaultWriter) -> None:
        with pytest.raises(VaultPathError):
            writer.append_to_note("/etc/hosts", "x")

    def test_reindexes_after_append(self, writer: VaultWriter, tmp_path: Path) -> None:
        (tmp_path / "note.md").write_text("# Existing note with body text.\n")
        writer.create_note("other.md", "# Other note body text here.")
        result = writer.append_to_note("note.md", "Appended searchable paragraph text.")
        assert result.chunks_indexed > 0
