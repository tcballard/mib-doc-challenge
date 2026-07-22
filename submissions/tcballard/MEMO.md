# MIB Doc Challenge — Technical Memo

**Author:** tcballard
**Solution code:** [`solution/`](../../solution) in this repository (offline, CPU-only, Dockerized)

## 1. Approach

The system is a deterministic, **trust-aware evidence engine**, not a black-box
classifier. It mirrors the field manual: extract only what is *visibly* on the
page, resolve conflicts by the manual's evidence precedence, then apply
adjudication rules.

**Pipeline stages**

1. **Extraction (`extract.py`).** PyMuPDF yields every text span with color,
   size, and bbox. Each span is tagged **visible** or **hidden**. Hidden =
   white-on-white, sub‑5pt, or painted outside the page crop. Hidden text is
   retained for auditing but *never* fills a field or influences a decision.
   This is the entire prompt-injection defense: the planted white-text "answer
   keys," out-of-crop instructions, and barcode payloads are structurally
   excluded rather than filtered by keyword.
2. **OCR fallback (`ocr.py`).** Applied **per page**, only to pages that are an
   image with no usable text layer (fully-scanned packets *and* the scanned
   biometric slip inside an otherwise-digital packet). Each such page gets two
   complementary Tesseract passes at 300 dpi — PSM 6 reads dense adjudicator
   notes better, PSM 4 reads labeled field lines better — and the parser
   consumes the deduplicated union, cherry-picking whichever pass rendered each
   line legibly. A hard 12s per-page timeout guards against Tesseract's
   pathological blow-ups on noisy scans at high DPI, with a cheap 200 dpi
   fallback when both passes come back empty. OCR output is treated as visible
   evidence because it is the rendered document; it recovers the majority of
   the `biohazard_red` / `active_warrant` risk flags that live only on
   rasterized biometric slips, plus ~28% of adjudicator findings.
3. **Parsing & consolidation (`parse.py`).** Each page type (intake form,
   registry extract, fee receipt, biometric slip, sponsor letter, adjudicator
   note) is parsed to key/value pairs and merged under the manual's precedence
   (adjudicator note > intake form > biometric > sponsor letter > registry).
   Robustness details that mattered: digital pages stack label/value vertically
   (direction chosen by *typed validation*) while scanned pages render fields
   inline, so both layouts are parsed with OCR-tolerant fuzzy label matching;
   a typed-pattern sweep recovers values whose labels OCR destroyed (SPN ids,
   ISO dates, visa classes, closed-vocabulary species/worlds/purposes);
   OCR character noise is repaired against the closed vocabularies
   ("ANOROMEDAN" → "ANDROMEDAN"), which also fixes downstream policy checks
   like embargo-world matching; watermark and placeholder strings are rejected
   from value slots; damage markers (`[NAME CUT OUT]`, `UNREADABLE`) are
   treated as absent; identity comparison across pages/passes is fuzzy so OCR
   variance of the same name doesn't fake an identity conflict; and OCR-garbled
   adjudicator findings are recovered with fuzzy matching scoped to the text
   *before* the reason (so a "rescinded denial" reason cannot flip a review
   finding to a denial).
4. **Adjudication (`policy.py`).** A visible/OCR‑recovered adjudicator **Finding
   is authoritative** (precedence #1) — this path is 100% accurate on training
   labels and covers ~24% of cases. Otherwise the manual's rule chain runs:
   disqualifying flags, `TRANSIT-7`, transit declared-purpose, revoked sponsor,
   embargoed home world, unpaid fee, and staleness → `DENIED`; unknown fee,
   missing arrival date, review-only flags, identity conflict → `NEEDS_REVIEW`.
5. **Confidence.** Each decision path emits a confidence equal to its measured
   training accuracy, which is the Brier-minimizing choice.

**Policy facts from data.** Embargoed worlds (`Wolf-1061c`, `TRAPPIST-1e`,
`Eris Relay`) and revoked sponsors were inferred from labeled examples and
training adjudicator‑note reasons, exactly as the manual invites
("Other revoked sponsors may appear in examples"). These are general policy
facts, reproducible via `solution/scripts/derive_policy_lists.py`, not answers
keyed to specific PDFs. Staleness uses the batch's most recent arrival date as
"now," so it adapts to any data era instead of a hardcoded date.

## 2. Results (training set, official evaluator)

| Section | Score |
| --- | --- |
| Classification | 62.2 / 80 |
| Field extraction | 40.6 / 50 |
| Confidence calibration | 15.0 / 20 |
| **Deterministic total** | **≈117.7 / 150** |
| Catastrophic false approvals | 36 / 1000 |

Training extraction is a *lower bound* on the graded score: many of its "misses"
are damaged fields (`[DATE WASHED OUT]`, cut-out names) that the private labels
mark unrecoverable and remove from the maximum.

## 3. The pivotal diagnostic: extraction *is* classification

Running the finished policy on **perfect fields** (the truth CSV) scores 66+/80
with near-perfect denial recall — so the denials that once looked "hidden" were
really extraction failures feeding the rules wrong values. That reframed the
whole project: every subsequent point came from reading documents better
(recovering risk flags from degraded slips, deskewing rotated scans), not from a
smarter classifier. A per-field sensitivity analysis (swap one extracted field
at a time for truth) showed `risk_flags` alone gates ~7 of the ~11 reachable
classification points, which is where the OCR effort was aimed.

Decision routing is expected-value-optimal against the scoring matrix, measured
per decision path: clean-and-complete packets approve; ambiguous signals
(transit purpose, stale-looking dates on OCR'd packets, salvaged-field-only
completeness) route to review. Whitelist-salvaged values are reported for
extraction but deliberately do not count toward the trusted-evidence bar that
unlocks an approval.

## 4. Failure modes

- **Flags with no surviving evidence.** The remaining risk-flag misses have no
  biometric slip, no note mention, no stamp — verified by rendering and
  red-ink-isolated OCR of every such packet. This matches the challenge's
  documented `unrecoverable_fields` design.
- **Illegible biometric slips.** Heavy-noise recovery reads flags through
  ~50% character corruption, but some slips are beyond any threshold.
- **Learned-list generalization.** Embargo/revoked lists come from training; a
  private test that introduces new embargoed worlds without an adjudicator note
  would slip through. The note path and the registry "EMBARGO REVIEW" status
  (both stated in-document) are the generalizable backstops.
- **Catastrophic false approvals** run ~37/1000 on train — the price of
  EV-optimal approval on a bucket with residual label noise; each is a case
  whose denial evidence is absent from the packet.

## 5. Experiments run (and what they showed)

1. **ML on the residual — negative, kept out.** Cross-validated GBT/logistic
   models with decision-theoretic label choice score *below* the rule engine
   (66.8–69.1 vs 71.4 cls+cal) with 6–10× the catastrophic false approvals.
   Reproducible in `solution/experiments/residual_model_cv.py`.
2. **Multi-pass OCR — positive, shipped.** A bounded union of two 300 dpi
   segmentation passes (PSM 6 for notes, PSM 4 for field lines) with hard
   per-page timeouts, plus a binarized pass on weak-yield pages, plus deskew.
   Naive always-multi-DPI merging is a tail-latency trap — Tesseract can grind
   minutes on noisy pages at high DPI.
3. **Visual stamp detection — negative, documented.** No denial stamps exist in
   the corpus; red ink on the relevant packets reads FILED/COPY/MIB (decoys).
   Swept via vector inspection, pixel statistics, and red-channel OCR.
4. **Barcodes — negative.** No decodable barcodes exist; "BARCODE PAYLOAD"
   strings are text-layer traps (correctly ignored as instructions).

## 6. What I'd do with another week

1. **Tesseract C API (`tesserocr`)** instead of subprocess-per-page (~35%
   runtime), reinvested in more preprocessing variants (multiple binarization
   thresholds, adaptive thresholding) — different thresholds unlock different
   degraded pages.
2. **Label-anchored region re-OCR**: locate a field's label via word bounding
   boxes, crop the value region, upscale, single-line whitelist OCR — the
   precision version of the current whole-page whitelist salvage.
3. **Confidence via held‑out calibration** (isotonic regression on an
   out‑of‑fold split) rather than in‑sample bucket accuracy, to derisk the mild
   optimism of calibrating on the same data the rules were tuned on.
