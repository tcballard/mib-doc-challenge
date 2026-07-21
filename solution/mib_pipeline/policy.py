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
REVOKED_SPONSORS = {"SPN-0007", "SPN-0139", "SPN-4040", "SPN-2718", "SPN-9090"}

# Home worlds under planetary embargo. Inferred from training examples
# (adjudicator-note reasons plus near-unanimous DENIED outcomes at good support)
# as the field manual invites. These are policy facts about worlds, not per-case
# answers keyed to specific PDFs.
EMBARGO_WORLDS = {"wolf-1061c", "trappist-1e", "eris relay"}

STALE_DAYS = 180


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
    return any(k in wc for k in ("DIP-WAIVER", "HARDSHIP", "WAIVER"))


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
    "transit_purpose": 0.35,
    "embargo_world": 0.76,
    "revoked_sponsor": 0.70,
    "fee_unpaid": 0.89,
    "stale_application": 0.46,
    "stale_ocr": 0.38,
    "fee_unknown": 0.95,
    "missing_arrival_date": 0.39,
    "review_flag": 0.95,
    "identity_conflict": 0.38,
    "unsupported_waiver": 0.50,
    "incomplete_evidence": 0.32,
    "clean_approved": 0.62,
    "approved_dip": 0.73,
}


def _conf(reason: str, default: float = 0.6) -> float:
    key = reason.split(":", 1)[0]
    return REASON_CONFIDENCE.get(key, default)


def _core_complete(rec: Record) -> bool:
    visa_ok = (rec.visa_class or "").upper() in VALID_VISA
    return all([
        bool(rec.applicant_name), bool(rec.species_code), bool(rec.home_world),
        visa_ok, bool(re.match(r"SPN-\d{4}", rec.sponsor_id or "")),
        _parse_date(rec.arrival_date) is not None,
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
    if sponsor in REVOKED_SPONSORS:
        return "DENIED", _conf("revoked_sponsor"), "revoked_sponsor"
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
        if rec.ocr_used:
            return "NEEDS_REVIEW", _conf("stale_ocr"), "stale_ocr"
        return "DENIED", _conf("stale_application"), "stale_application"

    # --- Review conditions -> NEEDS_REVIEW ---
    if rec.fee_observed and fee == "unknown":
        return "NEEDS_REVIEW", _conf("fee_unknown"), "fee_unknown"
    if not ad:
        return "NEEDS_REVIEW", _conf("missing_arrival_date"), "missing_arrival_date"

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
    if visa == "DIP-1":
        return "APPROVED", _conf("approved_dip"), "approved_dip"
    return "APPROVED", _conf("clean_approved"), "clean_approved"
