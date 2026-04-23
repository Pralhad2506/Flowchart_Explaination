"""
utils/file_utils.py — File validation, temporary directory management, and ZIP creation.
"""

import os
import shutil
import uuid
import zipfile
from pathlib import Path
from typing import List, Tuple

from  app.config import settings
from  app.utils.logger import get_logger

logger = get_logger(__name__)


# ── Validation ────────────────────────────────────────────────────────────────

def validate_folder(folder_path: str) -> Tuple[bool, str]:
    """
    Check that *folder_path* exists, is a directory, and is readable.
    Returns (ok: bool, error_message: str).
    """
    p = Path(folder_path)
    if not p.exists():
        return False, f"Path does not exist: {folder_path}"
    if not p.is_dir():
        return False, f"Path is not a directory: {folder_path}"
    if not os.access(p, os.R_OK):
        return False, f"Directory is not readable: {folder_path}"
    return True, ""


def collect_supported_files(folder_path: str) -> List[Path]:
    """
    Recursively collect all files with a supported extension inside *folder_path*.
    Skips hidden files and __MACOSX artefacts.
    """
    folder = Path(folder_path)
    found: List[Path] = []
    for f in sorted(folder.rglob("*")):
        if (
            f.is_file()
            and not f.name.startswith(".")
            and "__MACOSX" not in str(f)
            and f.suffix.lower() in settings.supported_extensions
        ):
            found.append(f)
    logger.info("Collected %d supported file(s) from %s", len(found), folder_path)
    return found


def is_file_readable(file_path: Path) -> bool:
    """Return True if the file is non-empty and readable."""
    try:
        return file_path.is_file() and file_path.stat().st_size > 0 and os.access(file_path, os.R_OK)
    except OSError:
        return False


# ── Job / temp directory management ──────────────────────────────────────────

def create_job_directory(job_id: str) -> Path:
    """Create and return a job-specific output directory."""
    job_dir = settings.output_dir / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    logger.debug("Created job directory: %s", job_dir)
    return job_dir


def cleanup_job_directory(job_id: str) -> None:
    """Remove temporary files for a finished job (keeps the ZIP)."""
    job_dir = settings.output_dir / job_id / "tmp"
    if job_dir.exists():
        shutil.rmtree(job_dir, ignore_errors=True)
        logger.debug("Cleaned up temp dir for job %s", job_id)


# ── ZIP packaging ─────────────────────────────────────────────────────────────

def create_zip(source_dir: Path, zip_path: Path) -> Path:
    """
    Compress all files inside *source_dir* into *zip_path*.
    Directory structure inside the ZIP mirrors *source_dir*.
    Returns the path to the created ZIP file.
    """
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for file in sorted(source_dir.rglob("*")):
            if file.is_file():
                arcname = file.relative_to(source_dir)
                zf.write(file, arcname)
                logger.debug("Added to ZIP: %s", arcname)
    logger.info("ZIP created at %s (%.1f KB)", zip_path, zip_path.stat().st_size / 1024)
    return zip_path


def generate_job_id() -> str:
    """Return a new unique job identifier."""
    return uuid.uuid4().hex


# ── Safe file name ─────────────────────────────────────────────────────────────

def safe_stem(file_path: Path) -> str:
    """Return a filesystem-safe version of the file stem (no spaces/special chars)."""
    import re
    stem = file_path.stem
    return re.sub(r"[^\w\-]", "_", stem)