"""OCR fallback for rasterized / scanned pages.

Only pages that carry an image but no usable text layer are OCR'd, so digital
packets stay fast. OCR output is treated as *visible* document evidence (it is
literally the rendered page), unlike the hidden PDF text layer.
"""
from __future__ import annotations

import hashlib
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
    # Sweep out to +/-24: pages skewed 15-20 degrees occur in this corpus and
    # sat outside the old +/-12 window, so they were left crooked and read as
    # unclassifiable. The extra angles are thumbnail rotations, which is why
    # widening the range is affordable where widening the DPI ladder was not.
    for angle in (-24, -20, -16, -12, -9, -6, -4, -2, 0,
                  2, 4, 6, 9, 12, 16, 20, 24):
        rot = thumb.rotate(angle, expand=False, fillcolor=255)
        arr = np.asarray(rot, dtype=np.uint8)
        dark = (arr < 128).sum(axis=1).astype(float)
        score = dark.var()
        if score > best_score:
            best_score, best_angle = score, angle
    return float(best_angle)


# Per-document render/OSD memo. The base pass, escalation ladder, and both
# recovery cascades each render and orientation-check the same pages; profiling
# found 455/667 pixmap renders and 146/251 OSD calls were exact repeats
# (~15% of OCR wall-clock). One document is cached at a time, so memory stays
# bounded to a single packet's pages and resets when the next packet arrives.
_MEMO = {"name": None, "renders": {}, "osd": {}, "texts": {}}


def _memo_for(page):
    name = getattr(getattr(page, "parent", None), "name", "") or ""
    if _MEMO["name"] != name:
        _MEMO["name"] = name
        _MEMO["renders"] = {}
        _MEMO["osd"] = {}
        _MEMO["texts"] = {}
    return _MEMO


def ocr_text(img, config, timeout):
    """Run tesseract, reusing the answer for an image+config already read.

    The escalation ladder and the risk-flag recovery cascade both threshold the
    same memoized render at the same levels, so on escalated packets 4-8 calls
    per packet were byte-for-byte repeats (~1.7s each packet, 5% of runtime).
    Keyed on the raw pixel bytes, so a hit is identical input by construction.
    Failures are never cached: tesseract timeouts are nondeterministic and must
    stay retryable. The key deliberately omits the timeout, which differs by two
    seconds between the two callers — a hit can only occur for a call that
    already finished, and no profiled call came within 10s of either limit.
    """
    key = (hashlib.md5(img.tobytes()).hexdigest(), img.size, config)
    texts = _MEMO["texts"]
    if key in texts:
        return texts[key]
    text = pytesseract.image_to_string(img, config=config, timeout=timeout)
    texts[key] = text
    return text


def _pix_to_image(pix):
    # Raw grayscale buffer copy: pixel-identical to the previous PNG
    # encode/decode roundtrip at a fraction of the cost.
    return Image.frombytes("L", (pix.width, pix.height), pix.samples)


def _render(page, dpi: int, orient: int = 0):
    memo = _memo_for(page)
    key = (page.number, dpi, orient)
    cached = memo["renders"].get(key)
    if cached is not None:
        return cached
    mat = fitz.Matrix(dpi / 72.0, dpi / 72.0)
    pix = page.get_pixmap(matrix=mat, colorspace=fitz.csGRAY)
    img = _pix_to_image(pix)
    # Quadrant orientation first (15% of scanned pages in this corpus are
    # rotated 90/180/270 and defeat every downstream pass), then fine deskew.
    if orient:
        img = img.rotate(-orient, expand=True, fillcolor=255)
    angle = _estimate_skew(img)
    if abs(angle) >= 2:
        img = img.rotate(angle, expand=True, fillcolor=255)
    memo["renders"][key] = img
    return img


MIN_OSD_CONFIDENCE = 2.0


def _detect_orientation(page) -> int:
    """OSD quadrant-rotation detection on a cheap low-DPI render. Returns the
    clockwise degrees the content must be rotated back by (0/90/180/270).

    Only acted on when OSD's own confidence clears a floor — low-confidence
    detections on noisy upright pages otherwise rotate good pages into bad
    ones (measured as a small across-the-board extraction dip)."""
    memo = _memo_for(page)
    if page.number in memo["osd"]:
        return memo["osd"][page.number]
    result = 0
    try:
        pix = page.get_pixmap(matrix=fitz.Matrix(150 / 72.0, 150 / 72.0), colorspace=fitz.csGRAY)
        img = _pix_to_image(pix)
        osd = pytesseract.image_to_osd(img, timeout=8)
        rot, conf = 0, 0.0
        for line in osd.splitlines():
            if line.startswith("Rotate:"):
                rot = int(line.split(":")[1])
            elif line.startswith("Orientation confidence:"):
                conf = float(line.split(":")[1])
        if rot and conf >= MIN_OSD_CONFIDENCE:
            result = rot
    except Exception:
        pass
    memo["osd"][page.number] = result
    return result


FORCED_QUADRANTS = (90, 270)


def _resolve_orientation(page, dpi: int, psm: int, base_legibility: int = 0):
    """Decide a page's quadrant rotation when the upright render read poorly.

    OSD is asked first, but on this corpus Tesseract's orientation confidence
    almost never clears MIN_OSD_CONFIDENCE and half the calls abort outright
    ("Too few characters"), so `_detect_orientation` alone leaves quadrant
    rotation effectively unhandled. The fallback simply tries the two
    quadrants that occur (180 and mirroring were measured to recover nothing)
    and keeps one only if it turns an illegible page into a legible one.

    Both halves of that are document properties: the trigger is an upright
    render that read weakly, and the acceptance test is a strict improvement
    in legibility over that upright read, so a page that already reads well is
    never rotated. Returns (orient, lines) so an accepted trial's text is
    reused instead of being OCR'd a second time.

    The trigger is deliberately not "read *nothing*". Vertically typeset text
    does not come back empty from an upright pass -- it comes back as a
    trickle of stray characters that scores above zero -- so a zero-legibility
    gate skipped exactly the rotated pages it was meant to catch. Comparing
    against the upright score rather than against zero is what makes the wider
    trigger safe.
    """
    orient = _detect_orientation(page)
    if orient:
        return orient, None
    best_lines, best_quadrant, best_score = None, 0, base_legibility
    for quadrant in FORCED_QUADRANTS:
        lines = _ocr_once(page, dpi, psm, orient=quadrant)
        score = _legibility(lines)
        if score > best_score:
            best_lines, best_quadrant, best_score = lines, quadrant, score
    if best_quadrant:
        _memo_for(page)["osd"][page.number] = best_quadrant
        return best_quadrant, best_lines
    return 0, None


def _ocr_once(page, dpi: int, psm: int, orient: int = 0) -> List[str]:
    try:
        img = _render(page, dpi, orient=orient)
        text = pytesseract.image_to_string(
            img, config=f"--oem 1 --psm {psm}", timeout=PAGE_TIMEOUT_S
        )
    except Exception:
        return []
    return [s.strip() for s in text.splitlines() if s.strip()]


WEAK_YIELD_WORDS = 60
# Legibility at or above which a page counts as read. Shared by the binarize
# rung below and the quadrant trigger, so "weak" means one thing everywhere.
WEAK_LEGIBILITY = 3
BINARIZE_THRESHOLD = 120


def _ocr_binarized(page, dpi: int, psm: int, orient: int = 0) -> List[str]:
    """OCR with hard black/white thresholding — recovers faint low-contrast
    scans that plain OCR reads as noise."""
    try:
        img = _render(page, dpi, orient=orient)
        img = img.point(lambda v: 255 if v > BINARIZE_THRESHOLD else 0)
        text = pytesseract.image_to_string(
            img, config=f"--oem 1 --psm {psm}", timeout=PAGE_TIMEOUT_S
        )
    except Exception:
        return []
    return [s.strip() for s in text.splitlines() if s.strip()]


import re as _re

_LEGIBLE_PATTERNS = _re.compile(
    r"SPN-\d|MIB-\d|\d{4}-\d{2}-\d{2}|XW-[12]|DIP-1|MED-3|TRANSIT-7|"
    r"Case ID|Applicant|Species|Home World|Fee Status|Observed|Finding|Sponsor",
    _re.I,
)


def _legibility(lines: List[str]) -> int:
    """Count of lines carrying a recognizable typed pattern or field label —
    a quality gate; raw word counts pass confident garbage."""
    return sum(1 for ln in lines if _LEGIBLE_PATTERNS.search(ln))


def ocr_page_lines(page) -> List[str]:
    """OCR a page: first segmentation pass upright, orientation detection only
    when that pass reads nothing legible (OSD costs ~0.4s/page and detected
    zero rotations across a 251-call profile — a rotated page already fails
    the upright pass, so lazy OSD is cost-neutral for it), then the second
    pass, then a binarized pass when yield is weak in *quantity or quality*.
    Falls back to a cheap low-DPI pass if everything is empty."""
    if not _OCR_OK:
        return []
    lines: List[str] = []
    seen = set()

    def _absorb(new):
        for ln in new:
            if ln not in seen:
                seen.add(ln)
                lines.append(ln)

    first_dpi, first_psm = PASSES[0]
    upright = _ocr_once(page, first_dpi, first_psm, orient=0)
    orient = 0
    rotated = None
    base_legibility = _legibility(upright)
    if base_legibility < WEAK_LEGIBILITY:
        orient, rotated = _resolve_orientation(
            page, first_dpi, first_psm, base_legibility)
    if orient:
        _absorb(rotated if rotated is not None
                else _ocr_once(page, first_dpi, first_psm, orient=orient))
    else:
        _absorb(upright)
    for dpi, psm in PASSES[1:]:
        _absorb(_ocr_once(page, dpi, psm, orient=orient))
    if (sum(len(l.split()) for l in lines) < WEAK_YIELD_WORDS
            or _legibility(lines) < WEAK_LEGIBILITY):
        _absorb(_ocr_binarized(page, 300, 4, orient=orient))
    if len(lines) < MIN_USEFUL_LINES:
        alt = _ocr_once(page, *FALLBACK, orient=orient)
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


def _escalation_variants(page, skip_segment: bool = False, deep: bool = True):
    """Ordered ladder of increasingly aggressive read attempts for a page the
    cheap layers couldn't crack. Yields (family, line-list) so the stopping
    rule can distinguish technique families.

    ``skip_segment=True`` when the caller seeds the base pass's lines: the two
    segment rungs are byte-identical re-runs of that pass (same render, same
    psm) and were measured as pure duplicate cost."""
    from PIL import ImageOps, ImageFilter
    orient = _detect_orientation(page)
    try:
        base = _render(page, 300, orient=orient)
    except Exception:
        return

    def run(img, psm):
        try:
            text = ocr_text(img, f"--oem 1 --psm {psm}", PAGE_TIMEOUT_S)
            return [s.strip() for s in text.splitlines() if s.strip()]
        except Exception:
            return []

    # Original rung set and order preserved: a reorder/prune attempt measured
    # net-negative on the 76 known escalation-win packets (9 fixed / 15 broken
    # — line arrival order feeds the merge, and the "zero-outcome" attribution
    # sample undersampled the winners). New depth is APPENDED instead: added
    # lines land after the proven ones, so they can only fill, never displace.
    if not skip_segment:
        # Only reachable when the base pass wasn't seeded (direct callers in
        # tests/experiments); the pipeline always seeds.
        yield "segment", run(base, 4)
        yield "segment", run(base, 6)

    def thr(t):
        return base.point(lambda v, _t=t: 255 if v > _t else 0)

    for th in ESCALATION_THRESHOLDS:
        yield "threshold", run(thr(th), 4)
    for th in ESCALATION_THRESHOLDS:
        yield "threshold6", run(thr(th), 6)
    yield "contrast", run(ImageOps.autocontrast(base, cutoff=2), 4)
    # Sparse-text mode: recovers free-floating words when layout analysis fails.
    yield "sparse", run(base, 11)
    # Denoise then binarize: beats salt-and-pepper speckle.
    den = base.filter(ImageFilter.MedianFilter(3))
    yield "denoise", run(ImageOps.autocontrast(den, cutoff=2).point(lambda v: 255 if v > 130 else 0), 4)
    # Upscale for small/blurry type.
    up = base.resize((base.width * 2, base.height * 2))
    yield "upscale", run(up.point(lambda v: 255 if v > BINARIZE_THRESHOLD else 0), 6)
    # Tier-2 depth: the governor sheds what follows first when pacing over
    # budget. Three further binarization cutoffs and a 400 dpi sparse pass
    # used to live here; measured against the quadrant rungs below they were
    # worth -0.04 for a full second per PDF, so they are gone rather than
    # merely demoted.
    if not deep:
        return
    # Quadrant re-reads. The ladder above varies threshold, contrast and
    # resolution but every rung reads the page at one orientation, so a page
    # whose rotation was missed upstream is unreadable at every rung. These
    # two carry nearly all of the escalation gain; a 400/600 dpi tail
    # trialled alongside them bought a further 0.07 for three times the time
    # and is not included.
    for quadrant in FORCED_QUADRANTS:
        try:
            rotated = _render(page, 300, orient=quadrant)
        except Exception:
            continue
        yield "quadrant%d" % quadrant, run(rotated, 4)
        yield "quadrant%d" % quadrant, run(rotated, 6)


PACKET_ESCALATION_BUDGET_S = 75.0


def make_escalated_ocr_fn(base_pages=None, deep: bool = True):
    """OCR function for the escalation pass: works down a ladder of variants,
    stopping once two consecutive rungs *from different technique families*
    contribute nothing new (adjacent same-family rungs are often redundant with
    each other, not evidence the page is exhausted). A shared per-packet
    deadline bounds the worst case: past it, remaining pages get no escalation
    and the packet ships as a low-confidence review rather than a blown budget.

    ``base_pages`` (extract.Page list from the cheap pass) seeds each page's
    line set so the ladder starts from what the base pass already read instead
    of re-running it: the output still contains the base lines (parsing needs
    the full page text) but the byte-identical segment rungs are skipped.
    """
    import time
    start = time.monotonic()
    base_lines = {}
    if base_pages:
        for bp in base_pages:
            if getattr(bp, "ocr_used", False):
                base_lines[bp.index] = list(bp.visible_lines)

    def _fn(page):
        if time.monotonic() - start > PACKET_ESCALATION_BUDGET_S:
            return ocr_page_lines(page)
        seeded = base_lines.get(page.number)
        lines: List[str] = list(seeded) if seeded else []
        seen = set(lines)
        dry_families: List[str] = []
        for family, variant_lines in _escalation_variants(page, skip_segment=bool(seeded), deep=deep):
            new = [ln for ln in variant_lines if ln not in seen]
            if new:
                dry_families = []
                for ln in new:
                    seen.add(ln)
                    lines.append(ln)
            else:
                dry_families.append(family)
                if len(set(dry_families)) >= 2 and lines:
                    break
            if time.monotonic() - start > PACKET_ESCALATION_BUDGET_S:
                break
        return lines
    return _fn
