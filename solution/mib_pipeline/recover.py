"""Targeted whitelist re-OCR for fields still missing after the main parse.

Whole-page OCR must consider every glyph shape; constraining the character set
to a field's alphabet (digits for SPN ids and dates, upper-case for species
codes) makes typed tokens legible in noise that defeats general OCR. This stage
runs only for packets that still lack those fields, so its cost is bounded.
"""
from __future__ import annotations

import io
import re
from typing import List, Optional

try:
    import fitz
    import pytesseract
    from PIL import Image
    _OK = True
except Exception:  # pragma: no cover
    _OK = False

from .parse import Record, DATE_RE, _valid_date
from .vocab import SPECIES, correct as vocab_correct

SPN_RE = re.compile(r"SPN-?\s?(\d{4})")
TIMEOUT_S = 10


def _whitelist_ocr(img, whitelist: str) -> str:
    try:
        return pytesseract.image_to_string(
            img,
            config=f'--oem 1 --psm 6 -c tessedit_char_whitelist="{whitelist}"',
            timeout=TIMEOUT_S,
        )
    except Exception:
        return ""


def _render_binarized(page, dpi: int = 300, threshold: int = 120):
    # Reuse the OCR module's render path so orientation repair and deskew apply
    # here too — whitelist re-OCR was previously blind on rotated pages.
    from .ocr import _render, _detect_orientation
    img = _render(page, dpi, orient=_detect_orientation(page))
    return img.point(lambda v: 255 if v > threshold else 0)


def recover_missing_fields(rec: Record, pdf_path: str) -> None:
    """Fill still-missing sponsor_id / arrival_date / species_code via
    whitelist OCR over the packet's image pages. Mutates ``rec`` in place."""
    if not _OK:
        return
    need_sponsor = not re.match(r"SPN-\d{4}$", rec.sponsor_id or "")
    need_date = not (rec.arrival_date and _valid_date(rec.arrival_date))
    need_species = not rec.species_code
    if not (need_sponsor or need_date or need_species):
        return
    try:
        doc = fitz.open(pdf_path)
    except Exception:
        return
    try:
        for pno in range(doc.page_count):
            page = doc[pno]
            if not page.get_images():
                continue
            img = None
            if need_sponsor or need_date:
                img = _render_binarized(page)
                text = _whitelist_ocr(img, "SPN-0123456789 ")
                if need_sponsor:
                    m = SPN_RE.search(text)
                    if m:
                        rec.sponsor_id = f"SPN-{m.group(1)}"
                        rec.field_sources["sponsor_id"] = "whitelist_ocr"
                        need_sponsor = False
                if need_date:
                    for dm in DATE_RE.finditer(text):
                        if _valid_date(dm.group(1)):
                            rec.arrival_date = dm.group(1)
                            rec.field_sources["arrival_date"] = "whitelist_ocr"
                            need_date = False
                            break
            if need_species:
                if img is None:
                    img = _render_binarized(page)
                text = _whitelist_ocr(img, "ABCDEFGHIJKLMNOPQRSTUVWXYZ_ ")
                for token in re.findall(r"[A-Z][A-Z_]{5,}", text):
                    hit = vocab_correct(token, SPECIES, max_ratio=0.3)
                    if hit:
                        rec.species_code = hit
                        rec.field_sources["species_code"] = "whitelist_ocr"
                        need_species = False
                        break
            if not (need_sponsor or need_date or need_species):
                break
    finally:
        doc.close()
