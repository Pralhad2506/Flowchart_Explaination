"""
main.py — FastAPI application entry point.

Endpoints
---------
POST /api/v1/process          — Submit a folder for batch processing
GET  /api/v1/status/{job_id}  — Poll job status
GET  /api/v1/download/{job_id}— Download ZIP result
GET  /api/v1/jobs             — List all jobs
GET  /api/v1/health           — Health check
DELETE /api/v1/jobs/{job_id}  — Cancel / remove a job record
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any, Dict, List

from fastapi import FastAPI, HTTPException, BackgroundTasks, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field, field_validator

from app.config import settings
from app.processors.batch_processor import (
    BatchProcessor,
    ProcessingJob,
    JobStatus,
    job_store,
)
from app.utils.file_utils import (
    validate_folder,
    collect_supported_files,
    generate_job_id,
)
from app.utils.logger import get_logger

logger = get_logger(__name__)

# ── App initialisation ────────────────────────────────────────────────────────

app = FastAPI(
    title="Diagram Processor API",
    description=(
        "Batch-process documents (PDF, PPTX, DOCX, XLSX) to detect flowcharts "
        "and block diagrams, explain them with AI, and export results as DOCX + ZIP.\n\n"
        "**Quick start:** POST to `/api/v1/process` with body: "
        '`{"folder_path": "/absolute/path/to/your/folder"}`'
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# ── CORS (allow all origins for local/dev use) ────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Root redirect → Swagger UI ────────────────────────────────────────────────
@app.get("/", include_in_schema=False)
async def root_redirect():
    """Redirect browser visits to the interactive API docs."""
    return RedirectResponse(url="/docs")


# ── Friendly 422 handler ──────────────────────────────────────────────────────
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Return a human-readable explanation instead of the default 422 blob."""
    errors = []
    for err in exc.errors():
        field = " → ".join(str(loc) for loc in err["loc"])
        errors.append({
            "field": field,
            "issue": err["msg"],
            "received": str(err.get("input", ""))[:200],
        })
    logger.warning("422 on %s %s: %s", request.method, request.url.path, errors)
    return JSONResponse(
        status_code=422,
        content={
            "error": "Request validation failed — check the 'detail' list below.",
            "detail": errors,
            "how_to_fix": (
                "Send a JSON body with the header  Content-Type: application/json.\n"
                "Required field: folder_path (string — absolute path to your documents folder).\n"
                'Correct curl example:\n'
                '  curl -X POST http://localhost:8000/api/v1/process \\\n'
                '       -H "Content-Type: application/json" \\\n'
                '       -d \'{"folder_path": "/absolute/path/to/your/folder"}\''
            ),
        },
    )


# ── Request / Response models ─────────────────────────────────────────────────

class ProcessRequest(BaseModel):
    folder_path: str = Field(
        ...,
        description="Absolute path to the folder containing documents to process.",
        examples=["/home/user/documents", "C:/Users/user/Documents"],
    )

    @field_validator("folder_path")
    @classmethod
    def folder_must_not_be_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("folder_path must not be empty")
        return v.strip()

    model_config = {
        "json_schema_extra": {
            "example": {
                "folder_path": "/absolute/path/to/your/documents/folder"
            }
        }
    }


class ProcessResponse(BaseModel):
    job_id: str
    status: str
    message: str
    total_files_found: int
    supported_extensions: List[str]


class StatusResponse(BaseModel):
    job_id: str
    status: str
    folder_path: str
    created_at: str
    started_at: str | None
    completed_at: str | None
    total_files: int
    processed_files: int
    progress_percent: float
    file_results: List[Dict[str, Any]]
    error: str | None


class JobListItem(BaseModel):
    job_id: str
    status: str
    folder_path: str
    total_files: int
    processed_files: int
    created_at: str


# ── Helper ────────────────────────────────────────────────────────────────────

def _progress(job: ProcessingJob) -> float:
    if job.total_files == 0:
        return 0.0
    return round(job.processed_files / job.total_files * 100, 1)


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/api/v1/health", tags=["System"])
async def health_check() -> dict:
    """Return service health and configuration summary."""
    groq_configured = bool(settings.groq_api_key)
    return {
        "status": "ok",
        "groq_configured": groq_configured,
        "groq_model": settings.groq_model,
        "supported_extensions": list(settings.supported_extensions),
        "max_concurrent_files": settings.max_concurrent_files,
        "output_dir": str(settings.output_dir),
    }


@app.post("/api/v1/process", response_model=ProcessResponse, tags=["Processing"])
async def start_processing(
    request: ProcessRequest,
    background_tasks: BackgroundTasks,
) -> ProcessResponse:
    """
    Submit a folder path for batch processing.

    Send a **JSON body** with `Content-Type: application/json`:
    ```json
    {"folder_path": "/absolute/path/to/your/folder"}
    ```

    Returns a `job_id` immediately. Poll `/api/v1/status/{job_id}` for progress.
    Download results via `/api/v1/download/{job_id}` when `status` is `completed`.
    """
    # ── Validate folder ───────────────────────────────────────────────────────
    ok, err = validate_folder(request.folder_path)
    if not ok:
        raise HTTPException(status_code=400, detail=err)

    # ── Check for supported files ─────────────────────────────────────────────
    files = collect_supported_files(request.folder_path)
    if not files:
        raise HTTPException(
            status_code=422,
            detail=(
                f"No supported files found in '{request.folder_path}'. "
                f"Supported extensions: {list(settings.supported_extensions)}"
            ),
        )

    # ── Create job ────────────────────────────────────────────────────────────
    job_id = generate_job_id()
    job = ProcessingJob(
        job_id=job_id,
        folder_path=request.folder_path,
        total_files=len(files),
    )
    await job_store.create(job)
    logger.info(
        "Job %s created — folder: %s  files: %d",
        job_id, request.folder_path, len(files),
    )

    # ── Launch background processing ──────────────────────────────────────────
    processor = BatchProcessor(job)
    background_tasks.add_task(processor.run)

    return ProcessResponse(
        job_id=job_id,
        status=JobStatus.PENDING.value,
        message=f"Job accepted. {len(files)} file(s) queued for processing.",
        total_files_found=len(files),
        supported_extensions=list(settings.supported_extensions),
    )


@app.get("/api/v1/status/{job_id}", response_model=StatusResponse, tags=["Processing"])
async def get_status(job_id: str) -> StatusResponse:
    """Poll the status and progress of a processing job."""
    job = await job_store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")

    return StatusResponse(
        job_id=job.job_id,
        status=job.status.value,
        folder_path=job.folder_path,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        total_files=job.total_files,
        processed_files=job.processed_files,
        progress_percent=_progress(job),
        file_results=job.to_dict()["file_results"],
        error=job.error,
    )


@app.get("/api/v1/download/{job_id}", tags=["Processing"])
async def download_results(job_id: str) -> FileResponse:
    """
    Download the ZIP archive of results for a completed job.

    Returns 202 if still running, 404 if not found, 500 on failure.
    """
    job = await job_store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")

    if job.status in (JobStatus.RUNNING, JobStatus.PENDING):
        raise HTTPException(
            status_code=202,
            detail=f"Job is still {job.status.value}. Progress: {_progress(job):.1f}%",
        )

    if job.status == JobStatus.FAILED:
        raise HTTPException(status_code=500, detail=f"Job failed: {job.error}")

    if not job.zip_path or not Path(job.zip_path).exists():
        raise HTTPException(
            status_code=500,
            detail="ZIP file not found. The job may have failed silently.",
        )

    zip_path = Path(job.zip_path)
    return FileResponse(
        path=str(zip_path),
        media_type="application/zip",
        filename=zip_path.name,
        headers={"Content-Disposition": f'attachment; filename="{zip_path.name}"'},
    )


@app.get("/api/v1/jobs", tags=["System"])
async def list_jobs() -> List[JobListItem]:
    """List all jobs, most recent first."""
    all_jobs = await job_store.list_all()
    all_jobs.sort(key=lambda j: j.created_at, reverse=True)
    return [
        JobListItem(
            job_id=j.job_id,
            status=j.status.value,
            folder_path=j.folder_path,
            total_files=j.total_files,
            processed_files=j.processed_files,
            created_at=j.created_at,
        )
        for j in all_jobs
    ]


@app.delete("/api/v1/jobs/{job_id}", tags=["System"])
async def delete_job(job_id: str) -> dict:
    """Remove a job record (does not delete output files on disk)."""
    job = await job_store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")
    if job.status == JobStatus.RUNNING:
        raise HTTPException(status_code=409, detail="Cannot delete a running job.")
    async with job_store._lock:
        del job_store._jobs[job_id]
    logger.info("Job %s deleted from store", job_id)
    return {"message": f"Job '{job_id}' removed."}


# ── Startup / shutdown events ─────────────────────────────────────────────────

@app.on_event("startup")
async def on_startup() -> None:
    logger.info("=" * 60)
    logger.info("Diagram Processor API  v1.0.0  starting up")
    logger.info("Swagger UI  → http://127.0.0.1:8000/docs")
    logger.info("Output dir  : %s", settings.output_dir)
    logger.info("Groq model  : %s", settings.groq_model)
    logger.info("Groq API key: %s", "SET" if settings.groq_api_key else "NOT SET — set GROQ_API_KEY in .env")
    logger.info("=" * 60)


@app.on_event("shutdown")
async def on_shutdown() -> None:
    logger.info("Diagram Processor API shutting down")