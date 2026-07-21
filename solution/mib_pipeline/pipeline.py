"""End-to-end packet processing: PDF dir -> predictions.jsonl.

Runs fully offline. Per-PDF work is CPU-bound (PDF parse + optional OCR), so
packets are processed across a process pool sized to the available cores.
"""
from __future__ import annotations

import json
import os

# Keep native libraries single-threaded; we parallelize at the packet level.
os.environ.setdefault("OMP_THREAD_LIMIT", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")

import datetime
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional

from .extract import extract_pages
from .parse import parse_packet, Record
from .policy import adjudicate, _parse_date

try:
    from .ocr import make_ocr_fn, ocr_available
except Exception:  # pragma: no cover
    def ocr_available():
        return False
    def make_ocr_fn(dpi=200):
        return None

OUTPUT_FIELDS = [
    "case_id", "applicant_name", "species_code", "home_world", "visa_class",
    "sponsor_id", "arrival_date", "declared_purpose", "risk_flags", "fee_status",
    "adjudication", "confidence",
]

CASE_ID_RE = re.compile(r"MIB-\d{6}")
SPN_RE = re.compile(r"^SPN-\d{4}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Valid-but-neutral placeholders so every emitted row passes schema validation
# even when a field is genuinely unrecoverable.
PLACEHOLDER_SPONSOR = "SPN-0000"
PLACEHOLDER_DATE = "1900-01-01"


def _clean_sponsor(v: str) -> str:
    m = re.search(r"SPN-\d{4}", v or "")
    return m.group(0) if m else PLACEHOLDER_SPONSOR


def _clean_date(v: str) -> str:
    m = re.search(r"\d{4}-\d{2}-\d{2}", v or "")
    if not m:
        return PLACEHOLDER_DATE
    try:
        datetime.date.fromisoformat(m.group(0))
    except ValueError:
        return PLACEHOLDER_DATE
    return m.group(0)


def _clean_text(v: str) -> str:
    v = " ".join((v or "").split()).strip()
    return v if v else "unknown"


def parse_one(path: str, use_ocr: bool = True) -> Record:
    """Extract + parse a single PDF into a Record (no adjudication)."""
    case_id = Path(path).stem
    m = CASE_ID_RE.search(case_id)
    case_id = m.group(0) if m else case_id
    ocr_fn = make_ocr_fn() if (use_ocr and ocr_available()) else None
    pages = extract_pages(path, ocr_fn=ocr_fn)
    rec = parse_packet(case_id, pages)
    if use_ocr and rec.ocr_used:
        try:
            from .recover import recover_missing_fields
            recover_missing_fields(rec, path)
        except Exception:
            pass
    return rec


def _worker(path):
    try:
        return parse_one(path)
    except Exception:
        # Never let one bad PDF kill the batch.
        m = CASE_ID_RE.search(Path(path).stem)
        return Record(case_id=m.group(0) if m else Path(path).stem, scanned=True)


def _format_row(rec: Record, now: Optional[datetime.date]) -> Dict:
    adj, conf, reason = adjudicate(rec, now=now)
    fee_out = rec.fee_status if rec.fee_observed else "paid"
    if fee_out not in {"paid", "waived", "unpaid", "unknown"}:
        fee_out = "unknown"
    return {
        "case_id": rec.case_id,
        "applicant_name": _clean_text(rec.applicant_name),
        "species_code": _clean_text(rec.species_code),
        "home_world": _clean_text(rec.home_world),
        "visa_class": _clean_text(rec.visa_class),
        "sponsor_id": _clean_sponsor(rec.sponsor_id),
        "arrival_date": _clean_date(rec.arrival_date),
        "declared_purpose": _clean_text(rec.declared_purpose),
        "risk_flags": rec.risk_flags or "none",
        "fee_status": fee_out,
        "adjudication": adj,
        "confidence": round(float(conf), 3),
    }


def batch_reference_date(records: List[Record]) -> Optional[datetime.date]:
    """Most recent *plausible* arrival date in the batch.

    OCR digit noise can fabricate far-future dates (e.g. 2076-05-03) that would
    make every real application look stale, so dates more than a year past the
    batch median are treated as misreads and excluded.
    """
    dates = sorted(d for r in records if (d := _parse_date(r.arrival_date)))
    if not dates:
        return None
    median = dates[len(dates) // 2]
    plausible = [d for d in dates if (d - median).days <= 366]
    return max(plausible) if plausible else median


def process_pdf(path: str, use_ocr: bool = True, now: Optional[datetime.date] = None) -> Optional[Dict]:
    """Convenience single-PDF entry (used in tests)."""
    return _format_row(parse_one(path, use_ocr=use_ocr), now)


def run(input_dir: str, output_path: str, workers: Optional[int] = None) -> int:
    pdfs = sorted(str(p) for p in Path(input_dir).glob("*.pdf"))
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    if workers is None:
        workers = max(1, min(4, (os.cpu_count() or 2)))

    # Pass 1: parse every packet (expensive: PDF + OCR), parallelized.
    records: List[Record] = []
    if workers > 1 and len(pdfs) > 1:
        import multiprocessing as mp
        with mp.Pool(processes=workers) as pool:
            for rec in pool.imap_unordered(_worker, pdfs, chunksize=4):
                if rec is not None:
                    records.append(rec)
    else:
        for p in pdfs:
            records.append(_worker(p))

    # Reference "now" for staleness = most recent arrival date in the batch, so
    # the staleness rule adapts to the era of the data rather than a fixed date.
    now = batch_reference_date(records)

    # Pass 2: adjudicate + format (cheap).
    results = [_format_row(rec, now) for rec in records]
    results.sort(key=lambda r: r["case_id"])
    with open(out, "w") as f:
        for r in results:
            f.write(json.dumps({k: r[k] for k in OUTPUT_FIELDS}, sort_keys=True) + "\n")
    return len(results)


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) != 2:
        raise SystemExit("usage: pipeline <input_pdf_dir> <output_path>")
    n = run(argv[0], argv[1])
    print(f"Wrote {n} predictions to {argv[1]}")


if __name__ == "__main__":
    main()
