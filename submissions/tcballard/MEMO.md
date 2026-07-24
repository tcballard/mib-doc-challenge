# MIB Doc Challenge — Technical Memo

**Author:** tcballard
**Solution code:** [`solution/`](../../solution) in this repository (offline, CPU-only, Dockerized)

**How this was built:** with an AI pair — Claude (Anthropic) wrote code and
ran experiments under my direction. I set the strategy, chose what to
measure, called the ship/kill decisions, and reviewed the results. "We" in
this memo means exactly that partnership. The full development history,
including the dead ends, is on the solution branch.

## 1. What this is

A deterministic, trust-aware evidence engine. It scores **121.9/150** on the
training set with 22 catastrophic false approvals in 1000 cases. No ML model
makes any decision — we tried that, measured it, and it lost (details in
section 5).

The core idea: read only what is visibly on the page, resolve conflicts by the
field manual's evidence precedence, then apply the manual's rules. Every
constant in the system traces to a measurement.

**The pipeline, in five moves:**

1. **Extraction (`extract.py`).** PyMuPDF yields every text span with color,
   size, and position. Each span is tagged visible or hidden — hidden means
   white-on-white, pale ink, sub-5pt, or painted outside the page crop. Hidden
   text never fills a field and never touches a decision. That single
   structural rule is the entire prompt-injection defense: the planted
   white-text "answer keys," out-of-crop instructions, and barcode payloads
   are excluded by construction, not by keyword filter.
2. **OCR fallback (`ocr.py`).** Per page, only where there's no usable text
   layer. Two complementary Tesseract passes at 300 dpi (PSM 6 reads dense
   notes, PSM 4 reads labeled field lines), deskew, orientation detection, and
   an escalation ladder of binarization and upscaling variants for pages the
   cheap passes leave deficient. Hard 12-second per-page timeout — Tesseract
   will happily grind for minutes on a noisy scan if you let it. OCR recovers
   most of the risk flags that live only on rasterized biometric slips, plus
   ~28% of adjudicator findings.
3. **Parsing (`parse.py`).** Each page type parses to key/value pairs and
   merges under the manual's precedence. The robustness work lives here:
   fuzzy label matching for OCR noise, typed-pattern sweeps for values whose
   labels were destroyed, closed-vocabulary repair ("ANOROMEDAN" →
   "ANDROMEDAN"), damage markers treated as absent, and fuzzy identity
   comparison so OCR variance of one name doesn't fake an identity conflict.
4. **Adjudication (`policy.py`).** A visible adjudicator finding is
   authoritative — 100% accurate on training labels, ~24% of cases. Otherwise
   the manual's rule chain runs: disqualifying flags, TRANSIT-7, revoked
   sponsor, embargoed world, unpaid fee, staleness → DENIED; ambiguous
   evidence → NEEDS_REVIEW. Approval requires positive, trusted evidence.
5. **Confidence.** Each decision path reports its measured training accuracy.
   That's the Brier-minimizing choice, and it's honest.

**Policy facts come from data, not hardcoding.** Embargoed worlds
(`Wolf-1061c`, `TRAPPIST-1e`, `Eris Relay`) and revoked sponsors were
inferred from labeled examples and adjudicator-note reasons, exactly as the
manual invites. Reproduce them with
`solution/scripts/derive_policy_lists.py`. Staleness anchors on the batch's
own most recent arrival date, so the system works in any data era.

## 2. Results (training set, official evaluator)

| Section | Score |
| --- | --- |
| Classification | 64.1 / 80 |
| Field extraction | 42.0 / 50 |
| Confidence calibration | 15.7 / 20 |
| **Deterministic total** | **≈121.9 / 150** |
| Catastrophic false approvals | 22 / 1000 |

Training extraction is a floor, not a ceiling: many "misses" are fields the
documents physically destroyed (`[DATE WASHED OUT]`, cut-out names) that the
private labels mark unrecoverable and drop from the maximum.

## 3. The diagnostic that changed the project

We ran the finished policy on perfect fields — the truth CSV — and it scored
66+/80 with near-perfect denial recall. The denials that looked "hidden" were
extraction failures feeding correct rules wrong values. From that point on,
every point came from reading documents better, not from a smarter
classifier. A per-field sensitivity analysis put ~7 of the ~11 reachable
classification points on `risk_flags` alone, which is where the OCR effort
went.

Decision routing is expected-value-optimal against the scoring matrix,
measured per path. Clean-and-complete packets approve. Ambiguous signals —
transit purpose, stale-looking dates on OCR'd packets, salvage-only
completeness — route to review. Whitelist-salvaged values are reported for
extraction but deliberately don't count toward the evidence bar that unlocks
an approval.

## 4. What still fails, and why

- **Flags with no surviving evidence.** Most remaining risk-flag misses have
  no biometric slip, no note mention, no stamp. We verified this by rendering
  and red-ink-isolating every such packet. It matches the challenge's
  documented `unrecoverable_fields` design.
- **Slips beyond any threshold.** Heavy-noise recovery reads flags through
  ~50% character corruption. Some slips are worse than that.
- **Learned-list generalization.** Embargo and revoked lists come from
  training. A private test with new embargoed worlds and no adjudicator note
  would slip through. The note path and the registry "EMBARGO REVIEW" status
  — both stated in-document — are the generalizable backstops.
- **22 catastrophic false approvals per 1000.** The price of EV-optimal
  approval on a bucket with residual label noise. Each one is a case whose
  denial evidence is absent from the packet.

## 5. What we tried, with numbers

1. **ML on the residual — lost, kept out.** Cross-validated GBT and logistic
   models with decision-theoretic label choice score below the rule engine
   (66.8–69.1 vs 71.4 cls+cal) with 6–10× the catastrophic approvals.
   Reproducible: `solution/experiments/residual_model_cv.py`.
2. **Multi-pass OCR — shipped.** Bounded union of two 300 dpi segmentation
   passes plus binarization and deskew. Naive always-multi-DPI merging is a
   tail-latency trap.
3. **Visual stamp detection — dead.** No denial stamps exist in the corpus.
   The red ink reads FILED/COPY/MIB — decoys. Swept via vector inspection,
   pixel statistics, and red-channel OCR.
4. **Barcodes — dead.** Nothing decodable. "BARCODE PAYLOAD" strings are
   text-layer traps, correctly ignored.
5. **Label-anchored region re-OCR — shipped, neutral on train.** Locate a
   field label by word bounding box, crop the value region, upscale, read
   with a whitelist. Fill-only-empty by construction, so we keep it as free
   precision salvage.
6. **Held-out calibration — measured, table kept.** 5-fold CV puts in-sample
   optimism at ~0.3 calibration points. Every finer bucket split we tested
   (× OCR-used, × biometric-present, × missing-field count, × fee-observed)
   scores worse out-of-fold than the shipped reason-only table. Small buckets
   add variance faster than granularity adds signal.
7. **Adversarial self-attack — one real bug, fixed.** Seven PyMuPDF mutation
   attacks on real packets (`solution/experiments/adversarial_suite.py`). Six
   defenses held: reworded footers, printed and novel injections, rotation,
   unseen vocabulary, pale-ink hidden text. One broke: novel risk-flag tokens
   on clean digital slips were dropped as OCR noise, bypassing the
   unknown-flag review rule. Fixed per-page, inert on all real training data.
8. **Unused-evidence census — five rules shipped, +~1.0 measured.** The
   registry's "sponsor standing requires additional verification" notice
   (denies non-diplomats 23/23, never blocks DIP-1 5/5). The fee receipt's
   dollar amount ($809.00 ⇒ paid, 297/297, emission-only). The sponsor
   letter's "class X compliance" line as visa evidence (294/294). The digital
   intake-vs-registry name-swap trap (registry correct 7/7). Trailing-junk
   truncation at emission — names are always two words, purpose and world
   come from closed vocabularies (35 fixes, 0 breaks).
9. **Name-token vocabulary — shipped.** The generator draws every applicant
   name from a closed set of 144 first and 144 last tokens; 365 cleanly-typed
   validation packets contain zero tokens outside it. Unambiguous
   nearest-token repair: 19 fixes, 0 breaks.

## 6. What we'd do with another week

1. **Tesseract C API (`tesserocr`)** instead of subprocess-per-page (~35%
   runtime), reinvested in more binarization variants — different thresholds
   unlock different degraded pages.
2. **Sub-quadrant deskew.** Skews beyond ±12° with low-confidence orientation
   detection currently fail safe to review. A rotation sweep scored by OCR
   word yield could recover them.
3. **Biometric-slip name channel at scale.** On a 40-case probe it's a wash
   (1 win, 1 loss, and the win is already caught by the name vocabulary).
   Worth re-measuring on the full corpus before calling it dead.
