"""
extractors/diagram_detector.py — Classify pages/images as diagram vs. prose.

Strategy
--------
1. Keyword heuristics on existing text layer
2. OCR on rendered page images (catches text inside shapes)
3. Layout analysis: high image area + low text = likely diagram
4. Combine signals into a DiagramRegion list

Each DiagramRegion carries:
  - the raw OCR text extracted from the image
  - a list of detected node labels / connector labels
  - a confidence score (0-1)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional

from app.parsers.base_parser import ImageBlock, PageContent
from app.extractors.ocr_engine import run_ocr, run_ocr_structured
from app.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)

# ── Keyword sets ──────────────────────────────────────────────────────────────

_FLOW_KEYWORDS = {
    "start", "end", "begin", "stop", "terminate",
    "yes", "no", "true", "false",
    "decision", "condition", "if", "else", "then", "while", "for",
    "process", "task", "action", "activity",
    "input", "output", "data",
    "loop", "repeat", "retry",
    "flow", "step", "phase", "stage",
    "→", "->", "⟶", "⇒", "⇨",
    "branch", "merge", "fork", "join", "gateway",
    "return", "exit", "continue", "break",
    "connector", "link", "edge", "node",
}

_BLOCK_DIAGRAM_KEYWORDS = {
    "component", "module", "service", "layer", "tier",
    "database", "db", "storage", "cache",
    "api", "interface", "client", "server", "host",
    "system", "subsystem", "dependency", "call",
    "request", "response", "message", "queue",
    "load balancer", "proxy", "router", "firewall",
    "user", "actor", "role",
}

_ARROW_PATTERN = re.compile(r"(?:→|->|⟶|⇒|⇨|<-|←|<→|↔|↑|↓|↕|\|)", re.UNICODE)


@dataclass
class DiagramRegion:
    """A page or image region identified as containing a diagram."""
    source_page: int
    ocr_text: str                          # Raw OCR text from this region
    node_labels: List[str] = field(default_factory=list)  # Shape labels
    connector_labels: List[str] = field(default_factory=list)  # Arrow labels
    diagram_type: str = "flowchart"        # "flowchart" | "block_diagram" | "process"
    confidence: float = 0.0
    image_bytes: Optional[bytes] = None    # The source image for LLM analysis
    text_layer: str = ""                   # Text from the document layer (not OCR)


def _score_text(text: str) -> float:
    """Return a 0-1 confidence that *text* describes a diagram."""
    lower = text.lower()
    words = set(re.findall(r"\b\w+\b", lower))
    flow_hits = len(words & {kw.lower() for kw in _FLOW_KEYWORDS})
    block_hits = len(words & {kw.lower() for kw in _BLOCK_DIAGRAM_KEYWORDS})
    arrow_hits = len(_ARROW_PATTERN.findall(text))

    raw_score = (flow_hits * 2 + block_hits * 1.5 + arrow_hits * 3)
    # Normalise to 0-1 (cap at 15 weighted hits)
    return min(raw_score / 15.0, 1.0)


def _infer_diagram_type(text: str) -> str:
    lower = text.lower()
    flow_hits = sum(1 for kw in _FLOW_KEYWORDS if kw in lower)
    block_hits = sum(1 for kw in _BLOCK_DIAGRAM_KEYWORDS if kw in lower)
    if block_hits > flow_hits:
        return "block_diagram"
    if "process" in lower or "phase" in lower or "stage" in lower:
        return "process_diagram"
    return "flowchart"


def _extract_node_labels(text: str) -> List[str]:
    """Very light extraction: any capitalised phrase ≤ 5 words on its own line."""
    labels = []
    for line in text.splitlines():
        line = line.strip()
        if 2 <= len(line.split()) <= 6 and len(line) <= 60:
            labels.append(line)
    return labels[:30]  # cap to keep prompts reasonable


def detect_diagrams_in_page(page: PageContent) -> List[DiagramRegion]:
    """
    Analyse a PageContent and return zero or more DiagramRegion objects.

    Strategy
    --------
    1. Score the text layer first (fast).
    2. For each embedded image, run OCR and score.
    3. Emit a DiagramRegion for any region scoring above threshold.
    """
    regions: List[DiagramRegion] = []

    # 1. Text-layer analysis (cheap, always done)
    text_score = _score_text(page.text)
    if text_score >= 0.15 or page.is_diagram_page:
        regions.append(
            DiagramRegion(
                source_page=page.page_number,
                ocr_text="",            # No OCR needed if text layer is good
                text_layer=page.text,
                node_labels=_extract_node_labels(page.text),
                diagram_type=_infer_diagram_type(page.text),
                confidence=text_score,
                image_bytes=None,       # Will be filled from page images below
            )
        )

    # 2. OCR on each image (more expensive)
    for img_block in page.images:
        # Skip full-page renders if we already have a good text layer
        if img_block.caption.startswith("Full page render") and regions:
            # Still attach the image bytes for LLM analysis
            regions[0].image_bytes = img_block.image_bytes
            continue

        ocr_text = run_ocr(img_block.image_bytes)
        if not ocr_text or len(ocr_text) < settings.min_diagram_text_length:
            # Even sparse OCR text on an image may still be a diagram
            if page.is_diagram_page:
                ocr_text = ocr_text or "[image content — no readable text]"
            else:
                continue

        ocr_score = _score_text(ocr_text)
        combined_score = max(text_score, ocr_score)

        if combined_score >= 0.1 or page.is_diagram_page:
            all_text = f"{page.text}\n{ocr_text}".strip()
            regions.append(
                DiagramRegion(
                    source_page=page.page_number,
                    ocr_text=ocr_text,
                    text_layer=page.text,
                    node_labels=_extract_node_labels(all_text),
                    connector_labels=[
                        m.group() for m in _ARROW_PATTERN.finditer(all_text)
                    ][:20],
                    diagram_type=_infer_diagram_type(all_text),
                    confidence=combined_score,
                    image_bytes=img_block.image_bytes,
                )
            )

    # De-duplicate: if both text-layer and OCR fired for the same page, merge
    if len(regions) >= 2:
        merged = regions[0]
        for r in regions[1:]:
            if r.ocr_text:
                merged.ocr_text = (merged.ocr_text + "\n" + r.ocr_text).strip()
            if r.image_bytes and not merged.image_bytes:
                merged.image_bytes = r.image_bytes
            merged.node_labels = list(dict.fromkeys(merged.node_labels + r.node_labels))
            merged.confidence = max(merged.confidence, r.confidence)
        regions = [merged]

    return regions


def detect_diagrams_in_pages(pages: List[PageContent]) -> List[DiagramRegion]:
    """Run diagram detection across all pages of a file."""
    all_regions: List[DiagramRegion] = []
    for page in pages:
        try:
            page_regions = detect_diagrams_in_page(page)
            all_regions.extend(page_regions)
            if page_regions:
                logger.debug(
                    "Page %d: %d diagram region(s), type=%s, conf=%.2f",
                    page.page_number,
                    len(page_regions),
                    page_regions[0].diagram_type,
                    page_regions[0].confidence,
                )
        except Exception as exc:
            logger.warning("Diagram detection error on page %d: %s", page.page_number, exc)
    logger.info("Total diagram regions detected: %d", len(all_regions))
    return all_regions