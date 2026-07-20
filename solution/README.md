# MIB Doc Challenge — Solution

An offline, CPU-only document-engineering pipeline that reads a directory of PDF
case packets and emits `predictions.jsonl` (one adjudicated applicant record per
case). No LLMs, no network, no GPU.

## Run

```bash
docker build -t mib-submission solution/
mkdir -p /tmp/mib-output
docker run --rm --network none \
  --mount type=bind,src="$PWD/data/train",dst=/input,readonly \
  --mount type=bind,src="/tmp/mib-output",dst=/output \
  mib-submission /input /output/predictions.jsonl
```

Or locally (needs `tesseract-ocr` on PATH plus `solution/requirements.txt`):

```bash
python3 solution/solution.py data/train /tmp/predictions.jsonl
```

## Architecture

The pipeline is a deterministic, trust-aware evidence engine. Each stage lives in
`mib_pipeline/`:

| Module | Responsibility |
| --- | --- |
| `extract.py` | PDF -> pages, classifying every text span as **visible** (trusted) or **hidden** (white-on-white, sub-5pt, painted outside the page crop). Hidden text is captured but never fills a field — this is the prompt-injection defense. |
| `ocr.py` | Tesseract fallback, applied **per page** only to pages that are an image with no usable text layer. OCR output counts as visible evidence (it is the rendered document). |
| `parse.py` | Label/value parsing of each page type (intake form, registry, fee receipt, biometric slip, sponsor letter, adjudicator note), then cross-page consolidation under the field-manual evidence precedence. Handles value-above/value-below layouts, watermark traps, damage markers, and OCR-garbled adjudicator findings. |
| `policy.py` | Adjudication rules + calibrated confidence. A visible adjudicator finding is authoritative; otherwise the manual's disqualify / review / approve logic applies. |
| `pipeline.py` | Directory driver. Parses packets across a process pool, then adjudicates with a batch-derived "now" for the staleness rule. |

## Key design decisions

- **Visible evidence wins.** Hidden PDF text, out-of-crop text, sub-5pt decoys,
  "SAMPLE DENIAL" watermarks, and barcode payloads are excluded from field
  values and decisions. Fake answer keys planted in white text are ignored.
- **Adjudicator note is precedence #1.** When a legible (or OCR-recoverable)
  "Finding: …" is present it is used directly — this path is 100% accurate on
  the training labels.
- **Conservative on approvals.** A clean, complete packet still carries a
  substantial hidden-denial rate that no visible field predicts. Because a false
  approval is the costliest error, non-diplomatic clean packets route to
  `NEEDS_REVIEW` rather than a speculative `APPROVED`.
- **Calibrated confidence.** Each decision path emits a confidence equal to its
  measured accuracy, which minimizes Brier error.
- **Era-adaptive staleness.** The staleness cutoff is measured against the most
  recent arrival date in the batch, so the rule works on any dataset era without
  a hardcoded calendar date.

## Policy facts learned from labeled examples

`scripts/derive_policy_lists.py` reproduces the embargo-world list, revoked-sponsor
list, and per-reason confidence table from `data/train_labels.csv` and the
training PDFs. These are general policy facts (which worlds are embargoed, which
sponsors are revoked), not answers keyed to specific PDFs.
