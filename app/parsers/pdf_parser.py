"""
parsers/pdf_parser.py — Extract text and rasterised images from PDF files.

Uses PyMuPDF (fitz) for fast, accurate text extraction and page rendering.
Each PDF page becomes one PageContent.  Embedded images are also extracted.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import List

import fitz  # PyMuPDF

from app.parsers.base_parser import BaseParser, ImageBlock, PageContent, ParserError
from app.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)

# Keywords that hint at a flowchart / diagram being present in the text layer
_DIAGRAM_KEYWORDS = {
    "start", "end", "begin", "stop", "yes", "no", "decision",
    "process", "input", "output", "if", "then", "else", "loop",
    "repeat", "flow", "step", "→", "->", "⇒", "condition",
    "branch", "merge", "join", "fork", "gateway", "return",
}


def _page_looks_like_diagram(text: str, image_count: int) -> bool:
    """Heuristic: page is likely diagram-heavy."""
    if image_count >= 1 and len(text.strip()) < 200:
        return True
    lower = text.lower()
    hits = sum(1 for kw in _DIAGRAM_KEYWORDS if kw in lower)
    return hits >= settings.diagram_keyword_threshold


class PdfParser(BaseParser):
    """Parse a PDF file into PageContent objects."""

    def parse(self) -> List[PageContent]:
        try:
            doc = fitz.open(str(self.file_path))
        except Exception as exc:
            raise ParserError(f"Cannot open PDF {self.file_name}: {exc}") from exc

        pages: List[PageContent] = []

        for page_idx in range(len(doc)):
            try:
                page = doc[page_idx]
                page_number = page_idx + 1

                # ── Text extraction ───────────────────────────────────────────
                text = page.get_text("text")  # plain text, reading order

                # ── Image extraction ──────────────────────────────────────────
                image_blocks: List[ImageBlock] = []

                # 1. Embedded image objects
                for img_info in page.get_images(full=True):
                    xref = img_info[0]
                    try:
                        base_image = doc.extract_image(xref)
                        img_bytes = base_image["image"]
                        ext = base_image.get("ext", "png")
                        mime = f"image/{ext}" if ext != "jpg" else "image/jpeg"
                        image_blocks.append(
                            ImageBlock(
                                image_bytes=img_bytes,
                                mime_type=mime,
                                page_number=page_number,
                            )
                        )
                    except Exception as img_exc:
                        logger.warning(
                            "Could not extract image xref=%d on page %d of %s: %s",
                            xref, page_number, self.file_name, img_exc,
                        )

                # 2. Render the full page as PNG (for OCR / diagram analysis)
                mat = fitz.Matrix(settings.pdf_dpi / 72, settings.pdf_dpi / 72)
                clip = page.rect
                pix = page.get_pixmap(matrix=mat, clip=clip, alpha=False)
                page_png = pix.tobytes("png")
                image_blocks.append(
                    ImageBlock(
                        image_bytes=page_png,
                        mime_type="image/png",
                        page_number=page_number,
                        caption=f"Full page render — page {page_number}",
                    )
                )

                # ── Section title: try to grab the first bold/large text ──────
                section_title = _extract_page_title(page)

                is_diagram = _page_looks_like_diagram(text, len(image_blocks) - 1)

                pages.append(
                    PageContent(
                        page_number=page_number,
                        text=text,
                        images=image_blocks,
                        section_title=section_title,
                        is_diagram_page=is_diagram,
                        raw_metadata={"source": "pdf", "total_pages": len(doc)},
                    )
                )

            except Exception as page_exc:
                logger.error(
                    "Error processing page %d of %s: %s",
                    page_idx + 1, self.file_name, page_exc,
                )
                pages.append(
                    PageContent(
                        page_number=page_idx + 1,
                        text=f"[ERROR extracting page {page_idx + 1}: {page_exc}]",
                        raw_metadata={"error": str(page_exc)},
                    )
                )

        doc.close()
        logger.info("PDF parsed: %s — %d page(s)", self.file_name, len(pages))
        return pages


def _extract_page_title(page: fitz.Page) -> str:
    """
    Attempt to extract a title from the page by finding the largest font size.
    Falls back to an empty string if nothing useful is found.
    """
    try:
        blocks = page.get_text("dict")["blocks"]
        best_size = 0.0
        best_text = ""
        for block in blocks:
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    if span["size"] > best_size and len(span["text"].strip()) > 3:
                        best_size = span["size"]
                        best_text = span["text"].strip()
        return best_text[:120]
    except Exception:
        return ""