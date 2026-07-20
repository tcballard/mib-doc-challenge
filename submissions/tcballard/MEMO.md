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
   biometric slip inside an otherwise-digital packet). Tesseract runs
   single-threaded per worker; OCR output is treated as visible evidence because
   it is the rendered document. This alone recovered the majority of the
   `biohazard_red` / `active_warrant` risk flags that live only on rasterized
   biometric slips.
3. **Parsing & consolidation (`parse.py`).** Each page type (intake form,
   registry extract, fee receipt, biometric slip, sponsor letter, adjudicator
   note) is parsed to key/value pairs and merged under the manual's precedence
   (adjudicator note > intake form > biometric > sponsor letter > registry).
   Robustness details that mattered: the label/value layout is sometimes
   value‑above and sometimes value‑below, so direction is chosen by *typed
   validation* (does the picked value look like a date / SPN / visa class?);
   watermark and placeholder strings are rejected from value slots; damage
   markers (`[NAME CUT OUT]`, `UNREADABLE`) are treated as absent; and
   OCR‑garbled adjudicator findings are recovered with fuzzy matching scoped to
   the text *before* the reason (so a "rescinded denial" reason cannot flip a
   review finding to a denial).
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
| Classification | 56.5 / 80 |
| Field extraction | 34.8 / 50 |
| Confidence calibration | 14.9 / 20 |
| **Deterministic total** | **≈106 / 150** |
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

## 5. What I'd do with another week

1. **Learn the residual denial signal.** Train a small, offline gradient‑boosted
   model on generalizable features (visa, fee, flags, staleness, purpose,
   completeness, embargo/revoked indicators) to recover part of the ~25% hidden
   denials and to produce smoother per‑case calibration than bucketed
   confidences — while keeping the deterministic note path as an override.
2. **Better OCR on degraded slips.** Deskew/denoise + adaptive thresholding and a
   digits/upper‑case whitelist for the biometric confidence and flags lines to
   recover more `illegible_biometrics` and species codes.
3. **Cross‑page identity reconciliation** for `sponsor_mismatch` /
   `identity_conflict` (sponsor‑letter name vs intake name vs registry) with a
   fuzzy matcher, instead of relying on the biometric flag line.
4. **Confidence via held‑out calibration** (isotonic regression on an
   out‑of‑fold split) rather than in‑sample bucket accuracy.
