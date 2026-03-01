"""Tests for application configuration."""

from pathlib import Path

import pytest
from obsidian_search.config import Settings
from pydantic import ValidationError


class TestSettingsDefaults:
    def test_default_port(self) -> None:
        s = Settings(vault_path="/tmp/vault")
        assert s.port == 51234

    def test_default_host(self) -> None:
        s = Settings(vault_path="/tmp/vault")
        assert s.host == "127.0.0.1"

    def test_default_model(self) -> None:
        s = Settings(vault_path="/tmp/vault")
        assert s.embedding_model == "nomic-ai/nomic-embed-text-v1.5"

    def test_default_top_k(self) -> None:
        s = Settings(vault_path="/tmp/vault")
        assert s.default_top_k == 10

    def test_default_rerank_candidates(self) -> None:
        s = Settings(vault_path="/tmp/vault")
        assert s.rerank_candidates == 50

    def test_default_chunk_max_tokens(self) -> None:
        s = Settings(vault_path="/tmp/vault")
        assert s.chunk_max_tokens == 512

    def test_default_chunk_min_tokens(self) -> None:
        s = Settings(vault_path="/tmp/vault")
        assert s.chunk_min_tokens == 64

    def test_default_chunk_overlap_tokens(self) -> None:
        s = Settings(vault_path="/tmp/vault")
        assert s.chunk_overlap_tokens == 50

    def test_default_watcher_debounce(self) -> None:
        s = Settings(vault_path="/tmp/vault")
        assert s.watcher_debounce_seconds == 2.0

    def test_default_excluded_folders_empty(self) -> None:
        s = Settings(vault_path="/tmp/vault")
        assert s.excluded_folders == []

    def test_default_embedding_batch_size(self) -> None:
        s = Settings(vault_path="/tmp/vault")
        assert s.embedding_batch_size == 32


class TestSettingsVaultPath:
    def test_vault_path_required(self) -> None:
        with pytest.raises(ValidationError):
            Settings()  # type: ignore[call-arg]

    def test_vault_path_stored_as_path(self, tmp_path: Path) -> None:
        s = Settings(vault_path=str(tmp_path))
        assert isinstance(s.vault_path, Path)

    def test_vault_path_string_accepted(self) -> None:
        s = Settings(vault_path="/tmp/vault")
        assert s.vault_path == Path("/tmp/vault")


class TestSettingsDbPath:
    def test_db_path_inside_vault(self, tmp_path: Path) -> None:
        s = Settings(vault_path=str(tmp_path))
        assert s.db_path == tmp_path / ".obsidian-search" / "semantic-search.db"

    def test_db_dir_inside_vault(self, tmp_path: Path) -> None:
        s = Settings(vault_path=str(tmp_path))
        assert s.db_dir == tmp_path / ".obsidian-search"


class TestSettingsEnvOverride:
    def test_port_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OBSIDIAN_SEARCH_PORT", "9000")
        s = Settings(vault_path="/tmp/vault")
        assert s.port == 9000

    def test_vault_path_from_env(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("VAULT_PATH", str(tmp_path))
        s = Settings()  # type: ignore[call-arg]
        assert s.vault_path == tmp_path

    def test_excluded_folders_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OBSIDIAN_SEARCH_EXCLUDED_FOLDERS", '["Archive","Templates"]')
        s = Settings(vault_path="/tmp/vault")
        assert s.excluded_folders == ["Archive", "Templates"]


class TestSettingsIgnoredPaths:
    def test_obsidian_dir_always_ignored(self, tmp_path: Path) -> None:
        s = Settings(vault_path=str(tmp_path))
        assert s.is_ignored_path(tmp_path / ".obsidian" / "workspace.json")

    def test_obsidian_search_dir_always_ignored(self, tmp_path: Path) -> None:
        s = Settings(vault_path=str(tmp_path))
        assert s.is_ignored_path(tmp_path / ".obsidian-search" / "semantic-search.db")

    def test_normal_md_not_ignored(self, tmp_path: Path) -> None:
        s = Settings(vault_path=str(tmp_path))
        assert not s.is_ignored_path(tmp_path / "Notes" / "hello.md")

    def test_excluded_folder_ignored(self, tmp_path: Path) -> None:
        s = Settings(vault_path=str(tmp_path), excluded_folders=["Archive"])
        assert s.is_ignored_path(tmp_path / "Archive" / "old.md")

    def test_nested_excluded_folder_ignored(self, tmp_path: Path) -> None:
        s = Settings(vault_path=str(tmp_path), excluded_folders=["Archive"])
        assert s.is_ignored_path(tmp_path / "Archive" / "2023" / "old.md")
