"""FastAPI routes for ingestion: /ingest/url, /ingest/pdf, /index/document."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from obsidian_search.ingestion.pipeline import IndexingPipeline
from obsidian_search.models import IngestResult

router = APIRouter()


# ── Request schemas ───────────────────────────────────────────────────────────


class IngestUrlRequest(BaseModel):
    url: Annotated[str, Field(min_length=4)]
    tags: list[str] | None = None


class IngestPdfRequest(BaseModel):
    file_path: Annotated[str, Field(min_length=1)]


class RemoveDocumentRequest(BaseModel):
    file_path: Annotated[str, Field(min_length=1)]


# ── Factory ───────────────────────────────────────────────────────────────────


def create_ingest_router(pipeline: IndexingPipeline) -> APIRouter:
    """Return a router with all ingest routes bound to *pipeline*."""

    @router.post("/ingest/url", response_model=IngestResult, status_code=status.HTTP_200_OK)
    def ingest_url(req: IngestUrlRequest) -> IngestResult:
        result = pipeline.index_url(req.url, tags=req.tags)
        if result.status == "failed":
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Failed to fetch or extract content from {req.url!r}",
            )
        return result

    @router.post("/ingest/pdf", response_model=IngestResult, status_code=status.HTTP_200_OK)
    def ingest_pdf(req: IngestPdfRequest) -> IngestResult:
        path = Path(req.file_path)
        if not path.exists():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"File not found: {req.file_path!r}",
            )
        if path.suffix.lower() != ".pdf":
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Only .pdf files are supported by this endpoint",
            )
        result = pipeline.index_file(path)
        return result

    @router.delete("/index/document", response_model=IngestResult, status_code=status.HTTP_200_OK)
    def remove_document(req: RemoveDocumentRequest) -> IngestResult:
        removed = pipeline.store.delete_by_file(req.file_path)
        return IngestResult(chunks_added=0, chunks_removed=removed, status="ok")

    return router
