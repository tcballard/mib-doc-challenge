#!/usr/bin/env python3
"""Parse every training PDF (with OCR) into a cache for fast policy iteration.

Restart-safe: results append to a JSONL as they complete, and already-parsed
case ids are skipped on relaunch. When all packets are present the consolidated
dict is written to /tmp/parse_cache_v16.json.
"""
import sys, json, dataclasses, os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "solution"))
os.environ.setdefault("OMP_THREAD_LIMIT", "1")

from mib_pipeline.pipeline import parse_one
import multiprocessing as mp

PARTIAL = Path("/tmp/parse_cache_v16.partial.jsonl")
OUT = Path("/tmp/parse_cache_v16.json")


def work(path):
    rec = parse_one(path)
    return Path(path).stem, dataclasses.asdict(rec)


if __name__ == "__main__":
    done = {}
    if PARTIAL.exists():
        for line in PARTIAL.read_text().splitlines():
            try:
                row = json.loads(line)
                done[row["case_id"]] = row
            except Exception:
                continue
    files = [str(p) for p in sorted((ROOT / "data/train").glob("*.pdf"))
             if p.stem not in done]
    print(f"resuming: {len(done)} cached, {len(files)} to parse", flush=True)
    with open(PARTIAL, "a") as f, mp.Pool(4) as pool:
        for i, (cid, d) in enumerate(pool.imap_unordered(work, files, chunksize=4), 1):
            done[cid] = d
            f.write(json.dumps(d) + "\n")
            f.flush()
            if i % 100 == 0:
                print(f"  {i}/{len(files)}", flush=True)
    json.dump(done, open(OUT, "w"))
    print("cached", len(done))
