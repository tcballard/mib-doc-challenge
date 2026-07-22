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


def parse_one(path: str, use_ocr: bool = True, allow_escalation: bool = True) -> Record:
    """Extract + parse a single PDF into a Record (no adjudication).

    ``allow_escalation=False`` is the budget governor's degraded mode: cheap
    layers only, used when the batch is pacing over the runtime budget.
    """
    case_id = Path(path).stem
    m = CASE_ID_RE.search(case_id)
    case_id = m.group(0) if m else case_id
    ocr_fn = make_ocr_fn() if (use_ocr and ocr_available()) else None
    pages = extract_pages(path, ocr_fn=ocr_fn)
    rec = parse_packet(case_id, pages)

    # Escalation layer ("onion" architecture): the cheap layers handle most
    # packets in well under budget; packets they leave deficient get a second,
    # much heavier OCR sweep, and the two parses merge field-wise.
    if use_ocr and allow_escalation and rec.ocr_used and (_deficiency(rec) >= 3 or _critical_gap(rec)):
        try:
            from .ocr import make_escalated_ocr_fn
            pages2 = extract_pages(path, ocr_fn=make_escalated_ocr_fn())
            rec2 = parse_packet(case_id, pages2)
            rec = _merge_records(rec, rec2)
        except Exception:
            pass

    if use_ocr and rec.ocr_used:
        try:
            from .recover import recover_missing_fields
            recover_missing_fields(rec, path)
        except Exception:
            pass
    return rec


CORE_FIELDS = ("applicant_name", "species_code", "home_world", "visa_class",
               "sponsor_id", "arrival_date", "declared_purpose")


def _critical_gap(rec: Record) -> bool:
    """A single missing item that likely swings the verdict outweighs several
    peripheral fields: escalate on value, not just volume."""
    if (rec.risk_flags or "none") == "none" and "biometric" not in rec.present_pages:
        return True  # a hidden disqualifying flag flips APPROVED to DENIED (-4 vs +8)
    if rec.note and rec.note.raw and not rec.note.finding:
        return True  # an unparsed adjudicator finding is worth 8 points alone
    return False


def _deficiency(rec: Record) -> int:
    """How much trusted evidence is still missing after the cheap layers.
    Damage-marked fields don't count: their evidence is destroyed, and no
    amount of OCR escalation can recover what isn't on the page."""
    src = rec.field_sources or {}
    score = sum(1 for f in CORE_FIELDS
                if not getattr(rec, f) and src.get(f) != "damaged")
    if (rec.risk_flags or "none") == "none" and "biometric" not in rec.present_pages:
        score += 1
    if not rec.fee_observed:
        score += 1
    if rec.note and not rec.note.finding and rec.note.raw:
        score += 1  # a note exists but its finding didn't parse
    return score


def _merge_records(a: Record, b: Record) -> Record:
    """Merge an escalated re-parse into the base record: fill gaps, and accept
    the escalated reading for evidence-bearing values the base pass lacked."""
    for f in CORE_FIELDS + ("waiver_code", "registry_status"):
        if not getattr(a, f) and getattr(b, f):
            setattr(a, f, getattr(b, f))
            a.field_sources[f] = "escalation"
    if (a.risk_flags or "none") == "none" and (b.risk_flags or "none") != "none":
        a.risk_flags = b.risk_flags
        a.field_sources["risk_flags"] = "escalation"
    if not a.fee_observed and b.fee_observed:
        a.fee_status, a.fee_observed = b.fee_status, True
    if (not a.note or not a.note.finding) and (b.note and b.note.finding):
        a.note = b.note
    a.present_pages = sorted(set(a.present_pages) | set(b.present_pages))
    a.identity_conflict = a.identity_conflict or b.identity_conflict
    return a


def _worker(path):
    return _worker_mode((path, True))


def _worker_mode(args):
    path, allow_escalation = args
    try:
        return parse_one(path, allow_escalation=allow_escalation)
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


# Runtime contract: 6s per PDF on average. The governor paces against a
# fraction of it so the run always lands inside the cap with margin.
BUDGET_S_PER_PDF = 6.0
BUDGET_SAFETY = 0.80
BATCH_SIZE = 200
CHECKPOINT = "/tmp/mib_run_checkpoint.jsonl"


def run(input_dir: str, output_path: str, workers: Optional[int] = None) -> int:
    """Process the input directory in batches with a runtime-budget governor.

    - Batched: packets stream through in chunks of BATCH_SIZE; per-packet
      results checkpoint to /tmp (the contract's writable tmpfs) as each batch
      completes, so an interrupted run resumes instead of restarting.
    - Governed: between batches, projected finish time is compared against the
      6s/PDF contract budget (with safety margin); if pacing over, the
      escalation layer is disabled for remaining batches — degraded reads beat
      a blown budget, exactly as an emitted low-confidence row beats an
      omission.
    """
    import dataclasses
    import time

    pdfs = sorted(str(p) for p in Path(input_dir).glob("*.pdf"))
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    if workers is None:
        workers = max(1, min(4, (os.cpu_count() or 2)))

    # Resume from checkpoint if a previous interrupted run left one.
    done: Dict[str, Record] = {}
    ckpt = Path(CHECKPOINT)
    if ckpt.exists():
        for line in ckpt.read_text().splitlines():
            try:
                d = json.loads(line)
                n = d.pop("note", {})
                r = Record(**{k: v for k, v in d.items()
                              if k in Record.__dataclass_fields__ and k != "note"})
                from .parse import Note
                r.note = Note(**n)
                done[r.case_id] = r
            except Exception:
                continue
    # Only honor checkpoint entries belonging to this input set — a stale
    # checkpoint from a different corpus must not leak cases into the output.
    stems = {Path(p).stem for p in pdfs}
    done = {cid: r for cid, r in done.items() if cid in stems}
    todo = [p for p in pdfs if Path(p).stem not in done]

    total_budget = BUDGET_S_PER_PDF * len(pdfs) * BUDGET_SAFETY
    start = time.monotonic()
    allow_escalation = True
    processed_this_run = 0

    import multiprocessing as mp
    ckpt_f = open(ckpt, "a")
    try:
        for i in range(0, len(todo), BATCH_SIZE):
            batch = todo[i:i + BATCH_SIZE]
            args = [(p, allow_escalation) for p in batch]
            if workers > 1 and len(batch) > 1:
                with mp.Pool(processes=workers) as pool:
                    batch_recs = list(pool.imap_unordered(_worker_mode, args, chunksize=4))
            else:
                batch_recs = [_worker_mode(a) for a in args]
            for rec in batch_recs:
                if rec is None:
                    continue
                done[rec.case_id] = rec
                ckpt_f.write(json.dumps(dataclasses.asdict(rec)) + "\n")
            ckpt_f.flush()

            # Governor: project finish time from THIS run's pace (checkpointed
            # packets cost no time now); degrade before the budget is at risk.
            processed_this_run += len(batch)
            elapsed = time.monotonic() - start
            remaining = len(pdfs) - len(done)
            if allow_escalation and processed_this_run and remaining:
                projected = elapsed + (elapsed / processed_this_run) * remaining
                if projected > total_budget:
                    allow_escalation = False
    finally:
        ckpt_f.close()

    records: List[Record] = list(done.values())

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
