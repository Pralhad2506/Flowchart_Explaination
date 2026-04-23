"""
processors/diagram_explainer.py — Convert detected diagram regions into
                                   detailed textual explanations using Groq.

This module builds carefully crafted prompts that instruct the LLM to:
  1. Identify all nodes (start, end, decision, process, data, etc.)
  2. Trace every path including branches, loops, and parallel flows
  3. Describe connector labels and conditions
  4. Produce a human-readable, step-by-step numbered explanation
  5. Also extract / summarise non-diagram text on the same page
"""

from __future__ import annotations

import textwrap
from dataclasses import dataclass, field
from typing import List, Optional

from  app.extractors.diagram_detector import DiagramRegion
from  app.processors.groq_client import GroqClient
from  app.parsers.base_parser import PageContent
from  app.utils.logger import get_logger

logger = get_logger(__name__)

# ── System prompt ─────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = textwrap.dedent("""
You are an expert technical analyst specialising in extracting, interpreting,
and explaining flowcharts, block diagrams, and process diagrams from documents.

Your task is to produce a COMPLETE, DETAILED, STEP-BY-STEP textual explanation
of ANY diagram described to you.  Follow these rules precisely:

1. IDENTIFY every node: start/end terminals, decision diamonds, process
   rectangles, data parallelograms, connectors (circles/off-page), and
   any other shape present.

2. TRACE every execution path from start to end, including:
   - All YES/NO branches from decision nodes
   - All loop-back arrows (with the condition that re-triggers the loop)
   - All parallel or concurrent flows
   - All exit conditions

3. LABEL connector arrows exactly as they appear (e.g. "YES → Step 4").

4. PRESERVE hierarchical relationships: parent boxes, child sub-boxes, layers.

5. FORMAT your explanation as a numbered step list.  Use sub-steps (e.g. 3a, 3b)
   for branches.  Clearly mark decisions as [DECISION], loops as [LOOP], and
   terminations as [END].

6. If the content is NOT a diagram, extract and summarise ALL textual
   information in well-structured prose, preserving the original hierarchy.

7. Never fabricate information.  If a label is unclear, note it explicitly.
   If no diagram is found, say so clearly.

8. Be concise but COMPLETE.  Do NOT omit any step, branch, or condition.
9. If diagram information is incomplete, reconstruct the most logical flow using available labels.
10. If only text is available, treat it as a pseudo-diagram and explain sequentially.
""").strip()


# ── Prompt builders ───────────────────────────────────────────────────────────

def _build_diagram_prompt(region: DiagramRegion, file_name: str) -> str:
    lines = [
        f"FILE: {file_name}",
        f"PAGE/SLIDE: {region.source_page}",
        f"DETECTED DIAGRAM TYPE: {region.diagram_type}",
        f"DETECTION CONFIDENCE: {region.confidence:.0%}",
        "",
    ]

    if region.text_layer.strip():
        lines += [
            "--- TEXT EXTRACTED FROM DOCUMENT LAYER ---",
            region.text_layer[:3000],   # cap to avoid token overflow
            "",
        ]

    if region.ocr_text.strip():
        lines += [
            "--- TEXT EXTRACTED VIA OCR FROM IMAGE ---",
            region.ocr_text[:2000],
            "",
        ]

    if region.node_labels:
        lines += [
            "--- DETECTED NODE / SHAPE LABELS ---",
            "\n".join(f"  • {lbl}" for lbl in region.node_labels[:30]),
            "",
        ]

    if region.connector_labels:
        lines += [
            "--- DETECTED CONNECTOR / ARROW LABELS ---",
            "  " + "  ".join(region.connector_labels[:20]),
            "",
        ]

    lines += [
        "TASK:",
        "Using ALL the information above, provide a complete, step-by-step",
        "explanation of this diagram.  Include every node, every branch,",
        "every loop, and every connector.  Use numbered steps and sub-steps.",
    ]

    return "\n".join(lines)


def _build_prose_prompt(page: PageContent, file_name: str) -> str:
    return textwrap.dedent(f"""
        FILE: {file_name}
        PAGE/SLIDE/SECTION: {page.page_number} — "{page.section_title}"

        --- CONTENT ---
        {page.text[:4000]}

        TASK:
        Extract and reformat the above content into a well-structured,
        readable summary that preserves all information, hierarchy, and order.
        Use appropriate headings, sub-headings, and bullet points.
        If there are any tables, reproduce them in text form clearly.
    """).strip()


# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class ExplainedContent:
    """Output from the LLM for one page / diagram region."""
    page_number: int
    section_title: str
    content_type: str          # "diagram" | "prose" | "error"
    explanation: str           # The LLM-generated explanation
    diagram_type: str = ""
    confidence: float = 0.0
    raw_error: str = ""


# ── Main explainer class ──────────────────────────────────────────────────────

class DiagramExplainer:
    """
    Orchestrate LLM calls to explain diagram regions and summarise prose pages.
    Intended to be called with an already-open GroqClient.
    """

    def __init__(self, groq: GroqClient, file_name: str) -> None:
        self.groq = groq
        self.file_name = file_name

    async def explain_diagram(self, region: DiagramRegion) -> ExplainedContent:
        """Generate a step-by-step explanation for a diagram region."""
        prompt = _build_diagram_prompt(region, self.file_name)
        try:
            explanation = await self.groq.ask(prompt, system_prompt=_SYSTEM_PROMPT)
            logger.debug(
                "Diagram explained — page %d, type=%s, len=%d",
                region.source_page, region.diagram_type, len(explanation),
            )
            return ExplainedContent(
                page_number=region.source_page,
                section_title=f"Diagram — Page {region.source_page}",
                content_type="diagram",
                explanation=explanation,
                diagram_type=region.diagram_type,
                confidence=region.confidence,
            )
        except Exception as exc:
            logger.error(
                "LLM explanation failed for page %d in %s: %s",
                region.source_page, self.file_name, exc,
            )
            return ExplainedContent(
                page_number=region.source_page,
                section_title=f"Diagram — Page {region.source_page}",
                content_type="error",
                explanation=(
                    f"[ERROR: Could not generate explanation for diagram on page "
                    f"{region.source_page}. Raw OCR text:\n{region.ocr_text[:500]}]"
                ),
                raw_error=str(exc),
            )

    async def explain_prose(self, page: PageContent) -> ExplainedContent:
        """Reformat and summarise non-diagram page content."""
        if not page.text.strip():
            return ExplainedContent(
                page_number=page.page_number,
                section_title=page.section_title or f"Page {page.page_number}",
                content_type="prose",
                explanation="[Page contains no extractable text content]",
            )

        prompt = _build_prose_prompt(page, self.file_name)
        try:
            explanation = await self.groq.ask(prompt, system_prompt=_SYSTEM_PROMPT)
            logger.debug(
                "Prose explained — page %d, len=%d",
                page.page_number, len(explanation),
            )
            return ExplainedContent(
                page_number=page.page_number,
                section_title=page.section_title or f"Page {page.page_number}",
                content_type="prose",
                explanation=explanation,
            )
        except Exception as exc:
            logger.error(
                "LLM prose extraction failed for page %d in %s: %s",
                page.page_number, self.file_name, exc,
            )
            # Fall back to raw text
            return ExplainedContent(
                page_number=page.page_number,
                section_title=page.section_title or f"Page {page.page_number}",
                content_type="prose",
                explanation=page.text,
                raw_error=str(exc),
            )