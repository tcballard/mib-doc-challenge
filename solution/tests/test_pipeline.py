"""Behavioral tests for the MIB pipeline.

Run from the repo root (needs the unzipped training data + tesseract):

    python3 -m pytest solution/tests/test_pipeline.py -q
"""
import re
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "solution"))

from mib_pipeline.parse import _extract_finding, _is_damage, _norm_flags, _fuzzy_flags
from mib_pipeline.policy import adjudicate
from mib_pipeline.parse import Record, Note

DATA = ROOT / "data" / "train"


def test_finding_fuzzy_recovery():
    assert _extract_finding("Finding: APPROVED. Reason: clean.")[0] == "APPROVED"
    # OCR-garbled variants
    assert _extract_finding("Firaling. APPROVES\nReason: Approv")[0] == "APPROVED"
    assert _extract_finding("ANuNG WERLS_Revicw |\nReason: P:")[0] == "NEEDS_REVIEW"
    assert _extract_finding("Finding: DENIED\nistry evidence")[0] == "DENIED"
    # A rescinded-denial review reason must not flip the finding to DENIED.
    assert _extract_finding(
        "Finding: NEEDS_REVIEW. Reason: Prior denial stamp rescinded. Route to review."
    )[0] == "NEEDS_REVIEW"


def test_damage_markers():
    assert _is_damage("[NAME CUT OUT]")
    assert _is_damage("UNREADABLE")
    assert _is_damage("[DATE WASHED OUT]")
    assert not _is_damage("Ixodane Luzarn")
    assert not _is_damage("XW-2")


def test_flag_normalization():
    assert _norm_flags("none") == "none"
    assert _norm_flags("") == "none"
    assert _norm_flags("sponsor_mismatch, illegible_biometrics") == "illegible_biometrics|sponsor_mismatch"
    assert _fuzzy_flags("biometric slip observed biohazard red here") == "biohazard_red"


def test_note_is_authoritative():
    rec = Record(case_id="MIB-000000", visa_class="TRANSIT-7")
    rec.note = Note(finding="APPROVED")
    adj, conf, reason = adjudicate(rec)
    assert adj == "APPROVED" and reason == "adjudicator_note"


def test_disqualifiers():
    rec = Record(case_id="X", risk_flags="biohazard_red", visa_class="MED-3")
    assert adjudicate(rec)[0] == "DENIED"
    rec = Record(case_id="X", visa_class="TRANSIT-7")
    assert adjudicate(rec)[0] == "DENIED"


def test_clean_nondip_routes_to_review():
    # Complete, clean, non-DIP -> conservative review, never a speculative approve.
    rec = Record(
        case_id="X", applicant_name="A B", species_code="ORION_GRAYS",
        home_world="Kepler-186f", visa_class="XW-2", sponsor_id="SPN-1042",
        arrival_date="2026-05-01", fee_status="paid", fee_observed=True,
    )
    assert adjudicate(rec, now=date(2026, 6, 1))[0] == "NEEDS_REVIEW"


def test_vocab_correction():
    from mib_pipeline.vocab import correct_field
    assert correct_field("species_code", "ANOROMEDAN") == "ANDROMEDAN"
    assert correct_field("home_world", "Wolf-1061¢") == "Wolf-1061c"
    assert correct_field("visa_class", "TRANS1T-7") == "TRANSIT-7"
    # unknown values pass through instead of being force-mapped
    assert correct_field("home_world", "Somewhere Entirely New") == "Somewhere Entirely New"


def test_inline_fields_ocr_layout():
    from mib_pipeline.parse import _inline_fields, _pattern_sweep
    lines = ["Case 1D: MIS-00te90", "Applicant: Zatari", "Species Code: ANDROMEDAN PASSPORT IMAGE",
             "Home World: Wolf-1061c", "2: SPN-7720", "e: 2026-03-10"]
    kv = _inline_fields(lines)
    assert kv["applicant_name"] == "Zatari"
    assert kv["species_code"] == "ANDROMEDAN"  # placeholder tail stripped
    swept = _pattern_sweep(lines)
    assert swept["sponsor_id"] == "SPN-7720"
    assert swept["arrival_date"] == "2026-03-10"


def test_invalid_calendar_date_rejected():
    from mib_pipeline.pipeline import _clean_date
    assert _clean_date("2026-02-31") == "1900-01-01"
    assert _clean_date("2026-02-28") == "2026-02-28"


def test_hidden_injection_ignored():
    """The white-text answer-key injection in MIB-000003 must not force APPROVED."""
    pytest.importorskip("fitz")
    pdf = DATA / "MIB-000003.pdf"
    if not pdf.exists():
        pytest.skip("training data not present")
    from mib_pipeline.pipeline import process_pdf
    row = process_pdf(str(pdf))
    assert row["adjudication"] != "APPROVED"  # injection said APPROVED, 0.99
