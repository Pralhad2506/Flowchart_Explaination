"""
processors/batch_processor.py — Async batch processing of multiple files.

Manages a job store (in-memory), runs files concurrently (bounded by semaphore),
generates per-file and master DOCX outputs, and zips everything.

Job lifecycle: PENDING → RUNNING → COMPLETED | FAILED
"""

from __future__ import annotations

import asyncio
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional

from app.config import settings
from app.processors.file_processor import FileProcessor
from app.processors.groq_client import GroqClient
from app.processors.diagram_explainer import ExplainedContent
from app.generators.docx_generator import generate_file_docx, generate_master_docx
from app.utils.file_utils import (
    collect_supported_files,
    create_job_directory,
    create_zip,
    safe_stem,
    is_file_readable,
)
from app.utils.logger import get_logger

logger = get_logger(__name__)


# ── Job model ─────────────────────────────────────────────────────────────────

class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class FileResult:
    file_name: str
    status: str = "pending"         # "ok" | "error"
    sections_extracted: int = 0
    diagrams_detected: int = 0
    error: str = ""


@dataclass
class ProcessingJob:
    job_id: str
    folder_path: str
    status: JobStatus = JobStatus.PENDING
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    total_files: int = 0
    processed_files: int = 0
    file_results: List[FileResult] = field(default_factory=list)
    zip_path: Optional[str] = None
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "folder_path": self.folder_path,
            "status": self.status.value,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "total_files": self.total_files,
            "processed_files": self.processed_files,
            "file_results": [
                {
                    "file_name": fr.file_name,
                    "status": fr.status,
                    "sections_extracted": fr.sections_extracted,
                    "diagrams_detected": fr.diagrams_detected,
                    "error": fr.error,
                }
                for fr in self.file_results
            ],
            "zip_path": self.zip_path,
            "error": self.error,
        }


# ── In-memory job store ───────────────────────────────────────────────────────

class JobStore:
    """Simple thread-safe in-memory job registry."""

    def __init__(self) -> None:
        self._jobs: Dict[str, ProcessingJob] = {}
        self._lock = asyncio.Lock()

    async def create(self, job: ProcessingJob) -> None:
        async with self._lock:
            self._jobs[job.job_id] = job

    async def get(self, job_id: str) -> Optional[ProcessingJob]:
        async with self._lock:
            return self._jobs.get(job_id)

    async def update(self, job: ProcessingJob) -> None:
        async with self._lock:
            self._jobs[job.job_id] = job

    async def list_all(self) -> List[ProcessingJob]:
        async with self._lock:
            return list(self._jobs.values())


# Singleton store instance
job_store = JobStore()


# ── Batch processor ───────────────────────────────────────────────────────────

class BatchProcessor:
    """
    Coordinate async processing of all files in a folder.
    Intended to be launched as a background task via asyncio.create_task().
    """

    def __init__(self, job: ProcessingJob) -> None:
        self.job = job
        self._sem = asyncio.Semaphore(settings.max_concurrent_files)

    async def run(self) -> None:
        """Entry point — runs the full batch pipeline and updates the job store."""
        job = self.job
        job.status = JobStatus.RUNNING
        job.started_at = datetime.now(timezone.utc).isoformat()
        await job_store.update(job)

        try:
            # ── Collect files ─────────────────────────────────────────────────
            files = collect_supported_files(job.folder_path)
            files = [f for f in files if is_file_readable(f)]
            job.total_files = len(files)
            await job_store.update(job)

            if not files:
                job.status = JobStatus.FAILED
                job.error = "No supported, readable files found in the specified folder."
                job.completed_at = datetime.now(timezone.utc).isoformat()
                await job_store.update(job)
                return

            logger.info("Job %s: processing %d file(s)", job.job_id, len(files))

            # ── Job output directory ──────────────────────────────────────────
            job_dir = create_job_directory(job.job_id)
            docs_dir = job_dir / "documents"
            docs_dir.mkdir(exist_ok=True)

            # ── Process files concurrently ────────────────────────────────────
            async with GroqClient() as groq:
                tasks = [self._process_file(f, groq, docs_dir) for f in files]
                file_outputs = await asyncio.gather(*tasks, return_exceptions=True)

            # ── Collect results ───────────────────────────────────────────────
            all_results = []   # list of {"file_name": ..., "contents": [...]}
            for idx, outcome in enumerate(file_outputs):
                f = files[idx]
                if isinstance(outcome, Exception):
                    logger.error("File %s raised exception: %s", f.name, outcome)
                    job.file_results.append(
                        FileResult(file_name=f.name, status="error", error=str(outcome))
                    )
                else:
                    file_name, contents, docx_path = outcome
                    diagrams = sum(1 for c in contents if c.content_type == "diagram")
                    job.file_results.append(
                        FileResult(
                            file_name=file_name,
                            status="ok",
                            sections_extracted=len(contents),
                            diagrams_detected=diagrams,
                        )
                    )
                    all_results.append({"file_name": file_name, "contents": contents})
                job.processed_files = idx + 1
                await job_store.update(job)

            # ── Master document ───────────────────────────────────────────────
            if all_results:
                master_path = docs_dir / "MASTER_REPORT.docx"
                generate_master_docx(all_results, master_path)
                logger.info("Master report generated: %s", master_path.name)

            # ── ZIP everything ────────────────────────────────────────────────
            zip_path = job_dir / f"results_{job.job_id[:8]}.zip"
            create_zip(docs_dir, zip_path)

            job.zip_path = str(zip_path)
            job.status = JobStatus.COMPLETED
            job.completed_at = datetime.now(timezone.utc).isoformat()
            await job_store.update(job)
            logger.info("Job %s COMPLETED — ZIP: %s", job.job_id, zip_path.name)

        except Exception as exc:
            tb = traceback.format_exc()
            logger.error("Job %s FAILED: %s\n%s", job.job_id, exc, tb)
            job.status = JobStatus.FAILED
            job.error = f"{exc}"
            job.completed_at = datetime.now(timezone.utc).isoformat()
            await job_store.update(job)

    async def _process_file(
        self,
        file_path: Path,
        groq: GroqClient,
        docs_dir: Path,
    ):
        """Process a single file under the semaphore and generate its DOCX."""
        async with self._sem:
            try:
                processor = FileProcessor(file_path, groq)
                file_name, contents = await processor.run()

                # Generate individual file DOCX
                stem = safe_stem(file_path)
                docx_path = docs_dir / f"{stem}_analysis.docx"
                generate_file_docx(file_name, contents, docx_path)

                return file_name, contents, docx_path

            except Exception as exc:
                logger.error("Error processing file %s: %s", file_path.name, exc)
                raise