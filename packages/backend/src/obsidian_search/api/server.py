"""FastAPI application factory."""

from __future__ import annotations

import time
from typing import Annotated, Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from obsidian_search.config import Settings
from obsidian_search.embedding.embedder import Embedder
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
) -> FastAPI:
    app = FastAPI(title="obsidian-search", version="0.1.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["app://obsidian.md", "http://localhost:51234"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    searcher = Searcher(settings=settings, store=store, embedder=embedder)

    @app.get("/health")  # type: ignore[misc]
    def health() -> dict[str, Any]:
        return {"status": "ok", "vault_path": str(settings.vault_path)}

    @app.post("/search", response_model=SearchResponse)  # type: ignore[misc]
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

    @app.get("/status", response_model=StatusResponse)  # type: ignore[misc]
    def status() -> StatusResponse:
        s = store.stats()
        return StatusResponse(**s)

    return app
