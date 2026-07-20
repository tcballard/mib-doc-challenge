"""OCR fallback for rasterized / scanned pages.

Only pages that carry an image but no usable text layer are OCR'd, so digital
packets stay fast. OCR output is treated as *visible* document evidence (it is
literally the rendered page), unlike the hidden PDF text layer.
"""
from __future__ import annotations

import io
from typing import List

try:
    import fitz
    import pytesseract
    from PIL import Image
    _OCR_OK = True
except Exception:  # pragma: no cover
    _OCR_OK = False

DEFAULT_DPI = 200


def ocr_available() -> bool:
    return _OCR_OK


def ocr_page_lines(page, dpi: int = DEFAULT_DPI) -> List[str]:
    """Render a PDF page to a bitmap and OCR it into text lines."""
    if not _OCR_OK:
        return []
    try:
        # Undo page rotation so text is upright for the OCR engine.
        mat = fitz.Matrix(dpi / 72.0, dpi / 72.0)
        pix = page.get_pixmap(matrix=mat, colorspace=fitz.csGRAY)
        img = Image.open(io.BytesIO(pix.tobytes("png")))
        text = pytesseract.image_to_string(img, config="--oem 1 --psm 4")
    except Exception:
        return []
    lines = []
    for raw in text.splitlines():
        s = raw.strip()
        if s:
            lines.append(s)
    return lines


def make_ocr_fn(dpi: int = DEFAULT_DPI):
    def _fn(page):
        return ocr_page_lines(page, dpi=dpi)
    return _fn
