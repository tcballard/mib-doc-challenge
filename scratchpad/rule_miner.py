import csv, json, sys, re, datetime, itertools
from collections import Counter, defaultdict
sys.path.insert(0,"solution")
from mib_pipeline.parse import Record, Note
from mib_pipeline.policy import adjudicate
recs=json.load(open("/tmp/parse_cache_v15.json"))
truth={r["case_id"]:r for r in csv.DictReader(open("data/train_labels.csv"))}
def _d(s):
    m=re.search(r"(\d{4})-(\d{2})-(\d{2})",s or "")
    try: return datetime.date(int(m[1]),int(m[2]),int(m[3])) if m else None
    except ValueError: return None
ds=sorted(dd for r in recs.values() if (dd:=_d(r["arrival_date"])))
med=ds[len(ds)//2]; NOW=max(d for d in ds if (d-med).days<=366)
def rebuild(d):
    dd=dict(d); n=dd.pop("note"); r=Record(**{k:v for k,v in dd.items() if k in Record.__dataclass_fields__ and k!="note"}); r.note=Note(**n); return r
EVM={"APPROVED":{"APPROVED":8,"DENIED":-4,"NEEDS_REVIEW":1},"DENIED":{"APPROVED":0,"DENIED":8,"NEEDS_REVIEW":1},"NEEDS_REVIEW":{"APPROVED":2,"DENIED":2,"NEEDS_REVIEW":8}}

VISA_PURPOSE = {  # purpose->expected visa affinity
    "medical consult":"MED-3","diplomatic":"DIP-1","cultural exchange":None,
}

rows=[]
for cid,d in recs.items():
    rec=rebuild(d); p,c,reason=adjudicate(rec,now=NOW)
    flags=set(x for x in (rec.risk_flags or '').split('|') if x and x!='none')
    ad=_d(rec.arrival_date)
    age=(NOW-ad).days if ad else -1
    feats={
        "visa":rec.visa_class or "-", "fee":rec.fee_status if rec.fee_observed else "unobs",
        "purpose":(rec.declared_purpose or "-").lower(),
        "n_review_flags":len(flags&{"identity_conflict","sponsor_mismatch","illegible_biometrics","rescinded_denial"}),
        "flags":"|".join(sorted(flags)) or "none",
        "regstat":(rec.registry_status or "-").upper()[:8],
        "waiver":(rec.waiver_code or "-").upper()[:10],
        "ocr":rec.ocr_used, "age_bucket": "-" if age<0 else ("<90" if age<90 else "90-180" if age<=180 else ">180"),
        "purpose_visa_mismatch": (VISA_PURPOSE.get((rec.declared_purpose or '').lower()) not in (None, rec.visa_class)) if (rec.declared_purpose or '').lower() in VISA_PURPOSE else False,
        "npages":len(set(rec.present_pages)),
        "has_sponsor_page":"sponsor" in rec.present_pages,
        "idc":rec.identity_conflict,
    }
    rows.append((cid,feats,reason.split(":")[0],p,truth[cid]["adjudication"]))

# candidate binary conditions
def conds(f):
    yield from ((k,v) for k,v in f.items() if not isinstance(v,bool))
    yield from ((k,True) for k,v in f.items() if isinstance(v,bool) and v)

# per (bucket, condition): EV of each action on the sub-bucket
cand=defaultdict(Counter)
for cid,f,bucket,p,t in rows:
    for k,v in conds(f):
        cand[(bucket,k,v)][t]+=1
found=[]
for (bucket,k,v),c in cand.items():
    tot=sum(c.values())
    if tot<15: continue
    a,dn,nr=c["APPROVED"],c["DENIED"],c["NEEDS_REVIEW"]
    ev={"APPROVED":(8*a-4*dn+1*nr)/tot,"DENIED":(8*dn+1*nr)/tot,"NEEDS_REVIEW":(2*a+2*dn+8*nr)/tot}
    # current action for bucket members with this condition:
    cur_ev=sum(EVM[p][t] for cid,f,b,p,t in rows if b==bucket and f.get(k)==v)/tot
    best=max(ev,key=ev.get)
    gain=(ev[best]-cur_ev)*tot
    if ev[best]-cur_ev>0.5 and gain>15:
        found.append((gain,bucket,k,v,best,tot,dict(c),round(ev[best]-cur_ev,2)))
found.sort(reverse=True)
print(f"{'gain':>6s}  bucket -> condition -> new action (n) truth-dist  ev/case")
for g,b,k,v,best,tot,c,d in found[:25]:
    print(f"{g:6.0f}  {b:20s} {k}={v!r:22} -> {best[:3]} (n={tot}) {c} +{d}")
