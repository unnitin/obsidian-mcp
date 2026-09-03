"""Vault write operations — create and append notes, then index them.

Writes are additive only: ``create_note`` refuses to touch an existing file and
``append_to_note`` refuses to create one.  Nothing here can overwrite or delete
a note, so a mistaken tool call cannot destroy existing writing.

Every write is confined to the vault via ``Settings.resolve_in_vault`` and
indexed through the same ``IndexingPipeline`` the watcher uses, so search
reflects the new content immediately rather than after the watcher's debounce.
"""

from __future__ import annotations

import logging
from pathlib import Path

from obsidian_search.config import Settings
from obsidian_search.ingestion.pipeline import IndexingPipeline
from obsidian_search.models import WriteResult

logger = logging.getLogger(__name__)


class VaultWriteError(RuntimeError):
    """Raised when a write cannot be performed as requested."""


class VaultWriter:
    """Create and append markdown notes inside the vault."""

    def __init__(self, settings: Settings, pipeline: IndexingPipeline) -> None:
        self.settings = settings
        self.pipeline = pipeline

    # ── Internal ──────────────────────────────────────────────────────────────

    def _target(self, file_path: str) -> Path:
        """Resolve *file_path* to a writable markdown path inside the vault.

        Raises:
            VaultPathError: if the path escapes the vault.
            VaultWriteError: if the path is not markdown or sits in a system folder.
        """
        path = self.settings.resolve_in_vault(file_path)

        if path.suffix == "":
            path = path.with_suffix(".md")
        if path.suffix.lower() != ".md":
            raise VaultWriteError(
                f"Only markdown notes can be written: {file_path!r} has suffix {path.suffix!r}"
            )
        if self.settings.is_ignored_path(path):
            raise VaultWriteError(f"Path is in an excluded or system folder: {file_path!r}")
        return path

    def _index(self, path: Path) -> int:
        """Index the note just written; never let an indexing failure lose the write."""
        try:
            return self.pipeline.index_file(path).chunks_added
        except Exception:  # noqa: BLE001
            # The file is on disk and the watcher will retry — report 0 indexed.
            logger.exception("Indexing failed after write: %s", path)
            return 0

    # ── Public API ────────────────────────────────────────────────────────────

    def create_note(self, file_path: str, content: str) -> WriteResult:
        """Create a new note. Fails if the file already exists."""
        path = self._target(file_path)
        if path.exists():
            raise VaultWriteError(
                f"Note already exists: {str(path)!r}. Use append_to_note to add to it."
            )

        path.parent.mkdir(parents=True, exist_ok=True)
        body = content if content.endswith("\n") else content + "\n"
        # exclusive create — loses to a file that appeared since the check above
        with path.open("x", encoding="utf-8") as fh:
            fh.write(body)

        return WriteResult(
            file_path=str(path),
            action="created",
            bytes_written=len(body.encode("utf-8")),
            chunks_indexed=self._index(path),
        )

    def append_to_note(self, file_path: str, content: str) -> WriteResult:
        """Append to an existing note. Fails if the file does not exist."""
        path = self._target(file_path)
        if not path.exists():
            raise VaultWriteError(
                f"Note not found: {str(path)!r}. Use create_note to make a new one."
            )
        if not path.is_file():
            raise VaultWriteError(f"Not a file: {str(path)!r}")

        existing = path.read_text(encoding="utf-8")
        # Separate appended content from the existing body with a blank line.
        separator = (
            ""
            if existing == "" or existing.endswith("\n\n")
            else "\n"
            if existing.endswith("\n")
            else "\n\n"
        )
        body = content if content.endswith("\n") else content + "\n"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(separator + body)

        return WriteResult(
            file_path=str(path),
            action="appended",
            bytes_written=len((separator + body).encode("utf-8")),
            chunks_indexed=self._index(path),
        )
