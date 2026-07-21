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
| Classification | 58.6 / 80 |
| Field extraction | 38.9 / 50 |
| Confidence calibration | 15.3 / 20 |
| **Deterministic total** | **≈112.7 / 150** |
| Catastrophic false approvals | 6 / 1000 |

Training extraction is a *lower bound* on the graded score: many of its "misses"
are damaged fields (`[DATE WASHED OUT]`, cut-out names) that the private labels
mark unrecoverable and remove from the maximum.

## 3. Deliberate tradeoff: conservatism over raw accuracy

A clean, complete, non‑diplomatic packet still has a ~25% hidden‑denial rate that
**no visible field predicts** — the denial evidence is genuinely absent from the
document (the manual is "incomplete by design"). Approving that bucket buys ~1
extra classification point but produces ~40 catastrophic false approvals per
1000. Since a false approval is the single costliest error (−4, and tie‑breaker
#2), I route those packets to `NEEDS_REVIEW`. This trades ~0.5 deterministic
points for a **7× reduction in catastrophic false approvals** (43 → 6) and
better calibration, and — most importantly — it is robust to distribution shift
on the private test: the system cannot mass‑approve packets it cannot justify.

## 4. Failure modes

- **Underdetermined denials.** The dominant residual error is `DENIED` cases with
  no visible disqualifier; these become `NEEDS_REVIEW`. Irreducible without the
  admin-only labels.
- **Illegible biometric slips.** When the biometric slip is a degraded scan, OCR
  cannot always read `Observed flags`, so `illegible_biometrics` is sometimes
  missed — ironically the very illegibility that defines the flag.
- **Risk flags without a biometric page.** ~2/3 of risk‑flag misses are packets
  with no biometric slip at all; the flag isn't visibly evidenced.
- **Learned-list generalization.** Embargo/revoked lists come from training; a
  private test that introduces new embargoed worlds without an adjudicator note
  would slip through. The note path (which states the reason in‑document) is the
  generalizable backstop.

## 5. Experiments run (and what they showed)

1. **ML on the residual — negative result, kept out of the pipeline.** A
   cross‑validated gradient‑boosted / logistic model over generalizable features,
   with decision‑theoretic label choice, was tested against the rule engine on
   the ambiguous cases. It reached higher raw accuracy but a *lower* challenge
   score (66.8–69.1 vs 71.4 cls+cal) with 6–10× more catastrophic false
   approvals: the residual denials are irreducible noise given visible evidence,
   so "uncertain → review" is already score‑optimal. Reproducible in
   `solution/experiments/residual_model_cv.py`; writeup in
   `solution/experiments/FINDINGS.md`.
2. **Multi‑pass OCR — positive, shipped.** Naive always‑multi‑DPI merging is a
   tail‑latency trap (Tesseract can grind minutes on noisy pages at high DPI),
   but a bounded union of two 300 dpi segmentation passes with hard timeouts
   gained ~+6 points total (extraction +4, classification +2) over the original
   single 200 dpi pass.

## 6. What I'd do with another week

1. **Tesseract C API (`tesserocr`)** instead of subprocess-per-page, freeing
   budget for more OCR passes and image preprocessing (deskew/denoise/adaptive
   thresholding) on the worst scans.
2. **Visual stamp/mark detection** (classical CV on rendered pages) for evidence
   that OCR can't read as text — e.g. crossed-out denial stamps.
3. **Confidence via held‑out calibration** (isotonic regression on an
   out‑of‑fold split) rather than in‑sample bucket accuracy, to derisk the mild
   optimism of calibrating on the same data the rules were tuned on.
