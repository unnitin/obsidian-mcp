"""FastAPI application factory and CLI entry point."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from obsidian_search.config import Settings
from obsidian_search.embedding.embedder import Embedder
from obsidian_search.ingestion.pipeline import IndexingPipeline
from obsidian_search.models import SearchResult
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

    app = FastAPI(title="obsidian-search", version="0.1.0", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["app://obsidian.md", "http://localhost:51234"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    searcher = Searcher(settings=settings, store=store, embedder=embedder)

    # ── Core routes ───────────────────────────────────────────────────────────

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "vault_path": str(settings.vault_path)}

    @app.post("/search", response_model=SearchResponse)
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

    @app.get("/status", response_model=StatusResponse)
    def status() -> StatusResponse:
        s = store.stats()
        return StatusResponse(**s, is_watching=watcher is not None and watcher.is_running)

    # ── Ingest routes ─────────────────────────────────────────────────────────

    ingest_router = create_ingest_router(pipeline)
    app.include_router(ingest_router)

    return app


# ── CLI entry point ───────────────────────────────────────────────────────────


def main() -> None:
    """Start the FastAPI server (used by the obsidian-search-api script)."""
    import uvicorn

    settings = Settings()  # type: ignore[call-arg]  # vault_path read from env
    settings.db_dir.mkdir(parents=True, exist_ok=True)

    store = VectorStore(settings.db_path)
    store.initialize(dims=768)

    embedder = Embedder(model_name=settings.embedding_model)

    app = create_app(settings=settings, store=store, embedder=embedder, start_watcher=True)

    uvicorn.run(app, host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
