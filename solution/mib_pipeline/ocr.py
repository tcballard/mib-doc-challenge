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

# Primary pass: 300 dpi / PSM 6 measurably out-reads 200 dpi / PSM 4 on the
# degraded scans in this corpus. A hard per-page timeout is essential: on some
# noisy pages Tesseract's component analysis blows up at high DPI and a single
# page can otherwise grind for minutes.
PRIMARY = (300, 4)
FALLBACK = (200, 4)
PAGE_TIMEOUT_S = 12
MIN_USEFUL_LINES = 3


def ocr_available() -> bool:
    return _OCR_OK


def _ocr_once(page, dpi: int, psm: int) -> List[str]:
    try:
        mat = fitz.Matrix(dpi / 72.0, dpi / 72.0)
        pix = page.get_pixmap(matrix=mat, colorspace=fitz.csGRAY)
        img = Image.open(io.BytesIO(pix.tobytes("png")))
        text = pytesseract.image_to_string(
            img, config=f"--oem 1 --psm {psm}", timeout=PAGE_TIMEOUT_S
        )
    except Exception:
        return []
    return [s.strip() for s in text.splitlines() if s.strip()]


def ocr_page_lines(page) -> List[str]:
    """OCR a page: high-detail primary pass, cheap fallback if it comes back
    nearly empty (timeout, blow-up, or unreadable at that setting)."""
    if not _OCR_OK:
        return []
    lines = _ocr_once(page, *PRIMARY)
    if len(lines) < MIN_USEFUL_LINES:
        alt = _ocr_once(page, *FALLBACK)
        if len(alt) > len(lines):
            return alt
    return lines


def make_ocr_fn():
    def _fn(page):
        return ocr_page_lines(page)
    return _fn
