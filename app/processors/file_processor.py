"""
processors/file_processor.py — Orchestrate the full pipeline for a single file.

Pipeline
--------
1. Parse file → List[PageContent]
2. Detect diagram regions in each page
3. For diagram pages: call LLM to explain
4. For prose pages: call LLM to summarise / reformat
5. Return ordered List[ExplainedContent]

This module is called by the async batch processor.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import List, Set, Tuple

from  app.parsers import get_parser, ParserError
from  app.parsers.base_parser import PageContent
from  app.extractors.diagram_detector import detect_diagrams_in_pages, DiagramRegion
from  app.processors.groq_client import GroqClient
from  app.processors.diagram_explainer import DiagramExplainer, ExplainedContent
from  app.utils.logger import get_logger

logger = get_logger(__name__)


class FileProcessor:
    """
    Full processing pipeline for a single document file.

    Usage
    -----
    async with GroqClient() as groq:
        processor = FileProcessor(Path("report.pdf"), groq)
        results = await processor.run()
    """

    def __init__(self, file_path: Path, groq: GroqClient) -> None:
        self.file_path = file_path
        self.file_name = file_path.name
        self.groq = groq

    async def run(self) -> Tuple[str, List[ExplainedContent]]:
        """
        Execute the pipeline.

        Returns
        -------
        Tuple[str, List[ExplainedContent]]
            (file_name, ordered list of explained content)
        """
        logger.info("▶  Starting pipeline for: %s", self.file_name)

        # ── Step 1: Parse ─────────────────────────────────────────────────────
        pages: List[PageContent] = []
        try:
            parser = get_parser(self.file_path)
            pages = parser.parse()
            logger.info("   Parsed %d page(s) from %s", len(pages), self.file_name)
        except ParserError as exc:
            logger.error("   Parse failed for %s: %s", self.file_name, exc)
            return self.file_name, [
                ExplainedContent(
                    page_number=0,
                    section_title="Parse Error",
                    content_type="error",
                    explanation=f"[PARSE ERROR] {exc}",
                    raw_error=str(exc),
                )
            ]
        except Exception as exc:
            logger.error("   Unexpected parse error for %s: %s", self.file_name, exc)
            return self.file_name, [
                ExplainedContent(
                    page_number=0,
                    section_title="Unexpected Error",
                    content_type="error",
                    explanation=f"[UNEXPECTED ERROR] {exc}",
                    raw_error=str(exc),
                )
            ]

        # ── Step 2: Detect diagram regions ────────────────────────────────────
        diagram_regions: List[DiagramRegion] = detect_diagrams_in_pages(pages)
        diagram_pages: Set[int] = {r.source_page for r in diagram_regions}
        logger.info(
            "   Detected %d diagram region(s) in %s", len(diagram_regions), self.file_name
        )

        # ── Step 3: Explain with LLM ──────────────────────────────────────────
        explainer = DiagramExplainer(self.groq, self.file_name)
        results: List[ExplainedContent] = []

        # Build coroutines for all pages preserving order
        tasks = []
        for page in pages:
            region = next((r for r in diagram_regions if r.source_page == page.page_number), None)
            if region:
                tasks.append(("diagram", page.page_number, explainer.explain_diagram(region)))
            else:
                tasks.append(("prose", page.page_number, explainer.explain_prose(page)))

        # Run LLM calls concurrently (but throttle to 3 at a time to avoid rate limits)
        sem = asyncio.Semaphore(3)

        async def _run_with_sem(coro):
            async with sem:
                return await coro

        coroutines = [_run_with_sem(coro) for _, _, coro in tasks]
        explained_list: List[ExplainedContent] = await asyncio.gather(
            *coroutines, return_exceptions=False
        )

        # Re-sort by page number to guarantee document order
        results = sorted(explained_list, key=lambda c: c.page_number)

        logger.info("✔  Completed pipeline for: %s (%d section(s))", self.file_name, len(results))
        return self.file_name, results