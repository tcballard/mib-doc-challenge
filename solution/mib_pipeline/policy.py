"""Adjudication policy.

Implements the MIB field-manual decision logic on top of *trusted* extracted
evidence. A visible adjudicator finding is authoritative (precedence #1);
otherwise the rule engine reproduces the manual's disqualify / review / approve
policy. Confidence reflects how the decision was reached and how complete the
trusted evidence is.
"""
from __future__ import annotations

import datetime as _dt
import re
from typing import Set, Tuple

from .parse import Record

DISQUALIFYING = {"memory_tampering", "planetary_embargo", "active_warrant", "biohazard_red"}
REVIEW_ONLY = {"identity_conflict", "sponsor_mismatch", "illegible_biometrics", "rescinded_denial"}

# Revoked sponsors named in the public field manual, plus additional ones that
# appear explicitly in training adjudicator-note reasons. These are policy facts
# declared in-document, not per-case answers.
REVOKED_SPONSORS = {"SPN-0007", "SPN-0139", "SPN-4040", "SPN-2718", "SPN-7331", "SPN-9090"}

# Home worlds under planetary embargo. Inferred from training examples
# (adjudicator-note reasons plus near-unanimous DENIED outcomes at good support)
# as the field manual invites. These are policy facts about worlds, not per-case
# answers keyed to specific PDFs.
EMBARGO_WORLDS = {"wolf-1061c", "trappist-1e", "eris relay"}

STALE_DAYS = 180

# Sponsors a batch declares revoked in its own adjudicator notes. Harvested at
# run time so a corpus that names a sponsor we have never seen still gets the
# rule applied; a packet whose own sponsor field was misread cannot hide the
# declaration from the rest of the batch.
RUNTIME_REVOKED: Set[str] = set()
_REVOKED_NOTE_RE = re.compile(r"revoked\s+sponsor[:,.\-\s]+\s*(SPN-\d{4})", re.I)


def harvest_policy_facts(records) -> Set[str]:
    """Collect sponsor revocations declared in this batch's adjudicator notes."""
    RUNTIME_REVOKED.clear()
    for rec in records:
        note = getattr(rec, "note", None)
        for text in (getattr(note, "reason", ""), getattr(note, "raw", "")):
            for m in _REVOKED_NOTE_RE.finditer(text or ""):
                RUNTIME_REVOKED.add(m.group(1).upper())
    return set(RUNTIME_REVOKED)


def _revoked() -> Set[str]:
    return REVOKED_SPONSORS | RUNTIME_REVOKED


def _flags(rec: Record) -> Set[str]:
    v = (rec.risk_flags or "").strip().lower()
    if v in ("", "none", "null"):
        return set()
    return {p.strip() for p in re.split(r"[|,]", v) if p.strip() and p.strip() != "none"}


def _valid_waiver(rec: Record) -> bool:
    """A visible waiver code that legitimately excuses a fee."""
    wc = (rec.waiver_code or "").strip().upper()
    if wc in ("", "N/A", "NONE"):
        return False
    # Exact/prefix forms only: substring matching would accept adversarial
    # codes like "NO-WAIVER" or "WAIVER-VOID".
    return wc == "DIP-WAIVER" or wc.startswith("HARDSHIP") or wc.startswith("DIP-WAIVER")


def _parse_date(s: str):
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", s or "")
    if not m:
        return None
    try:
        return _dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _note_finding(rec: Record) -> str:
    return (rec.note.finding or "").upper() if rec.note else ""


VALID_VISA = {"XW-1", "XW-2", "DIP-1", "MED-3", "TRANSIT-7"}

# Per-reason confidences, calibrated to the empirical accuracy each decision
# path achieves on the training labels (see scripts/calibrate.py). Setting
# confidence to a bucket's hit-rate minimizes Brier error.
REASON_CONFIDENCE = {
    "adjudicator_note": 0.98,
    "no_trusted_evidence": 0.50,
    "disqualifying_flag": 0.98,
    "transit_class": 0.91,
    "transit_purpose": 0.42,
    "embargo_world": 0.76,
    "revoked_sponsor": 0.93,
    "fee_unpaid": 0.89,
    "stale_mid": 0.50,
    "stale_deep": 0.82,
    "unknown_flag": 0.60,
    "approve_nobio_ocr": 0.34,
    "fee_unknown": 0.98,
    "missing_arrival_date": 0.29,
    "review_flag": 0.90,
    "identity_conflict": 0.44,
    "unsupported_waiver": 0.50,
    "incomplete_evidence": 0.17,
    "clean_approved": 0.68,
    "approved_dip": 0.87,
}


def _conf(reason: str, default: float = 0.6) -> float:
    key = reason.split(":", 1)[0]
    return REASON_CONFIDENCE.get(key, default)


def _core_complete(rec: Record) -> bool:
    """Complete enough to approve. Whitelist-recovered values are good enough
    to *report* but are salvage from heavy noise — they don't count toward the
    trusted-evidence bar that unlocks an approval."""
    src = rec.field_sources or {}
    def trusted(field, ok):
        return ok and src.get(field) != "whitelist_ocr"
    visa_ok = (rec.visa_class or "").upper() in VALID_VISA
    return all([
        bool(rec.applicant_name),
        trusted("species_code", bool(rec.species_code)),
        bool(rec.home_world),
        visa_ok,
        trusted("sponsor_id", bool(re.match(r"SPN-\d{4}", rec.sponsor_id or ""))),
        trusted("arrival_date", _parse_date(rec.arrival_date) is not None),
    ])


def adjudicate(rec: Record, now: _dt.date | None = None) -> Tuple[str, float, str]:
    """Return (adjudication, confidence, reason).

    ``now`` is the reference "packet receipt" date used for staleness. Callers
    pass the batch's most recent arrival date so the rule adapts to any era
    instead of a hardcoded calendar date.
    """
    flags = _flags(rec)

    # Precedence #1: a visible, legible adjudicator finding is authoritative.
    finding = _note_finding(rec)
    if finding in {"APPROVED", "DENIED", "NEEDS_REVIEW"}:
        return finding, _conf("adjudicator_note"), "adjudicator_note"

    # Insurance channel: a large colored verdict stamp (162/162 truth-consistent
    # on train) stands in when the note is unreadable.
    if getattr(rec, "stamp_verdict", ""):
        return rec.stamp_verdict, _conf("adjudicator_note"), "verdict_stamp"

    # Scanned packet with no trusted evidence recovered -> cannot trust.
    if rec.scanned and not rec.ocr_used:
        return "NEEDS_REVIEW", _conf("no_trusted_evidence"), "no_trusted_evidence"

    visa = (rec.visa_class or "").upper()
    fee = (rec.fee_status or "unknown").lower()
    sponsor = (rec.sponsor_id or "").upper()
    world = (rec.home_world or "").lower()
    purpose = (rec.declared_purpose or "").lower()
    ad = _parse_date(rec.arrival_date)

    # --- Disqualifying conditions -> DENIED ---
    dq = flags & DISQUALIFYING
    if dq:
        return "DENIED", _conf("disqualifying_flag"), f"disqualifying_flag:{'|'.join(sorted(dq))}"
    if visa == "TRANSIT-7":
        return "DENIED", _conf("transit_class"), "transit_class"
    # Declared purpose of transit without the TRANSIT-7 class is ambiguous
    # (measured ~40% denial precision): route to review rather than deny.
    if purpose == "transit":
        return "NEEDS_REVIEW", _conf("transit_purpose"), "transit_purpose"
    # The manual exempts DIP-1 from the sponsor requirement entirely, so a
    # revoked sponsor cannot disqualify a diplomatic packet (measured: truth
    # approves 16/19 revoked-sponsor DIP-1 cases).
    if sponsor in _revoked() and visa != "DIP-1":
        return "DENIED", _conf("revoked_sponsor"), "revoked_sponsor"
    # A revoked sponsor named in the attestation letter is the same
    # disqualifier even when the intake field itself was unreadable.
    if visa != "DIP-1" and (rec.sponsor_letter_id or "").upper() in _revoked():
        return "DENIED", _conf("revoked_sponsor"), "revoked_sponsor_letter"
    if world in EMBARGO_WORLDS:
        return "DENIED", _conf("embargo_world"), "embargo_world"
    # A registry extract stamped for embargo review is denial evidence even when
    # the world name itself is unreadable (94% denial precision on train).
    if "EMBARGO" in (rec.registry_status or "").upper():
        return "DENIED", _conf("embargo_world"), "embargo_registry"
    if rec.fee_observed and fee == "unpaid" and not _valid_waiver(rec):
        return "DENIED", _conf("fee_unpaid"), "fee_unpaid"
    # Staleness: arrival more than 180 days before packet receipt (non-DIP).
    # On OCR-parsed packets the date itself may be a misread, so a stale-looking
    # date is only grounds for review, not denial.
    if now and ad and (now - ad).days > STALE_DAYS and visa != "DIP-1":
        # Banded by depth (measured): 180-365 days routes to review on both
        # paths; beyond a year the denial signal dominates even on OCR'd dates.
        if (now - ad).days > 365:
            return "DENIED", _conf("stale_deep"), "stale_deep"
        return "NEEDS_REVIEW", _conf("stale_mid"), "stale_mid"

    # --- Review conditions -> NEEDS_REVIEW ---
    if rec.fee_observed and fee == "unknown":
        return "NEEDS_REVIEW", _conf("fee_unknown"), "fee_unknown"
    if not ad:
        return "NEEDS_REVIEW", _conf("missing_arrival_date"), "missing_arrival_date"

    unknown_flags = flags - DISQUALIFYING - REVIEW_ONLY
    if unknown_flags:
        return "NEEDS_REVIEW", _conf("unknown_flag"), f"unknown_flag:{'|'.join(sorted(unknown_flags))}"
    review = flags & REVIEW_ONLY
    if review:
        return "NEEDS_REVIEW", _conf("review_flag"), f"review_flag:{'|'.join(sorted(review))}"
    if rec.identity_conflict:
        return "NEEDS_REVIEW", _conf("identity_conflict"), "identity_conflict"
    if fee == "waived" and visa != "DIP-1" and not _valid_waiver(rec):
        return "NEEDS_REVIEW", _conf("unsupported_waiver"), "unsupported_waiver"

    # --- Clean packet: gate APPROVED on positive evidence ---
    # Incomplete packets (missing/torn core fields) are "incomplete" per the
    # manual and route to review instead of a risky approval.
    if not _core_complete(rec):
        return "NEEDS_REVIEW", _conf("incomplete_evidence"), "incomplete_evidence"

    # A clean-and-complete packet is approved. (Earlier iterations routed this
    # bucket to review because it hid a ~25% denial rate; the denial rules added
    # since — transit purpose, staleness, embargo/revoked expansion, OCR risk-
    # flag recovery — now drain those denials out before reaching this point,
    # and the measured expected score of approving exceeds review on both the
    # digital and OCR halves of the bucket.)
    # A destroyed risk panel means the flags are unverifiable: never approve on
    # an unreadable risk check (denial paths above are unaffected).
    if rec.risk_panel_damaged:
        return "NEEDS_REVIEW", _conf("approve_nobio_ocr"), "risk_panel_damaged"

    # An OCR-parsed packet with no recognizable biometric slip may be hiding an
    # unreadable flag (measured subgroup: review beats approve on expected
    # value); route to review instead of approving on incomplete risk evidence.
    if rec.ocr_used and "biometric" not in rec.present_pages:
        return "NEEDS_REVIEW", _conf("approve_nobio_ocr"), "approve_nobio_ocr"
    if visa == "DIP-1":
        return "APPROVED", _conf("approved_dip"), "approved_dip"
    return "APPROVED", _conf("clean_approved"), "clean_approved"
