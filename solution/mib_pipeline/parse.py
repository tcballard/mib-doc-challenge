"""Field parsing and cross-page consolidation.

Turns trust-classified pages into a single applicant record, honoring the
field-manual evidence precedence and the "active case_id" rule for packets that
contain more than one applicant.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .extract import Page
from .vocab import correct_field, SPECIES, HOME_WORLDS, PURPOSES, correct as vocab_correct

CASE_RE = re.compile(r"MIB-\d{4,}")
VISA_RE = re.compile(r"\b(XW-1|XW-2|DIP-1|MED-3|TRANSIT-7)\b")


def _visa_normalize(v: str) -> str:
    """Uppercase and repair OCR-noised separators ('XW.2', 'XW 2') before
    matching against the visa-class pattern."""
    repaired = re.sub(r"(?<=[A-Z])[.,_ ](?=\d)", "-", (v or "").upper())
    return repaired.replace(" ", "")
SPONSOR_RE = re.compile(r"\bSPN-\d{3,}\b")
DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
FINDING_RE = re.compile(r"Finding:\s*(APPROVED|DENIED|NEEDS_REVIEW)\.?\s*Reason:\s*(.*)", re.I)

# Vertical "label\nvalue" fields, keyed by the label text.
INTAKE_LABELS = {
    "Case ID": "case_id",
    "Applicant": "applicant_name",
    "Species Code": "species_code",
    "Home World": "home_world",
    "Visa Class": "visa_class",
    "Sponsor ID": "sponsor_id",
    "Arrival Date": "arrival_date",
    "Declared Purpose": "declared_purpose",
}
REGISTRY_LABELS = {
    "Registry Name": "applicant_name",
    "Home World": "home_world",
    "Species Code": "species_code",
    "Arrival Date": "arrival_date",
    "Registry Status": "registry_status",
}

# All label strings, used to know when a "value" slot is actually another label.
ALL_LABELS = set(INTAKE_LABELS) | set(REGISTRY_LABELS) | {
    "Fee Status", "Amount", "Waiver Code", "Registry Status",
}

FEE_VALUES = {"paid", "waived", "unpaid", "unknown"}

# Damage markers rendered in place of a field when the evidence was destroyed
# (cut out, washed out, torn, lost). Treated as "no trusted value".
DAMAGE_RE = re.compile(r"^\[.*\]$")
DAMAGE_WORDS = re.compile(
    r"\b(unreadable|illegible|washed\s*out|whiteout|cut\s*out|torn|redacted|"
    r"corrupt|registry\s*lost|missing)\b", re.I
)

KNOWN_FLAGS = [
    "memory_tampering", "planetary_embargo", "active_warrant", "biohazard_red",
    "identity_conflict", "sponsor_mismatch", "illegible_biometrics", "rescinded_denial",
]


def _is_damage(v: str) -> bool:
    v = (v or "").strip()
    return bool(DAMAGE_RE.match(v) or DAMAGE_WORDS.search(v))


def _fuzzy_flags(text: str) -> str:
    """Recover risk flags from noisy biometric text by matching known names."""
    low = re.sub(r"[\s_]+", "_", text.lower())
    found = [f for f in KNOWN_FLAGS if f in low]
    if not found:
        return "none"
    return "|".join(sorted(set(found)))


@dataclass
class Note:
    finding: Optional[str] = None
    reason: str = ""
    raw: str = ""


@dataclass
class Record:
    case_id: str = ""
    applicant_name: str = ""
    species_code: str = ""
    home_world: str = ""
    visa_class: str = ""
    sponsor_id: str = ""
    arrival_date: str = ""
    declared_purpose: str = ""
    risk_flags: str = "none"
    fee_status: str = "unknown"
    fee_observed: bool = False
    # provenance / policy inputs (not emitted directly)
    waiver_code: str = ""
    registry_status: str = ""
    note: Note = field(default_factory=Note)
    sponsor_letter_id: str = ""
    sponsor_letter_name: str = ""
    sponsor_letter_purpose: str = ""
    sponsor_letter_visa: str = ""
    fee_correction: str = ""
    field_sources: Dict[str, str] = field(default_factory=dict)
    present_pages: List[str] = field(default_factory=list)
    ocr_used: bool = False
    scanned: bool = False
    identity_conflict: bool = False


PLACEHOLDERS = {"passport image", "registry image", "scan image", "primary intake record",
                "mib eyes only", "sample denial", "n/a"}


def _typed_score(d: Dict[str, str]) -> int:
    """How many parsed values match the datatype expected for their field."""
    score = 0
    if re.match(r"MIB-\d", d.get("case_id", "")):
        score += 1
    if re.search(r"SPN-\d", d.get("sponsor_id", "")):
        score += 1
    if VISA_RE.search(d.get("visa_class", "").upper()):
        score += 1
    if DATE_RE.search(d.get("arrival_date", "")):
        score += 1
    if d.get("fee_status", "").strip().lower() in FEE_VALUES:
        score += 1
    if re.fullmatch(r"[A-Z][A-Z_]{3,}", d.get("species_code", "").strip()):
        score += 1
    if d.get("registry_status", "").strip().lower() in {"clear", "flagged", "review", "hold"}:
        score += 1
    # Penalize picking obvious placeholder/boilerplate strings as values.
    for v in d.values():
        vv = v.strip().lower()
        if vv in PLACEHOLDERS or vv.startswith("packet ") or vv.startswith("synthetic hiring"):
            score -= 1
    return score


def _kv_vertical(lines: List[str], labels: Dict[str, str]) -> Dict[str, str]:
    """Parse stacked label/value pairs.

    Layouts place the value either directly below or directly above its label
    (reading order flips depending on baseline rounding). We build both
    candidate parses and keep whichever validates more typed fields.
    """
    def build(offset: int) -> Dict[str, str]:
        out: Dict[str, str] = {}
        for i, ln in enumerate(lines):
            key = ln.strip().rstrip(":")
            if key not in labels:
                continue
            j = i + offset
            if 0 <= j < len(lines):
                val = lines[j].strip()
                low = val.lower()
                is_trap = (
                    low in PLACEHOLDERS
                    or low.startswith("packet ")
                    or low.startswith("synthetic hiring")
                    or "sample denial" in low
                )
                if val and val.rstrip(":") not in labels and not is_trap:
                    out.setdefault(labels[key], val)
        return out

    below = build(+1)
    above = build(-1)
    # Prefer whichever direction produces more type-valid fields; ties -> below.
    return above if _typed_score(above) > _typed_score(below) else below


def _kv_inline(lines: List[str]) -> Dict[str, str]:
    """Parse 'Label: value' pairs (biometric slip style)."""
    out: Dict[str, str] = {}
    for ln in lines:
        if ":" in ln:
            k, _, v = ln.partition(":")
            out[k.strip()] = v.strip()
    return out


def _norm_flags(value: str) -> str:
    v = (value or "").strip().lower()
    if v in ("", "none", "null", "unknown", "n/a"):
        return "none"
    parts = [p.strip() for p in re.split(r"[|,]", v) if p.strip()]
    parts = [p for p in parts if p != "none"]
    if not parts:
        return "none"
    return "|".join(sorted(set(parts)))


_REASON_SPLIT = re.compile(r"reas[oa]?r?|resor|\breason\b", re.I)


def _extract_finding(text: str):
    """Recover the adjudicator finding, tolerating OCR corruption.

    A note carries exactly one finding followed by a reason. We first try a
    clean parse, then fall back to fuzzy class detection scoped to the text
    *before* the reason (so a reason mentioning e.g. "denial" or "review" in a
    rescinded-denial note cannot flip the finding).
    """
    m = FINDING_RE.search(text)
    if m:
        return m.group(1).upper(), m.group(2).strip()

    # Scope to the head (header + finding line), dropping the reason section.
    head = _REASON_SPLIT.split(text, maxsplit=1)[0]
    hu = head.upper().replace(" ", "")
    # Priority APPROVED -> NEEDS_REVIEW -> DENIED avoids denial/review words in a
    # leaked reason misclassifying an approval or review note.
    if "APPRO" in hu or "APPRV" in hu:
        return "APPROVED", ""
    if "REVI" in hu or "REVIE" in hu or "NEEDS" in hu or "REVICW" in hu:
        return "NEEDS_REVIEW", ""
    if "DENI" in hu or "DENIE" in hu or "DEN1" in hu:
        return "DENIED", ""
    return None, ""


# Inline "Label: value" field labels, canonicalized -> field name. Scanned
# pages render fields inline (unlike the stacked layout of digital pages).
INLINE_LABELS = {
    "caseid": "case_id",
    "applicant": "applicant_name",
    "registryname": "applicant_name",
    "speciescode": "species_code",
    "speciesmatch": "species_code",
    "homeworld": "home_world",
    "visaclass": "visa_class",
    "sponsorid": "sponsor_id",
    "arrivaldate": "arrival_date",
    "declaredpurpose": "declared_purpose",
    "registrystatus": "registry_status",
    "feestatus": "fee_status",
    "waivercode": "waiver_code",
}


def _canon_label(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _valid_date(s: str) -> bool:
    import datetime as _dt
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", s or "")
    if not m:
        return False
    try:
        _dt.date(int(m[1]), int(m[2]), int(m[3]))
        return True
    except ValueError:
        return False


def _inline_fields(lines: List[str]) -> Dict[str, str]:
    """Parse inline 'Label: value' lines with OCR-tolerant label matching."""
    out: Dict[str, str] = {}
    for ln in lines:
        if ":" not in ln:
            continue
        label, _, value = ln.partition(":")
        value = value.strip()
        if not value:
            continue
        key = _canon_label(label)
        fld = INLINE_LABELS.get(key)
        if fld is None and len(key) >= 6:
            # OCR-noised label: accept a unique fuzzy match.
            hit = vocab_correct(key, INLINE_LABELS.keys(), max_ratio=0.3)
            fld = INLINE_LABELS.get(hit) if hit else None
        if fld and not _is_damage(value):
            # Values sometimes run into placeholder text ("... PASSPORT IMAGE").
            value = re.sub(r"\s*(PASSPORT|REGISTRY|SCAN)\s+IMAGE.*$", "", value).strip()
            if value:
                out.setdefault(fld, value)
    return out


def _pattern_sweep(lines: List[str]) -> Dict[str, str]:
    """Recover typed values whose labels OCR destroyed (e.g. '2: SPN-7720').

    Only unambiguous shapes are claimed: SPN ids, ISO dates, visa classes, and
    values that match the closed species/world/purpose vocabularies.
    """
    out: Dict[str, str] = {}
    for ln in lines:
        if _is_damage(ln):
            continue
        m = SPONSOR_RE.search(ln)
        if m:
            out.setdefault("sponsor_id", m.group(0))
        m = DATE_RE.search(ln)
        if m and _valid_date(m.group(1)):
            out.setdefault("arrival_date", m.group(1))
        m = VISA_RE.search(_visa_normalize(ln))
        if m:
            out.setdefault("visa_class", m.group(1))
        # Vocabulary-shaped values (checked on the post-colon tail if present).
        tail = ln.partition(":")[2].strip() if ":" in ln else ln.strip()
        if tail and not VISA_RE.search(_visa_normalize(tail)):
            # (visa-class tails are excluded so "TRANSIT-7" can't fuzzy-match the
            # purpose "transit")
            for fld, vocab in (("species_code", SPECIES), ("home_world", HOME_WORLDS),
                               ("declared_purpose", PURPOSES)):
                hit = vocab_correct(tail, vocab, max_ratio=0.25)
                if hit:
                    out.setdefault(fld, hit)
    return out


def _header_case_id(page: Page) -> Optional[str]:
    for ln in page.visible_lines:
        m = CASE_RE.search(ln)
        if m:
            return m.group(0)
    return None


def parse_packet(case_id: str, pages: List[Page]) -> Record:
    rec = Record(case_id=case_id)
    rec.ocr_used = any(p.ocr_used for p in pages)

    # Active-case filtering: if pages carry an explicit different case id in the
    # header, prefer pages matching the packet's own case id. Fall back to all
    # pages when nothing matches (keeps single-applicant packets robust).
    matching = [p for p in pages if (_header_case_id(p) or case_id) == case_id]
    use_pages = matching if matching else pages

    intake: Dict[str, str] = {}
    registry: Dict[str, str] = {}
    biometric: Dict[str, str] = {}
    sponsor_names: List[str] = []

    meaningful_visible = False

    for p in use_pages:
        vis = p.visible_lines
        title = p.title or ""
        text = "\n".join(vis)
        for t in vis:
            if not t.startswith("Packet ") and t != "Synthetic hiring challenge document" and t not in ALL_LABELS:
                meaningful_visible = True

        # Page-type detection tolerates OCR-garbled titles by looking for
        # distinctive markers anywhere in the visible text.
        if (title.startswith("FORM I-8090") or "I-8090" in text
                or "Authorization Intake" in text or "Primary intake record" in text):
            kv = _kv_vertical(vis, INTAKE_LABELS)
            for k, v in _inline_fields(vis).items():
                kv.setdefault(k, v)
            for k, v in kv.items():
                intake.setdefault(k, v)
            rec.present_pages.append("intake")

        elif title.startswith("Planetary Registry") or "Registry Extract" in text:
            kv = _kv_vertical(vis, REGISTRY_LABELS)
            for k, v in _inline_fields(vis).items():
                kv.setdefault(k, v)
            for k, v in kv.items():
                registry.setdefault(k, v)
            rec.present_pages.append("registry")

        elif title.startswith("MIB Fee Receipt") or "Fee Receipt" in text:
            kv = _kv_vertical(vis, {"Fee Status": "fee_status", "Waiver Code": "waiver_code"})
            for k, v in _inline_fields(vis).items():
                kv.setdefault(k, v)
            if kv.get("fee_status"):
                fs = correct_field("fee_status", kv["fee_status"].strip().lower())
                if fs in FEE_VALUES:
                    rec.fee_status = fs
                    rec.fee_observed = True
            if kv.get("waiver_code"):
                rec.waiver_code = kv["waiver_code"].strip()
            rec.present_pages.append("fee")

        elif title.startswith("FORM B-13") or "B-13" in text or "Biometric" in text:
            inl = _kv_inline(vis)
            biometric = inl
            obs = inl.get("Observed flags", "")
            flags = _norm_flags(obs)
            if flags == "none":
                # OCR may have mangled the "Observed flags:" label; scan the whole
                # slip for known flag names as a fallback.
                flags = _fuzzy_flags(text)
            rec.risk_flags = flags
            if inl.get("Species Match"):
                biometric["species_code"] = inl["Species Match"]
            if inl.get("Applicant"):
                sponsor_names.append(inl["Applicant"])
            rec.present_pages.append("biometric")

        elif title.startswith("Sponsor Attestation") or "Sponsor Attestation" in text or "attests that" in text:
            m = SPONSOR_RE.search(text)
            if m:
                rec.sponsor_letter_id = m.group(0)
            nm = re.search(r"attests that (.+?) is expected", text)
            if nm:
                rec.sponsor_letter_name = nm.group(1).strip()
            pm = re.search(r"on Earth for (.+?)\.", text.replace("\n", " "))
            if pm:
                rec.sponsor_letter_purpose = pm.group(1).strip()
            vm = re.search(r"class\s+(XW-1|XW-2|DIP-1|MED-3|TRANSIT-7)", text)
            if vm:
                rec.sponsor_letter_visa = vm.group(1)
            rec.present_pages.append("sponsor")

        elif title.startswith("Manual Adjudicator Note") or "Adjudicator Note" in text:
            finding, reason = _extract_finding(text)
            rec.note = Note(finding=finding, reason=reason, raw=text)
            fc = re.search(r"fee status is (paid|waived|unpaid|unknown)", text, re.I)
            if fc:
                rec.fee_correction = fc.group(1).lower()
            rec.present_pages.append("note")

    rec.scanned = not meaningful_visible

    # Consolidate identity fields with manual precedence:
    # intake form > biometric > registry (sponsor letter fills names/sponsor).
    def pick(fieldname, *sources):
        for src_name, d in sources:
            v = d.get(fieldname)
            if v:
                rec.field_sources[fieldname] = src_name
                return v
        return ""

    sponsor_d = {
        "applicant_name": rec.sponsor_letter_name,
        "visa_class": rec.sponsor_letter_visa,
        "declared_purpose": rec.sponsor_letter_purpose,
    }
    rec.applicant_name = pick("applicant_name", ("intake", intake), ("biometric", biometric), ("registry", registry), ("sponsor", sponsor_d))
    rec.species_code = pick("species_code", ("intake", intake), ("biometric", biometric), ("registry", registry))
    rec.home_world = pick("home_world", ("intake", intake), ("registry", registry))
    rec.visa_class = pick("visa_class", ("intake", intake), ("sponsor", sponsor_d))
    rec.arrival_date = pick("arrival_date", ("intake", intake), ("registry", registry))
    rec.declared_purpose = pick("declared_purpose", ("intake", intake), ("sponsor", sponsor_d))
    rec.registry_status = registry.get("registry_status", "")

    # Corroboration voting: a value read identically from >=2 independent
    # sources outranks a single-source precedence pick that nothing else
    # confirms (OCR misreads rarely repeat verbatim across pages).
    def _nzv(s):
        return " ".join((s or "").split()).casefold()

    def _corroborate(fld, cands):
        cands = [c for c in cands if c and not _is_damage(c)]
        cur = getattr(rec, fld)
        if not cands or not cur:
            return
        counts = Counter(_nzv(c) for c in cands)
        cur_n = counts.get(_nzv(cur), 0)
        best_n, best = max(((n, v) for v, n in counts.items()), default=(0, ""))
        if best_n >= 2 and cur_n <= 1 and best != _nzv(cur):
            for c in cands:  # keep original casing of a corroborated variant
                if _nzv(c) == best:
                    rec.field_sources[fld] = "corroboration"
                    setattr(rec, fld, c)
                    return

    _corroborate("applicant_name", [intake.get("applicant_name"), registry.get("applicant_name"),
                                    biometric.get("Applicant"), rec.sponsor_letter_name])
    _corroborate("species_code", [intake.get("species_code"), registry.get("species_code"),
                                  biometric.get("species_code")])
    spn_occurrences: List[str] = []
    for p in use_pages:
        for ln in p.visible_lines:
            spn_occurrences.extend(SPONSOR_RE.findall(ln))
    _corroborate("sponsor_id", spn_occurrences)

    # Last-resort recovery for OCR pages whose labels were destroyed: sweep the
    # packet's visible lines for unambiguously-typed values, filling only fields
    # that are still empty.
    if any(p.ocr_used for p in use_pages):
        sweep_lines: List[str] = []
        for p in use_pages:
            sweep_lines.extend(p.visible_lines)
        swept = _pattern_sweep(sweep_lines)
        for fld in ("sponsor_id", "arrival_date", "visa_class", "species_code",
                    "home_world", "declared_purpose"):
            if not getattr(rec, fld) and swept.get(fld):
                rec.field_sources[fld] = "sweep"
                setattr(rec, fld, swept[fld])

    # Keep only calendar-valid arrival dates; an OCR-misread impossible date is
    # not trusted evidence.
    if rec.arrival_date:
        dm = DATE_RE.search(rec.arrival_date)
        rec.arrival_date = dm.group(1) if (dm and _valid_date(dm.group(1))) else ""

    # Closed-vocabulary correction: repair OCR character noise on fields whose
    # value space is a known small set (also fixes policy checks that depend on
    # exact values, e.g. embargo-world matching).
    for fld in ("species_code", "home_world", "visa_class", "declared_purpose"):
        val = getattr(rec, fld)
        if val:
            setattr(rec, fld, correct_field(fld, val))

    # Blank out fields whose visible value is a damage marker so the
    # completeness gate correctly treats them as unrecoverable.
    for fld in ("applicant_name", "species_code", "home_world", "visa_class",
                "arrival_date", "declared_purpose"):
        if _is_damage(getattr(rec, fld)):
            setattr(rec, fld, "")

    # Sponsor: intake form primary, sponsor letter as fallback. Only accept a
    # value that actually matches the SPN-#### pattern (guards against watermark
    # traps like "SAMPLE DENIAL" landing in the value slot).
    sponsor = intake.get("sponsor_id") or rec.sponsor_letter_id or rec.sponsor_id
    m = SPONSOR_RE.search(sponsor or "")
    rec.sponsor_id = m.group(0) if m else ""

    # Strip trailing placeholder noise from species (e.g. "ARCTURIAN SCAN IMAGE").
    if rec.species_code:
        sm = re.match(r"[A-Z][A-Z_]{2,}", rec.species_code.upper())
        if sm:
            rec.species_code = sm.group(0)

    # Normalize visa class if noisy (OCR).
    if rec.visa_class:
        vm = VISA_RE.search(_visa_normalize(rec.visa_class))
        if vm:
            rec.visa_class = vm.group(1)

    # Fee correction from adjudicator note overrides receipt.
    if rec.fee_correction:
        rec.fee_status = rec.fee_correction
        rec.fee_observed = True

    # Identity conflict: intake vs registry/biometric name or species disagree.
    # Comparison is fuzzy — OCR renders the same name slightly differently
    # across passes/pages, and near-identical strings are the same identity,
    # not a conflict.
    from .vocab import _edit_distance, _canon

    def _really_different(a: str, b: str) -> bool:
        ca, cb = _canon(a), _canon(b)
        if not ca or not cb or ca in cb or cb in ca:
            return False
        tol = max(2, int(0.34 * max(len(ca), len(cb))))
        return _edit_distance(ca, cb, cap=tol) > tol

    names = [v for v in [intake.get("applicant_name"), registry.get("applicant_name"), biometric.get("Applicant")] if v and not _is_damage(v)]
    specs = [correct_field("species_code", v) for v in [intake.get("species_code"), registry.get("species_code"), biometric.get("species_code")] if v and not _is_damage(v)]
    if any(_really_different(a, b) for i, a in enumerate(names) for b in names[i + 1:]) \
            or len({s.upper() for s in specs}) > 1:
        rec.identity_conflict = True

    return rec
