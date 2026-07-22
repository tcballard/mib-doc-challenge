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


def _estimate_skew(img) -> float:
    """Estimate page skew by maximizing dark-pixel row-profile variance over
    candidate angles (cheap, runs on a small thumbnail)."""
    try:
        import numpy as np
    except Exception:
        return 0.0
    thumb = img.resize((img.width // 4 or 1, img.height // 4 or 1))
    best_angle, best_score = 0.0, -1.0
    for angle in (-12, -9, -6, -4, -2, 0, 2, 4, 6, 9, 12):
        rot = thumb.rotate(angle, expand=False, fillcolor=255)
        arr = np.asarray(rot, dtype=np.uint8)
        dark = (arr < 128).sum(axis=1).astype(float)
        score = dark.var()
        if score > best_score:
            best_score, best_angle = score, angle
    return float(best_angle)


def _render(page, dpi: int):
    mat = fitz.Matrix(dpi / 72.0, dpi / 72.0)
    pix = page.get_pixmap(matrix=mat, colorspace=fitz.csGRAY)
    img = Image.open(io.BytesIO(pix.tobytes("png")))
    # Deskew: rotated scans degrade Tesseract sharply; straighten when the
    # estimated skew is meaningful.
    angle = _estimate_skew(img)
    if abs(angle) >= 2:
        img = img.rotate(angle, expand=True, fillcolor=255)
    return img


def _ocr_once(page, dpi: int, psm: int) -> List[str]:
    try:
        img = _render(page, dpi)
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
        img = _render(page, dpi)
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


# --- Escalation layer -------------------------------------------------------
# For packets still deficient after the standard passes, spend more budget:
# extra binarization thresholds (different scans respond to different cutoffs),
# autocontrast, and a higher-DPI pass. Bounded per page by the same timeout.

ESCALATION_THRESHOLDS = (100, 140)


def _ocr_variant(page, dpi: int, psm: int, threshold=None, autocontrast=False) -> List[str]:
    try:
        img = _render(page, dpi)
        if autocontrast:
            from PIL import ImageOps
            img = ImageOps.autocontrast(img, cutoff=2)
        if threshold is not None:
            img = img.point(lambda v: 255 if v > threshold else 0)
        text = pytesseract.image_to_string(
            img, config=f"--oem 1 --psm {psm}", timeout=PAGE_TIMEOUT_S
        )
    except Exception:
        return []
    return [s.strip() for s in text.splitlines() if s.strip()]


def _fix_orientation(img):
    """Detect and undo 90/180/270-degree scan orientation via Tesseract OSD."""
    try:
        osd = pytesseract.image_to_osd(img, timeout=8)
        for line in osd.splitlines():
            if line.startswith("Rotate:"):
                rot = int(line.split(":")[1])
                if rot:
                    return img.rotate(-rot, expand=True, fillcolor=255)
    except Exception:
        pass
    return img


def _escalation_variants(page):
    """Ordered ladder of increasingly aggressive read attempts for a page the
    cheap layers couldn't crack. Yields line-lists."""
    from PIL import ImageOps, ImageFilter
    try:
        base = _render(page, 300)
    except Exception:
        return
    base = _fix_orientation(base)

    def run(img, psm):
        try:
            text = pytesseract.image_to_string(
                img, config=f"--oem 1 --psm {psm}", timeout=PAGE_TIMEOUT_S)
            return [s.strip() for s in text.splitlines() if s.strip()]
        except Exception:
            return []

    yield run(base, 4)
    yield run(base, 6)
    for th in ESCALATION_THRESHOLDS:
        yield run(base.point(lambda v, t=th: 255 if v > t else 0), 4)
    yield run(ImageOps.autocontrast(base, cutoff=2), 4)
    # Sparse-text mode: recovers free-floating words when layout analysis fails.
    yield run(base, 11)
    # Denoise then binarize: beats salt-and-pepper speckle.
    den = base.filter(ImageFilter.MedianFilter(3))
    yield run(ImageOps.autocontrast(den, cutoff=2).point(lambda v: 255 if v > 130 else 0), 4)
    # Upscale for small/blurry type.
    up = base.resize((base.width * 2, base.height * 2))
    yield run(up.point(lambda v: 255 if v > BINARIZE_THRESHOLD else 0), 6)


def make_escalated_ocr_fn():
    """OCR function for the escalation pass: works down a ladder of variants,
    stopping once two consecutive variants contribute nothing new — spend the
    time a page deserves, and no more."""
    def _fn(page):
        lines: List[str] = []
        seen = set()
        dry = 0
        for variant_lines in _escalation_variants(page):
            new = [ln for ln in variant_lines if ln not in seen]
            if new:
                dry = 0
                for ln in new:
                    seen.add(ln)
                    lines.append(ln)
            else:
                dry += 1
                if dry >= 2 and lines:
                    break
        return lines
    return _fn
