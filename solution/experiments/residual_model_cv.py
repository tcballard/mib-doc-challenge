#!/usr/bin/env python3
"""Does an offline ML model on the *residual* (non-note, ambiguous) cases beat
the rule engine?

Answer, empirically: no. This script reproduces that finding with honest 5-fold
cross-validation. It compares, on the full training set:

  * the rule engine alone, versus
  * a hybrid that keeps the high-precision rules but hands the ambiguous cases
    to a model (gradient boosting or logistic regression), choosing the label
    that maximizes expected challenge score from the model's probabilities.

Run from the repo root (needs the training data + a built parse cache; see
build_parse_cache below):

    python3 solution/experiments/residual_model_cv.py

Requires: scikit-learn, numpy (dev-only; not part of the runtime image).
"""
import csv
import datetime
import json
import re
import sys
from collections import Counter
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "solution"))

from mib_pipeline.parse import Record, Note, parse_packet
from mib_pipeline.extract import extract_pages
from mib_pipeline.policy import adjudicate
from mib_pipeline.features import featurize
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import make_pipeline
from sklearn.model_selection import StratifiedKFold

CACHE = Path("/tmp/parse_cache_ocr.json")
CLASSES = ["APPROVED", "DENIED", "NEEDS_REVIEW"]
CI = {c: i for i, c in enumerate(CLASSES)}
# Raw challenge points: EVM[prediction][truth].
EVM = {
    "APPROVED":     {"APPROVED": 8, "DENIED": -4, "NEEDS_REVIEW": 1},
    "DENIED":       {"APPROVED": 0, "DENIED": 8,  "NEEDS_REVIEW": 1},
    "NEEDS_REVIEW": {"APPROVED": 2, "DENIED": 2,  "NEEDS_REVIEW": 8},
}
# Rule reasons trusted as high-precision; everything else is "ambiguous".
CONFIDENT = {
    "adjudicator_note", "disqualifying_flag", "transit_class", "embargo_world",
    "revoked_sponsor", "fee_unpaid", "fee_unknown", "stale_application",
    "review_flag", "no_trusted_evidence",
}


def _d(s):
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", s or "")
    return datetime.date(int(m[1]), int(m[2]), int(m[3])) if m else None


def build_parse_cache():
    """Parse every training PDF once (with OCR) and cache the Records."""
    import dataclasses
    import multiprocessing as mp
    from mib_pipeline.ocr import make_ocr_fn

    def work(path):
        ocr = make_ocr_fn()
        return Path(path).stem, dataclasses.asdict(
            parse_packet(Path(path).stem, extract_pages(path, ocr_fn=ocr))
        )

    files = [str(p) for p in sorted((ROOT / "data/train").glob("*.pdf"))]
    out = {}
    with mp.Pool(4) as pool:
        for cid, d in pool.imap_unordered(work, files, chunksize=8):
            out[cid] = d
    CACHE.write_text(json.dumps(out))
    return out


def rebuild(d):
    d = dict(d); n = d.pop("note")
    r = Record(**{k: v for k, v in d.items() if k in Record.__dataclass_fields__ and k != "note"})
    r.note = Note(**n)
    return r


def decide_ev(prob):
    """Pick the label maximizing expected challenge score; conf = its prob."""
    best, bev = None, -1e9
    for p in CLASSES:
        ev = sum(EVM[p][t] * prob[CI[t]] for t in CLASSES)
        if ev > bev:
            bev, best = ev, p
    return best, float(prob[CI[best]])


def score(items, get_pred):
    raw, briers, cat, n = 0, [], 0, 0
    cm = Counter()
    for i, it in enumerate(items):
        t = it[2]
        p, c = get_pred(i, it)
        raw += EVM[p][t]; n += 1
        if t == "DENIED" and p == "APPROVED":
            cat += 1
        briers.append((c - (1.0 if t == p else 0.0)) ** 2)
        cm[(t, p)] += 1
    cls = 80 * raw / (8 * n)
    mb = sum(briers) / len(briers)
    cal = 20 * max(0.0, 1 - 2 * mb)
    acc = sum(v for (t, p), v in cm.items() if t == p) / n
    return dict(cls=cls, cal=cal, total=cls + cal, cat=cat, acc=acc)


def main():
    recs = json.loads(CACHE.read_text()) if CACHE.exists() else build_parse_cache()
    labels = {r["case_id"]: r for r in csv.DictReader(open(ROOT / "data/train_labels.csv"))}
    now = max(dd for r in recs.values() if (dd := _d(r["arrival_date"])))

    items = []
    for cid, d in recs.items():
        rec = rebuild(d)
        p, c, reason = adjudicate(rec, now=now)
        items.append((cid, rec, labels[cid]["adjudication"], p, c, reason.split(":", 1)[0]))

    amb = [i for i, it in enumerate(items) if it[5] not in CONFIDENT]
    X = np.array([featurize(items[i][1], now) for i in amb])
    y = np.array([CI[items[i][2]] for i in amb])
    skf = StratifiedKFold(5, shuffle=True, random_state=0)

    models = {
        "gbt": lambda: HistGradientBoostingClassifier(
            max_depth=3, max_iter=200, learning_rate=0.05,
            l2_regularization=2.0, min_samples_leaf=30, random_state=0),
        "logreg": lambda: make_pipeline(StandardScaler(),
            LogisticRegression(C=0.5, max_iter=2000)),
    }

    base = score(items, lambda i, it: (it[3], it[4]))
    print(f"confident-rule cases: {len(items)-len(amb)}  ambiguous: {len(amb)}")
    print(f"RULES baseline:      cls={base['cls']:.2f} cal={base['cal']:.2f} "
          f"total={base['total']:.2f} catastrophic={base['cat']} acc={base['acc']:.3f}")

    for name, factory in models.items():
        oof = {}
        for tr, te in skf.split(X, y):
            clf = factory(); clf.fit(X[tr], y[tr])
            pr = clf.predict_proba(X[te])
            prob = np.zeros((len(te), 3))
            for ci, c in enumerate(clf.classes_):
                prob[:, c] = pr[:, ci]
            for k, ti in enumerate(te):
                oof[amb[ti]] = decide_ev(prob[k])
        res = score(items, lambda i, it: oof[i] if i in oof else (it[3], it[4]))
        print(f"HYBRID {name:7s}[ev]: cls={res['cls']:.2f} cal={res['cal']:.2f} "
              f"total={res['total']:.2f} catastrophic={res['cat']} acc={res['acc']:.3f}")


if __name__ == "__main__":
    main()
