"""
config.py — Application-wide settings loaded from environment variables.
"""

import os
from pathlib import Path
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # ── Groq ──────────────────────────────────────────────────────────────────
    groq_api_key: str = os.getenv("GROQ_API_KEY", "")
    groq_model: str = "llama-3.3-70b-versatile"          # Best reasoning model on Groq
    groq_max_tokens: int = 4096
    groq_temperature: float = 0.2                 # Low temp → deterministic explanations

    # ── Paths ─────────────────────────────────────────────────────────────────
    base_dir: Path = Path(__file__).resolve().parent.parent
    output_dir: Path = base_dir / "outputs"
    log_dir: Path = base_dir / "logs"

    # ── Processing ────────────────────────────────────────────────────────────
    max_concurrent_files: int = 4                 # Semaphore limit for async processing
    max_image_dimension: int = 2048               # Cap rasterised page size
    ocr_lang: str = "eng"                         # Tesseract language
    pdf_dpi: int = 200                            # DPI for PDF → image rasterisation

    # ── Diagram detection thresholds ─────────────────────────────────────────
    # Minimum fraction of OCR keywords that flag a region as a diagram
    diagram_keyword_threshold: int = 2
    min_diagram_text_length: int = 30             # Skip tiny text blobs

    # ── Supported file extensions ─────────────────────────────────────────────
    supported_extensions: tuple = (
        ".pdf", ".ppt", ".pptx", ".doc", ".docx", ".xls", ".xlsx"
    )

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()

# Ensure output and log directories exist
settings.output_dir.mkdir(parents=True, exist_ok=True)
settings.log_dir.mkdir(parents=True, exist_ok=True)