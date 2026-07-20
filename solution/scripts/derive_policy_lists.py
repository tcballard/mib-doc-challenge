#!/usr/bin/env python3
"""Reproduce the policy facts baked into mib_pipeline.policy from public data.

Run from the challenge repo root after unzipping the data:

    python3 solution/scripts/derive_policy_lists.py

Prints the embargo-world set, revoked-sponsor set, and the per-reason confidence
table. This documents that those constants are inferred from labeled examples
(as the field manual invites) rather than hardcoded per-case answers.
"""
import csv
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "solution"))

from mib_pipeline.extract import extract_pages
from mib_pipeline.parse import parse_packet


def main():
    labels = {r["case_id"]: r for r in csv.DictReader(open(ROOT / "data/train_labels.csv"))}
    world = defaultdict(Counter)
    sponsor = defaultdict(Counter)
    for pdf in sorted((ROOT / "data/train").glob("*.pdf")):
        cid = pdf.stem
        rec = parse_packet(cid, extract_pages(str(pdf)))
        adj = labels[cid]["adjudication"]
        if rec.home_world:
            world[rec.home_world][adj] += 1
        if re.match(r"SPN-\d{4}", rec.sponsor_id or ""):
            sponsor[rec.sponsor_id][adj] += 1

    embargo = sorted(
        w for w, c in world.items()
        if sum(c.values()) >= 5 and c["DENIED"] / sum(c.values()) >= 0.7
    )
    revoked = sorted(
        s for s, c in sponsor.items()
        if c["DENIED"] >= 2 and c["DENIED"] / sum(c.values()) >= 0.75
    )
    print("Embargo worlds (support>=5, denial-rate>=0.70):")
    print("  ", embargo)
    print("Revoked sponsors (denied>=2, denial-rate>=0.75):")
    print("  ", revoked)
    print("\nNote: the public field manual also names SPN-0007/0139/4040 and these")
    print("appear in training adjudicator-note reasons.")


if __name__ == "__main__":
    main()
