"""FastAPI routes for ingestion: /ingest/url, /ingest/pdf, /index/document."""

from __future__ import annotations

import threading
import uuid
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from obsidian_search.config import Settings, VaultPathError
from obsidian_search.ingestion.pipeline import IndexingPipeline, iter_vault_files
from obsidian_search.models import IngestResult

# ── In-memory reindex job tracker ─────────────────────────────────────────────

_jobs: dict[str, ReindexStatus] = {}
_job_stop_events: dict[str, threading.Event] = {}
_MAX_COMPLETED_JOBS = 20


def _evict_finished_jobs() -> None:
    """Remove oldest completed/failed/cancelled jobs, keeping at most _MAX_COMPLETED_JOBS."""
    finished = [jid for jid, j in _jobs.items() if j.status not in ("running",)]
    for jid in finished[:-_MAX_COMPLETED_JOBS]:
        del _jobs[jid]
        _job_stop_events.pop(jid, None)


def _is_url(value: str) -> bool:
    return value.startswith(("http://", "https://"))


class ReindexStatus(BaseModel):
    job_id: str
    status: Literal["running", "completed", "failed", "cancelled"]
    files_total: int = 0
    files_done: int = 0
    chunks_added: int = 0
    error: str | None = None


# ── Request schemas ───────────────────────────────────────────────────────────


class IngestUrlRequest(BaseModel):
    url: Annotated[str, Field(min_length=4)]
    tags: list[str] | None = None


class IngestPdfRequest(BaseModel):
    file_path: Annotated[str, Field(min_length=1)]


class IngestFileRequest(BaseModel):
    file_path: Annotated[str, Field(min_length=1)]


class RemoveDocumentRequest(BaseModel):
    file_path: Annotated[str, Field(min_length=1)]


# ── Factory ───────────────────────────────────────────────────────────────────


def create_ingest_router(pipeline: IndexingPipeline, settings: Settings) -> APIRouter:
    """Return a router with all ingest routes bound to *pipeline*."""
    router = APIRouter()

    def _in_vault(file_path: str) -> Path:
        """Resolve a caller-supplied path, rejecting anything outside the vault.

        Checked before existence so the endpoint cannot be used to probe for
        files elsewhere on the machine.
        """
        try:
            return settings.resolve_in_vault(file_path)
        except VaultPathError as exc:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=str(exc),
            ) from exc

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
        path = _in_vault(req.file_path)
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

    @router.post("/ingest/file", response_model=IngestResult, status_code=status.HTTP_200_OK)
    def ingest_file(req: IngestFileRequest) -> IngestResult:
        path = _in_vault(req.file_path)
        if not path.exists():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"File not found: {req.file_path!r}",
            )
        if path.suffix.lower() != ".md":
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Only .md files are supported by this endpoint",
            )
        result = pipeline.index_file(path)
        return result

    @router.delete("/index/document", response_model=IngestResult, status_code=status.HTTP_200_OK)
    def remove_document(req: RemoveDocumentRequest) -> IngestResult:
        # Web sources are keyed by URL, not by a vault path — pass those through.
        key = req.file_path
        if not _is_url(key):
            key = str(_in_vault(key))
        removed = pipeline.store.delete_by_file(key)
        return IngestResult(chunks_added=0, chunks_removed=removed, status="ok")

    @router.post("/reindex", response_model=ReindexStatus, status_code=status.HTTP_202_ACCEPTED)
    def start_reindex() -> ReindexStatus:
        _evict_finished_jobs()
        job_id = str(uuid.uuid4())
        job = ReindexStatus(job_id=job_id, status="running")
        stop = threading.Event()
        _jobs[job_id] = job
        _job_stop_events[job_id] = stop

        def _run() -> None:
            try:
                files = list(iter_vault_files(settings))
                job.files_total = len(files)
                for path in files:
                    if stop.is_set():
                        job.status = "cancelled"
                        return
                    result = pipeline.index_file(path)
                    job.chunks_added += result.chunks_added
                    job.files_done += 1
                job.status = "completed"
            except Exception as exc:  # noqa: BLE001
                job.status = "failed"
                job.error = str(exc)

        threading.Thread(target=_run, daemon=True).start()
        return job

    @router.get("/reindex/{job_id}", response_model=ReindexStatus)
    def get_reindex_status(job_id: str) -> ReindexStatus:
        job = _jobs.get(job_id)
        if job is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No reindex job found with id {job_id!r}",
            )
        return job

    @router.delete("/reindex/{job_id}", response_model=ReindexStatus)
    def cancel_reindex(job_id: str) -> ReindexStatus:
        job = _jobs.get(job_id)
        if job is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No reindex job found with id {job_id!r}",
            )
        if job.status == "running":
            _job_stop_events[job_id].set()
        return job

    return router
