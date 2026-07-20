"""Generalizable feature vector for the residual adjudication model.

Only features that transfer across packets are used: visa/fee/flag categories,
completeness and page-composition signals, staleness, and the learned
embargo/revoked policy indicators. No packet identity (names, raw sponsor ids,
specific worlds) leaks in, so the model cannot memorize individual PDFs.
"""
from __future__ import annotations

import datetime as _dt
import re
from typing import List, Optional

from .parse import Record
from .policy import (
    DISQUALIFYING, REVIEW_ONLY, EMBARGO_WORLDS, REVOKED_SPONSORS,
    VALID_VISA, _flags, _parse_date, _valid_waiver, _core_complete,
)

VISA_CLASSES = ["XW-1", "XW-2", "DIP-1", "MED-3", "TRANSIT-7"]
FEE_CLASSES = ["paid", "waived", "unpaid", "unknown"]
ALL_FLAGS = sorted(DISQUALIFYING | REVIEW_ONLY)
PAGE_TYPES = ["intake", "registry", "fee", "biometric", "sponsor", "note"]

FEATURE_NAMES: List[str] = (
    [f"visa_{v}" for v in VISA_CLASSES] + ["visa_missing"]
    + [f"fee_{f}" for f in FEE_CLASSES] + ["fee_observed"]
    + [f"flag_{f}" for f in ALL_FLAGS]
    + ["n_flags", "has_disq_flag", "has_review_flag"]
    + ["is_embargo_world", "is_revoked_sponsor", "purpose_transit"]
    + [f"page_{p}" for p in PAGE_TYPES] + ["n_page_types"]
    + ["scanned", "ocr_used", "core_complete", "identity_conflict"]
    + ["name_present", "species_present", "home_present", "sponsor_present"]
    + ["date_present", "arrival_age_days", "stale", "valid_waiver"]
    + ["registry_clear", "registry_other", "registry_missing"]
)


def featurize(rec: Record, now: Optional[_dt.date]) -> List[float]:
    flags = _flags(rec)
    visa = (rec.visa_class or "").upper()
    fee = (rec.fee_status or "unknown").lower()
    world = (rec.home_world or "").lower()
    sponsor = (rec.sponsor_id or "").upper()
    purpose = (rec.declared_purpose or "").lower()
    pages = set(rec.present_pages)
    ad = _parse_date(rec.arrival_date)
    reg = (rec.registry_status or "").strip().lower()

    f: List[float] = []
    f += [1.0 if visa == v else 0.0 for v in VISA_CLASSES]
    f.append(1.0 if visa not in VALID_VISA else 0.0)
    f += [1.0 if fee == c else 0.0 for c in FEE_CLASSES]
    f.append(1.0 if rec.fee_observed else 0.0)
    f += [1.0 if fl in flags else 0.0 for fl in ALL_FLAGS]
    f.append(float(len(flags)))
    f.append(1.0 if flags & DISQUALIFYING else 0.0)
    f.append(1.0 if flags & REVIEW_ONLY else 0.0)
    f.append(1.0 if world in EMBARGO_WORLDS else 0.0)
    f.append(1.0 if sponsor in REVOKED_SPONSORS else 0.0)
    f.append(1.0 if purpose == "transit" else 0.0)
    f += [1.0 if p in pages else 0.0 for p in PAGE_TYPES]
    f.append(float(len(pages)))
    f.append(1.0 if rec.scanned else 0.0)
    f.append(1.0 if rec.ocr_used else 0.0)
    f.append(1.0 if _core_complete(rec) else 0.0)
    f.append(1.0 if rec.identity_conflict else 0.0)
    f.append(1.0 if rec.applicant_name else 0.0)
    f.append(1.0 if rec.species_code else 0.0)
    f.append(1.0 if rec.home_world else 0.0)
    f.append(1.0 if re.match(r"SPN-\d{4}", sponsor) else 0.0)
    f.append(1.0 if ad else 0.0)
    age = (now - ad).days if (now and ad) else -1.0
    f.append(float(age))
    f.append(1.0 if (now and ad and (now - ad).days > 180) else 0.0)
    f.append(1.0 if _valid_waiver(rec) else 0.0)
    f.append(1.0 if reg == "clear" else 0.0)
    f.append(1.0 if (reg and reg != "clear") else 0.0)
    f.append(1.0 if not reg else 0.0)
    return f
