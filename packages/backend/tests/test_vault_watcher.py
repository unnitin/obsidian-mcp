"""Unit tests for VaultWatcher."""

from __future__ import annotations

import time
import unittest.mock as mock
from pathlib import Path

from obsidian_search.config import Settings
from obsidian_search.ingestion.pipeline import IndexingPipeline
from obsidian_search.watcher.vault_watcher import VaultWatcher


def _make_watcher(tmp_path: Path, debounce: float = 0.05) -> VaultWatcher:
    settings = Settings(vault_path=str(tmp_path), watcher_debounce_seconds=debounce)
    pipeline = mock.MagicMock(spec=IndexingPipeline)
    pipeline.store = mock.MagicMock()
    pipeline.store.delete_by_file.return_value = 0
    pipeline.index_file.return_value = mock.MagicMock(status="ok", chunks_added=1)
    return VaultWatcher(settings=settings, pipeline=pipeline)


class TestWatcherProperties:
    def test_is_running_false_before_start(self, tmp_path: Path) -> None:
        w = _make_watcher(tmp_path)
        assert w.is_running is False

    def test_stop_before_start_is_noop(self, tmp_path: Path) -> None:
        w = _make_watcher(tmp_path)
        w.stop()  # must not raise

    def test_start_sets_is_running(self, tmp_path: Path) -> None:
        w = _make_watcher(tmp_path)
        with mock.patch.object(w, "_reconcile"), mock.patch.object(w, "_start_observer"):
            w.start()
        assert w.is_running is True
        w.stop()

    def test_start_twice_is_idempotent(self, tmp_path: Path) -> None:
        w = _make_watcher(tmp_path)
        with mock.patch.object(w, "_reconcile"), mock.patch.object(w, "_start_observer"):
            w.start()
            w.start()  # second call — no error
        assert w.is_running is True
        w.stop()

    def test_stop_sets_is_running_false(self, tmp_path: Path) -> None:
        w = _make_watcher(tmp_path)
        with mock.patch.object(w, "_reconcile"), mock.patch.object(w, "_start_observer"):
            w.start()
        w.stop()
        assert w.is_running is False


class TestWatcherReconcile:
    def test_reconcile_indexes_md_files(self, tmp_path: Path) -> None:
        (tmp_path / "note.md").write_text("# Hello")
        settings = Settings(vault_path=str(tmp_path))
        pipeline = mock.MagicMock(spec=IndexingPipeline)
        pipeline.store = mock.MagicMock()
        pipeline.index_file.return_value = mock.MagicMock(status="ok", chunks_added=1)
        w = VaultWatcher(settings=settings, pipeline=pipeline)
        w._reconcile()
        pipeline.index_file.assert_called_once()

    def test_reconcile_skips_ignored_paths(self, tmp_path: Path) -> None:
        obsidian_dir = tmp_path / ".obsidian"
        obsidian_dir.mkdir()
        (obsidian_dir / "config.md").write_text("ignored")
        (tmp_path / "real.md").write_text("# Real")
        settings = Settings(vault_path=str(tmp_path))
        pipeline = mock.MagicMock(spec=IndexingPipeline)
        pipeline.store = mock.MagicMock()
        pipeline.index_file.return_value = mock.MagicMock(status="ok", chunks_added=1)
        w = VaultWatcher(settings=settings, pipeline=pipeline)
        w._reconcile()
        # Only real.md should be indexed
        call_paths = [str(c.args[0]) for c in pipeline.index_file.call_args_list]
        assert not any(".obsidian" in p for p in call_paths)
        assert any("real.md" in p for p in call_paths)

    def test_reconcile_handles_index_error_gracefully(self, tmp_path: Path) -> None:
        (tmp_path / "note.md").write_text("# Hello")
        settings = Settings(vault_path=str(tmp_path))
        pipeline = mock.MagicMock(spec=IndexingPipeline)
        pipeline.store = mock.MagicMock()
        pipeline.index_file.side_effect = RuntimeError("disk error")
        w = VaultWatcher(settings=settings, pipeline=pipeline)
        w._reconcile()  # must not raise


class TestWatcherOnEvent:
    def test_unsupported_extension_ignored(self, tmp_path: Path) -> None:
        w = _make_watcher(tmp_path, debounce=0.05)
        with mock.patch.object(w, "_dispatch") as dispatch:
            w._on_event(str(tmp_path / "image.png"), deleted=False)
            time.sleep(0.15)
        dispatch.assert_not_called()

    def test_ignored_path_not_dispatched(self, tmp_path: Path) -> None:
        obsidian_dir = tmp_path / ".obsidian"
        obsidian_dir.mkdir()
        w = _make_watcher(tmp_path, debounce=0.05)
        with mock.patch.object(w, "_dispatch") as dispatch:
            w._on_event(str(obsidian_dir / "config.md"), deleted=False)
            time.sleep(0.15)
        dispatch.assert_not_called()

    def test_md_file_dispatched_after_debounce(self, tmp_path: Path) -> None:
        md = tmp_path / "note.md"
        md.write_text("hello")
        w = _make_watcher(tmp_path, debounce=0.05)
        dispatched: list[tuple[Path, bool]] = []

        def fake_dispatch(path: Path, *, deleted: bool) -> None:
            dispatched.append((path, deleted))

        with mock.patch.object(w, "_dispatch", side_effect=fake_dispatch):
            w._on_event(str(md), deleted=False)
            time.sleep(0.2)

        assert len(dispatched) == 1
        assert dispatched[0][1] is False  # not deleted

    def test_rapid_events_coalesced(self, tmp_path: Path) -> None:
        """Multiple rapid events for the same file → single dispatch."""
        md = tmp_path / "note.md"
        md.write_text("hello")
        w = _make_watcher(tmp_path, debounce=0.1)
        dispatched: list[Path] = []

        def fake_dispatch(path: Path, *, deleted: bool) -> None:
            dispatched.append(path)

        with mock.patch.object(w, "_dispatch", side_effect=fake_dispatch):
            for _ in range(5):
                w._on_event(str(md), deleted=False)
                time.sleep(0.01)
            time.sleep(0.3)

        assert len(dispatched) == 1

    def test_delete_event_dispatched_with_deleted_true(self, tmp_path: Path) -> None:
        md = tmp_path / "note.md"
        w = _make_watcher(tmp_path, debounce=0.05)
        dispatched: list[tuple[Path, bool]] = []

        def fake_dispatch(path: Path, *, deleted: bool) -> None:
            dispatched.append((path, deleted))

        with mock.patch.object(w, "_dispatch", side_effect=fake_dispatch):
            w._on_event(str(md), deleted=True)
            time.sleep(0.2)

        assert len(dispatched) == 1
        assert dispatched[0][1] is True  # deleted=True


class TestWatcherDispatch:
    def test_dispatch_delete_calls_store(self, tmp_path: Path) -> None:
        w = _make_watcher(tmp_path)
        w._dispatch(tmp_path / "note.md", deleted=True)
        w.pipeline.store.delete_by_file.assert_called_once()

    def test_dispatch_modify_calls_index_file(self, tmp_path: Path) -> None:
        w = _make_watcher(tmp_path)
        w._dispatch(tmp_path / "note.md", deleted=False)
        w.pipeline.index_file.assert_called_once()


class TestWatcherStop:
    def test_stop_cancels_pending_timers(self, tmp_path: Path) -> None:
        md = tmp_path / "note.md"
        md.write_text("hello")
        w = _make_watcher(tmp_path, debounce=5.0)  # long debounce
        dispatched: list[Path] = []

        def fake_dispatch(path: Path, *, deleted: bool) -> None:
            dispatched.append(path)

        with mock.patch.object(w, "_dispatch", side_effect=fake_dispatch):
            w._on_event(str(md), deleted=False)
            w.stop()  # cancel before debounce fires
            time.sleep(0.1)

        assert dispatched == []  # timer was cancelled
