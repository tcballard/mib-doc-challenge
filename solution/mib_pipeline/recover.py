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


_LABEL_TARGETS = {
    "sponsor": ("sponsor_id", "SPN-0123456789 "),
    "arrival": ("arrival_date", "-0123456789 "),
    "applicant": ("applicant_name", None),
}


def _roi_reocr(page, img, want_fields):
    """Label-anchored region re-OCR: find a field label's word box via
    image_to_data, crop the value region to its right, upscale 3x, and read it
    with a single-line pass (whitelisted for typed fields). The precision
    version of whole-page whitelist salvage."""
    from .vocab import _canon, _edit_distance
    out = {}
    try:
        data = pytesseract.image_to_data(
            img, config="--oem 1 --psm 6", timeout=TIMEOUT_S,
            output_type=pytesseract.Output.DICT)
    except Exception:
        return out
    n = len(data.get("text", []))
    for i in range(n):
        word = _canon(data["text"][i] or "")
        if len(word) < 5:
            continue
        for anchor, (fld, whitelist) in _LABEL_TARGETS.items():
            if fld not in want_fields or fld in out:
                continue
            if _edit_distance(word, anchor, cap=3) > 2:
                continue
            x, y, w, h = (data["left"][i], data["top"][i],
                          data["width"][i], data["height"][i])
            box = (x + w, max(0, y - 6), min(img.width, x + w + int(img.width * 0.55)),
                   min(img.height, y + h + 8))
            if box[2] - box[0] < 20 or box[3] - box[1] < 8:
                continue
            crop = img.crop(box)
            crop = crop.resize((crop.width * 3, crop.height * 3))
            cfg = "--oem 1 --psm 7"
            if whitelist:
                cfg += f' -c tessedit_char_whitelist="{whitelist}"'
            try:
                val = pytesseract.image_to_string(crop, config=cfg, timeout=TIMEOUT_S).strip()
            except Exception:
                continue
            if val:
                out[fld] = val
    return out


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
            # Precision stage: label-anchored region re-OCR for what remains.
            if need_sponsor or need_date:
                if img is None:
                    img = _render_binarized(page)
                want = set()
                if need_sponsor:
                    want.add("sponsor_id")
                if need_date:
                    want.add("arrival_date")
                roi = _roi_reocr(page, img, want)
                if need_sponsor and "sponsor_id" in roi:
                    m = SPN_RE.search(roi["sponsor_id"].replace(" ", ""))
                    if m:
                        rec.sponsor_id = f"SPN-{m.group(1)}"
                        rec.field_sources["sponsor_id"] = "whitelist_ocr"
                        need_sponsor = False
                if need_date and "arrival_date" in roi:
                    dm = DATE_RE.search(roi["arrival_date"].replace(" ", ""))
                    if dm and _valid_date(dm.group(1)):
                        rec.arrival_date = dm.group(1)
                        rec.field_sources["arrival_date"] = "whitelist_ocr"
                        need_date = False
            if not (need_sponsor or need_date or need_species):
                break
    finally:
        doc.close()


# --- Risk-flag cascade (slip-OCR specialist recipe, measured 14/48 recovery) --

_FLAG_NAMES = ["memory_tampering", "planetary_embargo", "active_warrant",
               "biohazard_red", "identity_conflict", "sponsor_mismatch",
               "illegible_biometrics", "rescinded_denial"]
_DISQUALIFYING = {"memory_tampering", "planetary_embargo", "active_warrant", "biohazard_red"}


def _flag_vote(value_text: str, votes: dict) -> None:
    from .vocab import _canon, _edit_distance
    for part in re.split(r"[|,;/]", value_text):
        cp = _canon(part)
        if not cp or cp in ("none", "nome", "norne"):
            continue
        best, second = (None, 99.0), 99.0
        for f in _FLAG_NAMES:
            cf = _canon(f)
            d = _edit_distance(cp, cf, cap=len(cf)) / max(1, len(cf))
            if d < best[1]:
                second = best[1]
                best = (f, d)
            elif d < second:
                second = d
        f, ratio = best
        # Disqualifying flags demand tighter reads (a misread here denies a case).
        cap = 0.55 if f in _DISQUALIFYING else 0.60
        if f and (ratio <= 0.5 or (ratio <= cap and second - ratio >= 0.08)):
            votes.setdefault(f, 0)
            votes[f] += 1


def _obs_line_value(ln: str):
    """Fuzzy 'Observed flags' line matcher: first word within edit distance 3
    of 'observed', second within 2 of 'flags' (per measured recipe — regex
    prefixes miss reads like 'Cieerved flags. ...'). Returns the value tail or
    None."""
    from .vocab import _canon, _edit_distance
    words = ln.split()
    if len(words) < 2:
        return None
    w1, w2 = _canon(words[0]), _canon(words[1].rstrip(":."))
    if not w1 or not w2:
        return None
    if (_edit_distance(w1, "observed", cap=4) <= 3
            and _edit_distance(w2, "flags", cap=3) <= 2):
        return " ".join(words[2:])
    return None


def recover_risk_flags(rec: Record, pdf_path: str) -> None:
    """Fallback flag recovery for OCR packets whose slip resisted the standard
    ladder: psm-6 threshold sweep with a separator-tolerant flags-line matcher,
    ROI rescue of the flags line, and a bounded unreadable-slip inference.
    Never runs when flags were already read."""
    if (not _OK or (rec.risk_flags or "none") != "none"
            or rec.risk_panel_damaged or rec.flags_observed):
        return
    try:
        doc = fitz.open(pdf_path)
    except Exception:
        return
    votes: dict = {}
    try:
        from .ocr import _render, _detect_orientation
        for pno in range(doc.page_count):
            page = doc[pno]
            if not page.get_images():
                continue
            orient = _detect_orientation(page)
            base = _render(page, 300, orient=orient)
            page_hit = False
            for th in (120, 140, 160):
                img = base.point(lambda v, t=th: 255 if v > t else 0)
                try:
                    text = pytesseract.image_to_string(
                        img, config="--oem 1 --psm 6", timeout=TIMEOUT_S)
                except Exception:
                    continue
                for ln in text.splitlines():
                    val = _obs_line_value(ln)
                    if val is not None:
                        page_hit = True
                        from .vocab import _canon as _cn
                        if _cn(val) in ("none", "nome", "norne", "mone"):
                            # A readable 'none': trust it, stop entirely.
                            return
                        _flag_vote(val, votes)
            if page_hit and votes:
                break
    finally:
        doc.close()
    if votes:
        # Require corroboration for disqualifying flags read at the loose cap.
        accepted = sorted(f for f, n in votes.items()
                          if n >= 2 or f not in _DISQUALIFYING)
        if accepted:
            rec.risk_flags = "|".join(accepted)
            rec.field_sources["risk_flags"] = "flag_cascade"
            return
    # (An unreadable-slip inference heuristic was measured at 7 right / 40
    # wrong on train and removed: silence is not evidence of illegibility.)
