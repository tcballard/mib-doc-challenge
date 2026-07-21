import sys, json, dataclasses, os
sys.path.insert(0, "solution")
os.environ.setdefault("OMP_THREAD_LIMIT", "1")
from pathlib import Path
from mib_pipeline.extract import extract_pages
from mib_pipeline.parse import parse_packet
from mib_pipeline.ocr import make_ocr_fn
import multiprocessing as mp
def work(path):
    rec = parse_packet(Path(path).stem, extract_pages(path, ocr_fn=make_ocr_fn()))
    return Path(path).stem, dataclasses.asdict(rec)
if __name__ == "__main__":
    files = [str(p) for p in sorted(Path("data/train").glob("*.pdf"))]
    out = {}
    with mp.Pool(4) as pool:
        for cid, d in pool.imap_unordered(work, files, chunksize=8):
            out[cid] = d
    json.dump(out, open("/tmp/parse_cache_v6.json", "w"))
    print("cached", len(out))
