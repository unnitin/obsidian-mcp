"""Tests for bearer-token auth on the HTTP API."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from obsidian_search.api.server import create_app
from obsidian_search.config import Settings
from obsidian_search.embedding.embedder import Embedder
from obsidian_search.store.vector_store import VectorStore

DIMS = 8
TOKEN = "s3cret-token-value"


def _fake_encode(texts: list[str]) -> np.ndarray:
    vecs = np.random.rand(len(texts), DIMS).astype(np.float32)
    return vecs / np.linalg.norm(vecs, axis=1, keepdims=True)


def _client(tmp_path: Path, **overrides: object) -> tuple[TestClient, VectorStore]:
    settings = Settings(vault_path=str(tmp_path), **overrides)  # type: ignore[arg-type]
    store = VectorStore(tmp_path / "test.db")
    store.initialize(dims=DIMS)
    embedder = Embedder.__new__(Embedder)
    embedder.encode = _fake_encode  # type: ignore[method-assign]
    embedder.dims = DIMS
    app = create_app(settings=settings, store=store, embedder=embedder)
    return TestClient(app, raise_server_exceptions=True), store


class TestNoTokenConfigured:
    """Loopback-only default: the OS is the boundary, so no token is required."""

    def test_search_is_open(self, tmp_path: Path) -> None:
        client, store = _client(tmp_path)
        assert client.post("/search", json={"query": "hello"}).status_code == 200
        store.close()

    def test_status_is_open(self, tmp_path: Path) -> None:
        client, store = _client(tmp_path)
        assert client.get("/status").status_code == 200
        store.close()


class TestTokenConfigured:
    def test_health_stays_open(self, tmp_path: Path) -> None:
        client, store = _client(tmp_path, api_token=TOKEN)
        resp = client.get("/health")
        assert resp.status_code == 200
        store.close()

    def test_health_does_not_leak_the_vault_path(self, tmp_path: Path) -> None:
        client, store = _client(tmp_path, api_token=TOKEN)
        assert "vault_path" not in client.get("/health").json()
        store.close()

    def test_status_exposes_vault_path_behind_the_token(self, tmp_path: Path) -> None:
        client, store = _client(tmp_path, api_token=TOKEN)
        resp = client.get("/status", headers={"Authorization": f"Bearer {TOKEN}"})
        assert resp.json()["vault_path"] == str(tmp_path)
        store.close()

    @pytest.mark.parametrize(
        ("method", "path", "body"),
        [
            ("post", "/search", {"query": "hello"}),
            ("get", "/status", None),
            ("post", "/ingest/url", {"url": "https://example.com"}),
            ("post", "/ingest/file", {"file_path": "note.md"}),
            ("post", "/ingest/pdf", {"file_path": "doc.pdf"}),
            ("post", "/reindex", None),
        ],
    )
    def test_routes_reject_missing_token(
        self, tmp_path: Path, method: str, path: str, body: dict[str, object] | None
    ) -> None:
        client, store = _client(tmp_path, api_token=TOKEN)
        resp = client.request(method.upper(), path, json=body)
        assert resp.status_code == 401
        store.close()

    def test_delete_route_rejects_missing_token(self, tmp_path: Path) -> None:
        client, store = _client(tmp_path, api_token=TOKEN)
        resp = client.request("DELETE", "/index/document", json={"file_path": "note.md"})
        assert resp.status_code == 401
        store.close()

    def test_wrong_token_rejected(self, tmp_path: Path) -> None:
        client, store = _client(tmp_path, api_token=TOKEN)
        resp = client.post(
            "/search", json={"query": "hello"}, headers={"Authorization": "Bearer wrong"}
        )
        assert resp.status_code == 401
        store.close()

    def test_wrong_scheme_rejected(self, tmp_path: Path) -> None:
        client, store = _client(tmp_path, api_token=TOKEN)
        resp = client.post(
            "/search", json={"query": "hello"}, headers={"Authorization": f"Basic {TOKEN}"}
        )
        assert resp.status_code == 401
        store.close()

    def test_correct_token_accepted(self, tmp_path: Path) -> None:
        client, store = _client(tmp_path, api_token=TOKEN)
        resp = client.post(
            "/search", json={"query": "hello"}, headers={"Authorization": f"Bearer {TOKEN}"}
        )
        assert resp.status_code == 200
        store.close()

    def test_challenge_header_present(self, tmp_path: Path) -> None:
        client, store = _client(tmp_path, api_token=TOKEN)
        resp = client.get("/status")
        assert resp.headers.get("www-authenticate") == "Bearer"
        store.close()


class TestLoopbackGuard:
    """Binding beyond loopback without a token must not be possible."""

    def test_default_host_is_loopback(self) -> None:
        assert Settings(vault_path="/tmp/v").is_loopback_host

    @pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.42", "::"])
    def test_non_loopback_hosts_detected(self, host: str) -> None:
        assert not Settings(vault_path="/tmp/v", host=host).is_loopback_host

    @pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
    def test_loopback_hosts_detected(self, host: str) -> None:
        assert Settings(vault_path="/tmp/v", host=host).is_loopback_host

    def test_main_refuses_public_bind_without_token(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from obsidian_search.api import server

        monkeypatch.setenv("VAULT_PATH", str(tmp_path))
        monkeypatch.setenv("OBSIDIAN_SEARCH_HOST", "0.0.0.0")
        monkeypatch.delenv("OBSIDIAN_SEARCH_API_TOKEN", raising=False)
        with pytest.raises(SystemExit, match="without authentication"):
            server.main()


class TestDefaultTopK:
    """OBSIDIAN_SEARCH_DEFAULT_TOP_K was unreachable: both entry points always
    sent an explicit top_k, so the Searcher's fallback never fired."""

    def _seed(self, store: VectorStore, n: int) -> None:
        from obsidian_search.models import Chunk, ChunkId, SourceType

        for i in range(n):
            store.upsert_chunks(
                [
                    Chunk(
                        id=ChunkId.generate(f"/v/n{i}.md", 0),
                        source_type=SourceType.MARKDOWN,
                        file_path=f"/v/n{i}.md",
                        content=f"content number {i}",
                        mtime=1.0,
                        chunk_index=0,
                    )
                ],
                _fake_encode([f"content number {i}"]),
            )

    def test_omitted_top_k_uses_the_configured_default(self, tmp_path: Path) -> None:
        client, store = _client(tmp_path, default_top_k=3)
        self._seed(store, 10)
        resp = client.post("/search", json={"query": "content"})
        assert resp.status_code == 200
        assert len(resp.json()["results"]) == 3
        store.close()

    def test_explicit_top_k_still_wins(self, tmp_path: Path) -> None:
        client, store = _client(tmp_path, default_top_k=3)
        self._seed(store, 10)
        resp = client.post("/search", json={"query": "content", "top_k": 7})
        assert len(resp.json()["results"]) == 7
        store.close()

    def test_out_of_range_top_k_still_rejected(self, tmp_path: Path) -> None:
        client, store = _client(tmp_path)
        assert client.post("/search", json={"query": "x", "top_k": 0}).status_code == 422
        assert client.post("/search", json={"query": "x", "top_k": 101}).status_code == 422
        store.close()
