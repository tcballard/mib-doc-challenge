import csv, json, sys, time, io, random
sys.path.insert(0, "solution")
from pathlib import Path
from collections import Counter
import fitz, pytesseract
from PIL import Image
from mib_pipeline.extract import extract_pages
from mib_pipeline.parse import parse_packet

truth = {r["case_id"]: r for r in csv.DictReader(open("data/train_labels.csv"))}
cache = json.load(open("/tmp/parse_cache_v2.json"))
def nz(s): return " ".join(str(s or "").strip().split()).casefold()
FIELDS = ["applicant_name","species_code","home_world","visa_class","sponsor_id","arrival_date","declared_purpose"]

# sample: OCR packets with >=1 identity-field miss
cands = []
for cid, rec in cache.items():
    if not rec["ocr_used"]: continue
    miss = sum(1 for f in FIELDS if nz(truth[cid][f]) != nz(rec[f]))
    if miss >= 1:
        cands.append((miss, cid))
random.seed(7)
sample = [cid for _, cid in random.sample(cands, 80)]
print(f"candidates={len(cands)} sample={len(sample)}")

def make_fn(dpi, psm, thresh=None):
    def fn(page):
        mat = fitz.Matrix(dpi/72.0, dpi/72.0)
        pix = page.get_pixmap(matrix=mat, colorspace=fitz.csGRAY)
        img = Image.open(io.BytesIO(pix.tobytes("png")))
        if thresh:
            img = img.point(lambda p: 255 if p > thresh else 0)
        txt = pytesseract.image_to_string(img, config=f"--oem 1 --psm {psm}")
        return [s.strip() for s in txt.splitlines() if s.strip()]
    return fn

CONFIGS = {
    "cur_200_psm4": make_fn(200, 4),
    "300_psm4":     make_fn(300, 4),
    "300_psm6":     make_fn(300, 6),
    "400_psm4":     make_fn(400, 4),
    "300_psm4_bin": make_fn(300, 4, thresh=140),
}

results = {}
for name, fn in CONFIGS.items():
    t0 = time.time(); acc = Counter(); tot = Counter()
    recs = {}
    for cid in sample:
        rec = parse_packet(cid, extract_pages(f"data/train/{cid}.pdf", ocr_fn=fn))
        recs[cid] = rec
        for f in FIELDS:
            tot[f] += 1
            if nz(truth[cid][f]) == nz(getattr(rec, f)): acc[f] += 1
    dt = time.time() - t0
    results[name] = recs
    line = " ".join(f"{f.split('_')[0][:4]}={acc[f]/tot[f]:.2f}" for f in FIELDS)
    print(f"{name:14s} {dt/len(sample):.2f}s/pdf  total={sum(acc.values())}/{sum(tot.values())}  {line}")

# merged best-of: 200psm4 base, fill gaps/upgrades from 300psm4 then 300psm6
def better(a, b, f):
    if nz(a) == nz(b): return a
    if not a: return b
    if not b: return a
    return a
merged_acc = 0; merged_tot = 0
for cid in sample:
    for f in FIELDS:
        v = getattr(results["cur_200_psm4"][cid], f)
        for alt in ["300_psm4", "300_psm6"]:
            v = better(v, getattr(results[alt][cid], f), f)
        merged_tot += 1
        if nz(truth[cid][f]) == nz(v): merged_acc += 1
print(f"merged(fill-gaps) total={merged_acc}/{merged_tot}")
