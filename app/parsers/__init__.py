"""
parsers/__init__.py — Parser registry and factory function.

Usage:
    from app.parsers import get_parser
    parser = get_parser(Path("report.pdf"))
    pages = parser.parse()
"""

from pathlib import Path
from typing import Dict, Type

from app.parsers.base_parser import BaseParser, ParserError
from app.parsers.pdf_parser import PdfParser
from app.parsers.pptx_parser import PptxParser
from app.parsers.docx_parser import DocxParser
from app.parsers.xlsx_parser import XlsxParser

_REGISTRY: Dict[str, Type[BaseParser]] = {
    ".pdf":  PdfParser,
    ".ppt":  PptxParser,
    ".pptx": PptxParser,
    ".doc":  DocxParser,
    ".docx": DocxParser,
    ".xls":  XlsxParser,
    ".xlsx": XlsxParser,
}


def get_parser(file_path: Path) -> BaseParser:
    """
    Return the appropriate parser instance for *file_path*.

    Raises
    ------
    ParserError
        If the file extension is not supported.
    """
    ext = file_path.suffix.lower()
    parser_cls = _REGISTRY.get(ext)
    if parser_cls is None:
        raise ParserError(f"No parser available for extension '{ext}'")
    return parser_cls(file_path)


__all__ = ["get_parser", "BaseParser", "ParserError"]