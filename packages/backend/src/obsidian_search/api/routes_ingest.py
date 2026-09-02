"""FastAPI routes for ingestion: /ingest/url, /ingest/pdf, /index/document."""

from __future__ import annotations

import threading
import uuid
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from obsidian_search.config import Settings, VaultPathError
from obsidian_search.ingestion.pipeline import IndexingPipeline
from obsidian_search.models import IngestResult


def _is_url(value: str) -> bool:
    return value.startswith(("http://", "https://"))


class ReindexStatus(BaseModel):
    job_id: str
    status: Literal["running", "completed", "failed", "cancelled"]
    files_total: int = 0
    files_done: int = 0
    chunks_added: int = 0
    error: str | None = None


# ── In-memory reindex job tracker ─────────────────────────────────────────────


class ReindexJobs:
    """Reindex jobs for one router instance.

    Owned by the router rather than the module: as a module global it was
    shared by every app built in the process, so tests leaked jobs into each
    other and two apps over one vault would have reported each other's
    progress.
    """

    MAX_COMPLETED = 20

    def __init__(self) -> None:
        self._jobs: dict[str, ReindexStatus] = {}
        self._stop_events: dict[str, threading.Event] = {}
        self._lock = threading.Lock()

    def create(self) -> tuple[ReindexStatus, threading.Event]:
        """Register a new running job, evicting old finished ones."""
        with self._lock:
            self._evict_finished()
            job = ReindexStatus(job_id=str(uuid.uuid4()), status="running")
            stop = threading.Event()
            self._jobs[job.job_id] = job
            self._stop_events[job.job_id] = stop
            return job, stop

    def get(self, job_id: str) -> ReindexStatus | None:
        with self._lock:
            return self._jobs.get(job_id)

    def request_stop(self, job_id: str) -> None:
        with self._lock:
            event = self._stop_events.get(job_id)
        if event is not None:
            event.set()

    def _evict_finished(self) -> None:
        """Keep at most MAX_COMPLETED finished jobs; running jobs are never evicted."""
        finished = [jid for jid, j in self._jobs.items() if j.status != "running"]
        for jid in finished[: -self.MAX_COMPLETED or None]:
            del self._jobs[jid]
            self._stop_events.pop(jid, None)


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
    jobs = ReindexJobs()

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
        job, stop = jobs.create()

        def _run() -> None:
            try:
                md_files = list(settings.vault_path.rglob("*.md"))
                job.files_total = len(md_files)
                for path in md_files:
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
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No reindex job found with id {job_id!r}",
            )
        return job

    @router.delete("/reindex/{job_id}", response_model=ReindexStatus)
    def cancel_reindex(job_id: str) -> ReindexStatus:
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No reindex job found with id {job_id!r}",
            )
        if job.status == "running":
            jobs.request_stop(job_id)
        return job

    return router
