"""FastAPI application factory and CLI entry point."""

from __future__ import annotations

import secrets
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Request, status
from pydantic import BaseModel, Field

from obsidian_search import __version__
from obsidian_search.config import Settings
from obsidian_search.embedding.embedder import Embedder
from obsidian_search.ingestion.pipeline import IndexingPipeline
from obsidian_search.models import SearchResult
from obsidian_search.search.reranker import Reranker
from obsidian_search.search.searcher import Searcher
from obsidian_search.store.vector_store import VectorStore

# ── Request / response schemas ────────────────────────────────────────────────


class SearchRequest(BaseModel):
    query: Annotated[str, Field(min_length=1)]
    top_k: Annotated[int, Field(default=10, ge=1, le=100)] = 10
    source_types: list[str] | None = None
    tags: list[str] | None = None


class SearchResponse(BaseModel):
    results: list[SearchResult]
    query_time_ms: float


class StatusResponse(BaseModel):
    total_chunks: int
    total_documents: int
    last_indexed_at: float | None
    index_size_bytes: int
    is_watching: bool = False
    vault_path: str | None = None


# ── Auth ──────────────────────────────────────────────────────────────────────


def _make_auth_dependency(settings: Settings) -> Any:  # noqa: ANN401
    """Return a dependency enforcing the bearer token, or a no-op if unset.

    Compared with ``secrets.compare_digest`` so a wrong token cannot be
    recovered a character at a time by timing the response.
    """
    expected = settings.api_token

    def require_token(request: Request) -> None:
        if expected is None:
            return
        header = request.headers.get("authorization", "")
        scheme, _, presented = header.partition(" ")
        if scheme.lower() != "bearer" or not secrets.compare_digest(presented, expected):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing or invalid bearer token",
                headers={"WWW-Authenticate": "Bearer"},
            )

    return require_token


# ── Factory ───────────────────────────────────────────────────────────────────


def create_app(
    settings: Settings,
    store: VectorStore,
    embedder: Embedder,
    pipeline: IndexingPipeline | None = None,
    start_watcher: bool = False,
) -> FastAPI:
    from obsidian_search.api.routes_ingest import create_ingest_router
    from obsidian_search.watcher.vault_watcher import VaultWatcher

    if pipeline is None:
        pipeline = IndexingPipeline(settings=settings, store=store, embedder=embedder)

    watcher: VaultWatcher | None = None
    if start_watcher:
        watcher = VaultWatcher(settings=settings, pipeline=pipeline)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if watcher is not None:
            watcher.start()
        yield
        if watcher is not None:
            watcher.stop()
        store.close()

    # No CORS middleware: the HTTP API has no browser client. It exists for
    # local scripts and the MCP process, which are not subject to CORS.
    app = FastAPI(title="obsidian-search", version=__version__, lifespan=lifespan)

    reranker = (
        Reranker(model_name=settings.reranker_model, device=settings.device)
        if settings.reranker_enabled
        else None
    )
    searcher = Searcher(settings=settings, store=store, embedder=embedder, reranker=reranker)
    auth = Depends(_make_auth_dependency(settings))

    # ── Core routes ───────────────────────────────────────────────────────────

    @app.get("/health")
    def health() -> dict[str, Any]:
        """Unauthenticated liveness probe.

        Deliberately says nothing about the vault — the path moved to /status,
        which is behind the token.
        """
        return {"status": "ok"}

    @app.post("/search", response_model=SearchResponse, dependencies=[auth])
    def search(req: SearchRequest) -> SearchResponse:
        t0 = time.perf_counter()
        results = searcher.search(
            query=req.query,
            top_k=req.top_k,
            source_types=req.source_types,
            tags=req.tags,
        )
        elapsed = (time.perf_counter() - t0) * 1000
        return SearchResponse(results=results, query_time_ms=round(elapsed, 1))

    @app.get("/status", response_model=StatusResponse, dependencies=[auth])
    def index_status() -> StatusResponse:
        s = store.stats()
        return StatusResponse(
            **s,
            is_watching=watcher is not None and watcher.is_running,
            vault_path=str(settings.vault_path),
        )

    # ── Ingest routes ─────────────────────────────────────────────────────────

    ingest_router = create_ingest_router(pipeline, settings=settings)
    app.include_router(ingest_router, dependencies=[auth])

    return app


# ── CLI entry point ───────────────────────────────────────────────────────────


def main() -> None:
    """Start the FastAPI server (used by the obsidian-search-api script)."""
    import uvicorn

    settings = Settings()  # type: ignore[call-arg]  # vault_path read from env

    if not settings.is_loopback_host and settings.api_token is None:
        raise SystemExit(
            f"Refusing to bind {settings.host}:{settings.port} without authentication.\n"
            f"This exposes note contents and file indexing to every host that can\n"
            f"reach this machine. Set OBSIDIAN_SEARCH_API_TOKEN to a random secret\n"
            f"(e.g. `python -c 'import secrets; print(secrets.token_urlsafe(32))'`),\n"
            f"or leave OBSIDIAN_SEARCH_HOST at 127.0.0.1 for local-only access."
        )

    settings.db_dir.mkdir(parents=True, exist_ok=True)

    embedder = Embedder(model_name=settings.embedding_model, device=settings.device)
    embedder.load()

    store = VectorStore(settings.db_path)
    store.initialize(
        dims=embedder.dims,
        profile=embedder.profile,
        legacy_profile=embedder.legacy_profile,
    )

    app = create_app(settings=settings, store=store, embedder=embedder, start_watcher=True)

    uvicorn.run(app, host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
