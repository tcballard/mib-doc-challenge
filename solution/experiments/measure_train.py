import csv, json, sys, re, datetime
from collections import Counter
sys.path.insert(0, "solution")
from mib_pipeline.parse import Record, Note
from mib_pipeline.policy import adjudicate, harvest_policy_facts
from mib_pipeline.pipeline import _format_row

recs = json.load(open(sys.argv[1] if len(sys.argv)>1 else "/tmp/parse_cache_v21.json"))
truth = {r["case_id"]: r for r in csv.DictReader(open("data/train_labels.csv"))}
def _d(s):
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", s or "")
    try: return datetime.date(int(m[1]), int(m[2]), int(m[3])) if m else None
    except ValueError: return None
_dates = sorted(dd for r in recs.values() if (dd := _d(r["arrival_date"])))
_med = _dates[len(_dates)//2]
NOW = max(d for d in _dates if (d - _med).days <= 366)
def rebuild(d):
    d = dict(d); n = d.pop("note")
    r = Record(**{k: v for k, v in d.items() if k in Record.__dataclass_fields__ and k != "note"})
    r.note = Note(**n); return r
def cls_pts(t, p):
    if t == p: return 8
    if t == "DENIED" and p == "APPROVED": return -4
    if p == "NEEDS_REVIEW": return 2
    if t == "NEEDS_REVIEW": return 1
    return 0
def nz(s): return " ".join(str(s or "").strip().split()).casefold()
def nf(v):
    v = nz(v)
    if v in ("", "none", "null", "unknown"): return "none"
    return "|".join(sorted(p for p in v.split("|") if p))
FIELDS = ["applicant_name","species_code","home_world","visa_class","sponsor_id","arrival_date","declared_purpose","risk_flags","fee_status"]
W = {"applicant_name":5,"species_code":6,"home_world":5,"visa_class":5,"sponsor_id":5,"arrival_date":4,"declared_purpose":3,"risk_flags":8,"fee_status":4}
_all = {cid: rebuild(d) for cid, d in recs.items()}
harvest_policy_facts(_all.values())
raw=0;n=0;cat=0;correct=0;er=0.0;em=0.0;briers=[];conf=Counter();fa=Counter();ft=Counter()
for cid, d in recs.items():
    rec = _all[cid]; t = truth[cid]
    row = _format_row(rec, NOW)
    p, c = row["adjudication"], row["confidence"]
    raw += cls_pts(t["adjudication"], p); n += 1
    conf[(t["adjudication"], p)] += 1
    ac = t["adjudication"] == p
    if ac: correct += 1
    briers.append((c - (1.0 if ac else 0.0))**2)
    if t["adjudication"]=="DENIED" and p=="APPROVED": cat += 1
    for fld in FIELDS:
        pv = row[fld]
        tv = nf(t[fld]) if fld=="risk_flags" else nz(t[fld])
        pvn = nf(pv) if fld=="risk_flags" else nz(pv)
        em += W[fld]; ft[fld]+=1
        if tv==pvn: er += W[fld]; fa[fld]+=1
cls=80*raw/(8*n); ext=50*er/em; mb=sum(briers)/len(briers); cal=20*max(0,1-2*mb)
print(f"n={n} acc={correct/n:.3f} catastrophic={cat}")
print(f"classification {cls:.2f}/80  extraction {ext:.2f}/50  calibration {cal:.2f}/20")
print(f"TOTAL {cls+ext+cal:.2f}/150   brier={mb:.3f}")
for f in FIELDS: print(f"  {f:16s} {fa[f]/ft[f]:.3f}")
