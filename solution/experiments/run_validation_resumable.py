#!/usr/bin/env python3
"""Resumable validation-set prediction run.

Parses each PDF through the full pipeline (identical to solution.py) but
checkpoints per-packet Records to an append-only JSONL, so a killed run
resumes where it stopped. When all packets are parsed, adjudicates with the
batch reference date and writes predictions — byte-identical semantics to
`pipeline.run()`.
"""
import sys, json, dataclasses, os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "solution"))
os.environ.setdefault("OMP_THREAD_LIMIT", "1")

from mib_pipeline.pipeline import parse_one, batch_reference_date, _format_row, OUTPUT_FIELDS
from mib_pipeline.parse import Record, Note
import multiprocessing as mp

PARTIAL = Path("/tmp/validation_records.partial.jsonl")
OUT = ROOT / "submissions/tcballard/predictions.jsonl"


def work(path):
    try:
        rec = parse_one(path)
    except Exception:
        rec = Record(case_id=Path(path).stem, scanned=True)
    return dataclasses.asdict(rec)


def rebuild(d):
    d = dict(d); n = d.pop("note")
    r = Record(**{k: v for k, v in d.items() if k in Record.__dataclass_fields__ and k != "note"})
    r.note = Note(**n)
    return r


if __name__ == "__main__":
    done = {}
    if PARTIAL.exists():
        for line in PARTIAL.read_text().splitlines():
            try:
                row = json.loads(line)
                done[row["case_id"]] = row
            except Exception:
                continue
    files = [str(p) for p in sorted((ROOT / "data/validation").glob("*.pdf"))
             if p.stem not in done]
    print(f"resuming: {len(done)} done, {len(files)} to parse", flush=True)
    with open(PARTIAL, "a") as f, mp.Pool(4) as pool:
        for i, d in enumerate(pool.imap_unordered(work, files, chunksize=4), 1):
            done[d["case_id"]] = d
            f.write(json.dumps(d) + "\n")
            f.flush()
            if i % 250 == 0:
                print(f"  {i}/{len(files)}", flush=True)
    records = [rebuild(d) for d in done.values()]
    now = batch_reference_date(records)
    rows = sorted((_format_row(r, now) for r in records), key=lambda r: r["case_id"])
    with open(OUT, "w") as f:
        for r in rows:
            f.write(json.dumps({k: r[k] for k in OUTPUT_FIELDS}, sort_keys=True) + "\n")
    print(f"wrote {len(rows)} predictions to {OUT}")
