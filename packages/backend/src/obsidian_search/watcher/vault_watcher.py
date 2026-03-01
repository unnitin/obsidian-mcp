"""FSEvents-based vault watcher with debounce and startup reconciliation."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from obsidian_search.config import Settings
from obsidian_search.ingestion.pipeline import IndexingPipeline

logger = logging.getLogger(__name__)


class VaultWatcher:
    """Watch an Obsidian vault directory and reindex changed files.

    Uses ``watchdog`` with the ``FSEventsObserver`` on macOS (native
    kqueue/FSEvents — zero polling) and falls back to ``Observer`` (inotify
    on Linux, ReadDirectoryChanges on Windows) on other platforms.

    A per-file debounce timer coalesces rapid successive events (e.g. multiple
    ``modify`` events from Obsidian's autosave) into a single reindex call.

    Startup reconciliation: on ``start()`` we walk the vault and reindex any
    file whose mtime is newer than what is stored in the DB.  This catches
    changes synced from other devices via iCloud while the backend was offline.
    """

    def __init__(self, settings: Settings, pipeline: IndexingPipeline) -> None:
        self.settings = settings
        self.pipeline = pipeline
        self._observer: Any = None  # noqa: ANN401
        self._timers: dict[str, threading.Timer] = {}
        self._lock = threading.Lock()
        self._running = False

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self) -> None:
        """Start the watcher and run startup reconciliation."""
        if self._running:
            return

        self._reconcile()
        self._start_observer()
        self._running = True
        logger.info("VaultWatcher started: %s", self.settings.vault_path)

    def stop(self) -> None:
        """Stop the watcher and cancel pending debounce timers."""
        if not self._running:
            return

        self._running = False

        # Cancel pending timers
        with self._lock:
            for timer in self._timers.values():
                timer.cancel()
            self._timers.clear()

        if self._observer is not None:
            try:
                self._observer.stop()
                self._observer.join(timeout=5)
            except Exception:  # noqa: BLE001
                pass
            self._observer = None

        logger.info("VaultWatcher stopped")

    # ── Internal ──────────────────────────────────────────────────────────────

    def _reconcile(self) -> None:
        """Reindex any file that has changed since the last indexing session."""
        vault = self.settings.vault_path
        md_files = [p for p in vault.rglob("*.md") if not self.settings.is_ignored_path(p)]
        pdf_files = [p for p in vault.rglob("*.pdf") if not self.settings.is_ignored_path(p)]
        for path in md_files + pdf_files:
            try:
                self.pipeline.index_file(path)
            except Exception:  # noqa: BLE001
                logger.exception("Reconciliation error for %s", path)

    def _start_observer(self) -> None:
        import sys

        from watchdog.events import FileSystemEventHandler

        if sys.platform == "darwin":
            try:
                from watchdog.observers.fsevents import FSEventsObserver

                ObserverClass: Any = FSEventsObserver  # noqa: ANN401
            except ImportError:
                from watchdog.observers import Observer

                ObserverClass = Observer
        else:
            from watchdog.observers import Observer

            ObserverClass = Observer

        watcher = self

        class _Handler(FileSystemEventHandler):
            def on_modified(self, event: Any) -> None:  # noqa: ANN401
                if not event.is_directory:
                    watcher._on_event(str(event.src_path), deleted=False)

            def on_created(self, event: Any) -> None:  # noqa: ANN401
                if not event.is_directory:
                    watcher._on_event(str(event.src_path), deleted=False)

            def on_deleted(self, event: Any) -> None:  # noqa: ANN401
                if not event.is_directory:
                    watcher._on_event(str(event.src_path), deleted=True)

            def on_moved(self, event: Any) -> None:  # noqa: ANN401
                if not event.is_directory:
                    watcher._on_event(str(event.src_path), deleted=True)
                    watcher._on_event(str(event.dest_path), deleted=False)

        observer = ObserverClass()
        observer.schedule(_Handler(), str(self.settings.vault_path), recursive=True)
        observer.start()
        self._observer = observer

    def _on_event(self, src_path: str, *, deleted: bool) -> None:
        """Debounce and dispatch file system events."""
        path = Path(src_path)

        # Only handle supported extensions
        if path.suffix.lower() not in {".md", ".pdf"}:
            return
        if self.settings.is_ignored_path(path):
            return

        key = src_path
        with self._lock:
            existing = self._timers.pop(key, None)
            if existing is not None:
                existing.cancel()

            delay = self.settings.watcher_debounce_seconds

            def _fire(p: Path = path, d: bool = deleted) -> None:
                with self._lock:
                    self._timers.pop(key, None)
                self._dispatch(p, deleted=d)

            timer = threading.Timer(delay, _fire)
            self._timers[key] = timer
            timer.start()

    def _dispatch(self, path: Path, *, deleted: bool) -> None:
        if deleted:
            n = self.pipeline.store.delete_by_file(str(path))
            logger.debug("Deleted %d chunks for %s", n, path)
        else:
            result = self.pipeline.index_file(path)
            logger.debug("Indexed %s: %s (%d chunks)", path, result.status, result.chunks_added)


# ── Convenience type alias for callers ────────────────────────────────────────

WatcherCallback = Callable[[Path, bool], None]
