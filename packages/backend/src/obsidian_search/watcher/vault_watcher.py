"""FSEvents-based vault watcher with debounce and startup reconciliation."""

from __future__ import annotations

import hashlib
import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import IO, Any

from obsidian_search.config import Settings, VaultPathError
from obsidian_search.ingestion.pipeline import IndexingPipeline, iter_vault_files

logger = logging.getLogger(__name__)


def _lock_path(vault_path: Path) -> Path:
    """Per-vault lock file, outside the vault so it is never synced.

    Keyed by the resolved vault path, so two vaults on one machine watch
    independently while two processes on the same vault contend.
    """
    digest = hashlib.sha256(str(vault_path.resolve()).encode()).hexdigest()[:16]
    return Path.home() / ".cache" / "obsidian-search" / f"{digest}.watcher.lock"


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

    Single owner per vault: the API server and the MCP server each construct a
    watcher, and the intended setup can run both.  Two watchers on one vault
    means two startup reconciliations re-embedding the same files and two
    processes writing the same SQLite index on every save.  ``start()``
    therefore takes an exclusive file lock keyed to the vault; whichever
    process gets it does the watching, and the other skips both the walk and
    the observer.  The lock is advisory and per-machine, which is what we want:
    two Macs syncing the same vault each watch their own local copy.
    """

    def __init__(self, settings: Settings, pipeline: IndexingPipeline) -> None:
        self.settings = settings
        self.pipeline = pipeline
        self._observer: Any = None  # noqa: ANN401
        self._timers: dict[str, threading.Timer] = {}
        self._lock = threading.Lock()
        self._running = False
        self._lock_file: IO[str] | None = None

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self) -> None:
        """Start the watcher and run startup reconciliation.

        Does nothing if another process already owns this vault — check
        ``is_running`` to find out whether this instance took ownership.
        """
        if self._running:
            return

        if not self._acquire_ownership():
            logger.warning(
                "Another obsidian-search process is already watching %s — "
                "this process will not reconcile or watch. Its index writes "
                "still work; only one process reindexes on file change.",
                self.settings.vault_path,
            )
            return

        try:
            self._reconcile()
            self._start_observer()
        except Exception:
            self._release_ownership()
            raise

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

        self._release_ownership()
        logger.info("VaultWatcher stopped")

    # ── Ownership ─────────────────────────────────────────────────────────────

    def _acquire_ownership(self) -> bool:
        """Try to become the single watcher for this vault.

        Returns True if this process may watch. On platforms without
        ``fcntl`` there is no cross-process lock available, so we assume a
        single process rather than refusing to watch at all.
        """
        try:
            import fcntl
        except ImportError:
            return True

        path = _lock_path(self.settings.vault_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = path.open("w")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False

        self._lock_file = handle
        return True

    def _release_ownership(self) -> None:
        """Drop the vault lock. The OS also drops it if the process dies."""
        handle, self._lock_file = self._lock_file, None
        if handle is None:
            return
        try:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except (ImportError, OSError):  # noqa: S110 — closing releases it anyway
            pass
        finally:
            handle.close()

    # ── Internal ──────────────────────────────────────────────────────────────

    def _reconcile(self) -> None:
        """Reindex changed files and evict deleted files from the index."""
        on_disk: set[str] = set()
        for path in iter_vault_files(self.settings):
            on_disk.add(str(path))
            try:
                self.pipeline.index_file(path)
            except Exception:  # noqa: BLE001
                logger.exception("Reconciliation error for %s", path)

        # Evict index entries for vault files that no longer exist on disk.
        for file_path in self.pipeline.store.list_files():
            if file_path not in on_disk:
                removed = self.pipeline.store.delete_by_file(file_path)
                logger.info("Evicted deleted file from index: %s (%d chunks)", file_path, removed)

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

        # Confine to the vault. is_ignored_path only looks for system folder
        # names among the path parts, so it says nothing about whether the path
        # is inside the vault at all — and on_moved forwards dest_path straight
        # through, so a note moved out of the vault would otherwise be indexed
        # at its new location outside it.
        try:
            path = self.settings.resolve_in_vault(path)
        except VaultPathError:
            logger.debug("Ignoring event outside the vault: %s", src_path)
            return

        if self.settings.is_ignored_path(path):
            return

        key = str(path)
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
