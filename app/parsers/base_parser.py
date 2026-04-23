"""
parsers/base_parser.py — Abstract base class for all file parsers.

Each concrete parser must implement `parse()` and return a list of
`PageContent` dataclass instances that carry text, images, and metadata
for a single logical page / slide / sheet.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


@dataclass
class ImageBlock:
    """A rasterised image extracted from a document page."""
    image_bytes: bytes                # Raw PNG/JPEG bytes
    mime_type: str = "image/png"
    page_number: int = 0
    caption: str = ""                 # Alt-text or surrounding label if available


@dataclass
class PageContent:
    """
    Represents the parsed content of one logical page, slide, or sheet.

    Attributes
    ----------
    page_number : int
        1-based index.
    text : str
        All plain text visible on this page, in reading order.
    images : List[ImageBlock]
        Rasterised images / diagrams detected on this page.
    notes : str
        Presenter notes (for PPTX) or cell notes (for XLSX).
    section_title : str
        Slide title, sheet name, or inferred heading.
    is_diagram_page : bool
        Set True when the parser detects this page is likely diagram-heavy
        (e.g. a PPTX slide with only shapes and no body text).
    raw_metadata : dict
        Arbitrary extra parser-specific data.
    """
    page_number: int
    text: str = ""
    images: List[ImageBlock] = field(default_factory=list)
    notes: str = ""
    section_title: str = ""
    is_diagram_page: bool = False
    raw_metadata: dict = field(default_factory=dict)


class BaseParser(abc.ABC):
    """Abstract parser.  Subclass and implement :meth:`parse`."""

    def __init__(self, file_path: Path) -> None:
        self.file_path = file_path
        self.file_name = file_path.name

    @abc.abstractmethod
    def parse(self) -> List[PageContent]:
        """
        Parse the document and return a list of PageContent objects,
        one per page / slide / sheet.

        Raises
        ------
        ParserError
            If the file is corrupted or unreadable.
        """

    def __repr__(self) -> str:  # pragma: no cover
        return f"{self.__class__.__name__}({self.file_name!r})"


class ParserError(Exception):
    """Raised when a file cannot be parsed."""