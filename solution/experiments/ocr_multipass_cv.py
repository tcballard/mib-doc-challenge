import csv, json, sys, time, io, random
sys.path.insert(0, "solution")
from pathlib import Path
from collections import Counter
import fitz, pytesseract
from PIL import Image
from mib_pipeline.extract import extract_pages
from mib_pipeline.parse import parse_packet
import multiprocessing as mp

truth = {r["case_id"]: r for r in csv.DictReader(open("data/train_labels.csv"))}
cache = json.load(open("/tmp/parse_cache_v2.json"))
def nz(s): return " ".join(str(s or "").strip().split()).casefold()
FIELDS = ["applicant_name","species_code","home_world","visa_class","sponsor_id","arrival_date","declared_purpose"]

cands = []
for cid, rec in cache.items():
    if not rec["ocr_used"]: continue
    miss = sum(1 for f in FIELDS if nz(truth[cid][f]) != nz(rec[f]))
    if miss >= 1: cands.append(cid)
random.seed(7)
sample = random.sample(sorted(cands), 60)

CONFIGS = [("c200p4",200,4),("c300p4",300,4),("c300p6",300,6)]
def make_fn(dpi, psm):
    def fn(page):
        mat = fitz.Matrix(dpi/72.0, dpi/72.0)
        pix = page.get_pixmap(matrix=mat, colorspace=fitz.csGRAY)
        img = Image.open(io.BytesIO(pix.tobytes("png")))
        txt = pytesseract.image_to_string(img, config=f"--oem 1 --psm {psm}")
        return [s.strip() for s in txt.splitlines() if s.strip()]
    return fn

def work(args):
    cid, name, dpi, psm = args
    rec = parse_packet(cid, extract_pages(f"data/train/{cid}.pdf", ocr_fn=make_fn(dpi,psm)))
    return cid, name, {f: getattr(rec,f) for f in FIELDS}

jobs = [(cid,n,d,p) for cid in sample for (n,d,p) in CONFIGS]
out = {}
t0=time.time()
with mp.Pool(4) as pool:
    for cid,name,vals in pool.imap_unordered(work, jobs, chunksize=4):
        out.setdefault(cid,{})[name]=vals
print(f"{len(jobs)} parses in {time.time()-t0:.0f}s")

def acc_of(getval):
    a=t=0
    for cid in sample:
        for f in FIELDS:
            t+=1
            if nz(truth[cid][f])==nz(getval(cid,f)): a+=1
    return a,t

for name,_,_ in CONFIGS:
    a,t=acc_of(lambda cid,f: out[cid][name][f])
    print(f"{name}: {a}/{t} = {a/t:.3f}")

# strategy 1: fill gaps (200 base, others fill empties)
def fill(cid,f):
    for name,_,_ in CONFIGS:
        v=out[cid][name][f]
        if v: return v
    return ""
a,t=acc_of(fill); print(f"fill-gaps: {a}/{t} = {a/t:.3f}")

# strategy 2: majority vote on normalized values (ties -> config order)
def vote(cid,f):
    vals=[out[cid][n][f] for n,_,_ in CONFIGS if out[cid][n][f]]
    if not vals: return ""
    c=Counter(nz(v) for v in vals)
    best,cnt=c.most_common(1)[0]
    if cnt>=2:
        for v in vals:
            if nz(v)==best: return v
    return vals[0]
a,t=acc_of(vote); print(f"majority-vote: {a}/{t} = {a/t:.3f}")
