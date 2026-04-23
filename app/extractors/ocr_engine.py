"""
extractors/ocr_engine.py — Tesseract OCR wrapper with preprocessing.

Provides functions to run OCR on raw image bytes and return clean text.
Preprocessing (grayscale + mild sharpening) improves accuracy on diagrams.
"""

from __future__ import annotations

import io
from typing import Optional

from PIL import Image, ImageEnhance, ImageFilter
import pytesseract

from  app.config import settings
from  app.utils.logger import get_logger

logger = get_logger(__name__)


def _preprocess_image(img: Image.Image) -> Image.Image:
    """
    Light preprocessing to improve OCR accuracy on diagrams:
    1. Convert to RGB (handles RGBA, palette modes, etc.)
    2. Resize if too large (Tesseract works best ~300 DPI equivalents)
    3. Convert to greyscale
    4. Mild unsharp-mask to sharpen text
    """
    # Convert to RGB first
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")

    # Down-scale very large images
    max_dim = settings.max_image_dimension
    if max(img.width, img.height) > max_dim:
        img.thumbnail((max_dim, max_dim), Image.LANCZOS)

    # Greyscale
    img = img.convert("L")

    # Sharpen
    img = img.filter(ImageFilter.UnsharpMask(radius=1, percent=150, threshold=3))

    return img


def run_ocr(image_bytes: bytes, lang: Optional[str] = None) -> str:
    """
    Run Tesseract OCR on raw image bytes.

    Parameters
    ----------
    image_bytes : bytes
        Raw PNG / JPEG / BMP image data.
    lang : str, optional
        Tesseract language string (e.g. "eng+fra"). Defaults to settings.ocr_lang.

    Returns
    -------
    str
        Extracted text, stripped of leading/trailing whitespace.
        Returns empty string on failure (logged as WARNING).
    """
    lang = lang or settings.ocr_lang
    try:
        img = Image.open(io.BytesIO(image_bytes))
        img = _preprocess_image(img)

        config = "--oem 3 --psm 6"  # OEM 3 = LSTM + legacy, PSM 6 = uniform block of text
        text = pytesseract.image_to_string(img, lang=lang, config=config)
        return text.strip()
    except Exception as exc:
        logger.warning("OCR failed: %s", exc)
        return ""


def run_ocr_structured(image_bytes: bytes, lang: Optional[str] = None) -> dict:
    """
    Run OCR and return structured data dict with text, confidence, and word-level boxes.
    Useful for diagram layout analysis.
    """
    lang = lang or settings.ocr_lang
    try:
        img = Image.open(io.BytesIO(image_bytes))
        img = _preprocess_image(img)
        config = "--oem 3 --psm 6"
        data = pytesseract.image_to_data(
            img, lang=lang, config=config,
            output_type=pytesseract.Output.DICT,
        )
        return data
    except Exception as exc:
        logger.warning("Structured OCR failed: %s", exc)
        return {}