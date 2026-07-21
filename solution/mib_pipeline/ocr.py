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

# Two complementary passes at 300 dpi: PSM 6 (block mode) reads adjudicator
# notes and dense paragraphs better, PSM 4 (column mode) reads labeled field
# lines better. The parser consumes the union of lines and cherry-picks
# whichever pass rendered each line legibly. A hard per-page timeout is
# essential: on some noisy pages Tesseract's component analysis blows up at
# high DPI and a single page can otherwise grind for minutes.
PASSES = [(300, 6), (300, 4)]
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


WEAK_YIELD_WORDS = 60
BINARIZE_THRESHOLD = 120


def _ocr_binarized(page, dpi: int, psm: int) -> List[str]:
    """OCR with hard black/white thresholding — recovers faint low-contrast
    scans that plain OCR reads as noise."""
    try:
        mat = fitz.Matrix(dpi / 72.0, dpi / 72.0)
        pix = page.get_pixmap(matrix=mat, colorspace=fitz.csGRAY)
        img = Image.open(io.BytesIO(pix.tobytes("png")))
        img = img.point(lambda v: 255 if v > BINARIZE_THRESHOLD else 0)
        text = pytesseract.image_to_string(
            img, config=f"--oem 1 --psm {psm}", timeout=PAGE_TIMEOUT_S
        )
    except Exception:
        return []
    return [s.strip() for s in text.splitlines() if s.strip()]


def ocr_page_lines(page) -> List[str]:
    """OCR a page with both segmentation passes and return the union of their
    lines (deduplicated, pass order preserved). Weak-yield pages get an extra
    binarized pass — faint scans often only become legible after hard
    thresholding. Falls back to a cheap low-DPI pass if everything is empty."""
    if not _OCR_OK:
        return []
    lines: List[str] = []
    seen = set()

    def _absorb(new):
        for ln in new:
            if ln not in seen:
                seen.add(ln)
                lines.append(ln)

    for dpi, psm in PASSES:
        _absorb(_ocr_once(page, dpi, psm))
    if sum(len(l.split()) for l in lines) < WEAK_YIELD_WORDS:
        _absorb(_ocr_binarized(page, 300, 4))
    if len(lines) < MIN_USEFUL_LINES:
        alt = _ocr_once(page, *FALLBACK)
        if len(alt) > len(lines):
            return alt
    return lines


def make_ocr_fn():
    def _fn(page):
        return ocr_page_lines(page)
    return _fn
