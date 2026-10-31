"""PDF pages → images, for OCR.

PyMuPDF rather than `pdf2image`: it rasterises in-process with no system binary, so
OCR does not require a `brew install poppler` that will be missing on someone else's
machine and will fail at upload time rather than at install time.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

#: 300 DPI is the usual floor for OCR on printed text. Below ~200 small type and
#: subscripts start dropping out; above 400 the file grows without accuracy following.
DEFAULT_DPI = 300


class RasterizeUnavailable(Exception):
    """PyMuPDF is not installed."""


def page_images(
    data: bytes, *, dpi: int = DEFAULT_DPI, max_pages: int | None = None
) -> list[bytes]:
    """Render each page to PNG bytes, in page order."""
    try:
        import pymupdf
    except ImportError as exc:  # pragma: no cover - exercised by config, not tests
        raise RasterizeUnavailable(
            "OCR needs PyMuPDF: install the 'ocr' extra (uv sync --extra ocr)"
        ) from exc

    images: list[bytes] = []
    with pymupdf.open(stream=data, filetype="pdf") as document:
        count = document.page_count if max_pages is None else min(document.page_count, max_pages)
        for index in range(count):
            page = document.load_page(index)
            pixmap = page.get_pixmap(dpi=dpi)
            images.append(pixmap.tobytes("png"))
    log.info("rasterised pages for OCR", extra={"pages": len(images), "dpi": dpi})
    return images


def page_count(data: bytes) -> int:
    try:
        import pymupdf
    except ImportError as exc:  # pragma: no cover
        raise RasterizeUnavailable("PyMuPDF is not installed") from exc
    with pymupdf.open(stream=data, filetype="pdf") as document:
        return document.page_count
