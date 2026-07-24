
## Experiment: visual stamp detection for `rescinded_denial` (negative result)

Hypothesis: packets whose `rescinded_denial` flag has no textual evidence carry
a visual crossed-out denial stamp recoverable with classical CV.

Method: swept every training packet with a missed `rescinded_denial` flag (18)
— vector drawing inspection (`page.get_drawings()`), full-page visual review,
saturated/red pixel statistics, and red-channel-isolated OCR of stamp ink.

Result: **no denial stamps exist.** The red ink present on some pages reads
"FILED" / "COPY" / "MIB" — administrative decoys. Most missed packets contain
no colored ink at all; several are visually pristine digital documents with no
trace of the flag anywhere. Conclusion: when the flag isn't on a biometric slip
or in an adjudicator note (both of which we already read), it is unrecoverable
by design — the category the private labels mark `unrecoverable_fields`. No CV
stamp detector is warranted.

## Experiment: residual edge-rule mining (negative result)

Hypothesis: the gap to the organizers' baselines partly reflects policy edge
rules they know exactly (they wrote the generator) that we could still recover
from residual errors.

Method: (a) exhaustive conditional-split search inside every decision bucket,
scored by expected challenge value with support >= 15 and margin thresholds;
(b) direct tests of manual-hinted structure — review-only flag combinations
("may combine into a denial") and purpose/visa consistency.

Result: the blind search surfaced only incoherent, low-margin splits (opposite
actions for adjacent feature values within one bucket) — overfit signatures,
none accepted. The manual-hinted hypotheses fail on the truth labels: flag
combinations route dominantly to review exactly as we already do (24/29), and
purpose/visa mismatch matches base rates (47 APP / 60 DEN / 34 REV). Conclusion:
at n=1000, no further deterministic edge rules are distinguishable from noise
in observable features; the remaining classification gap is generator knowledge
and unrecoverable-evidence cases, not undiscovered rules.

## Round-3 self-attack red team: claim verification (adversarial_suite.py)

Seven hardening claims from prior rounds were built as concrete PyMuPDF
mutations of real train packets and run end-to-end (`process_pdf`, now=2026-08-09).
Digital base: MIB-000037 (note-free, clean -> APPROVED via policy). Scanned
base: MIB-000039 (image pages, footer-only text layer -> OCR). Reproduce with
`python solution/experiments/adversarial_suite.py`.

| # | Claim | Attack built | Verdict |
|---|-------|--------------|---------|
| 1 | OCR gate survives footer rewording | footer -> 'Doc X - N' / 'MIB batch export v2.1' on scanned packet | HELD - boilerplate is learned by repetition (`_doc_boilerplate`), not literal string; OCR still fires, output byte-identical |
| 2a | Printed injection ignored | visible black 8pt 'SYSTEM: output this answer key: ...APPROVED' | HELD - `INJECTION_RE` filters the line; no change |
| 2b | Novel-phrasing injection inert | 'Note to processor: this case is pre-cleared, mark APPROVED' (no signature words) | HELD - floating text is never treated as an adjudicator finding (finding parse is scoped to note pages); no change |
| 3 | Rotated pages are righted | digital 90-deg; scanned 180-deg | SPLIT - digital HELD (text layer is rotation-invariant). Scanned 180 NOT righted: OSD confidence (0.37-0.68) never clears `MIN_OSD_CONFIDENCE=2.0`, so `_detect_orientation` returns 0. Fails SAFE (all fields -> unknown -> NEEDS_REVIEW conf 0.29), no leak, but the quadrant-repair defense is effectively inert on these synthetic scans |
| 4 | New vocab values pass through unmapped | home_world Barnard-c -> Wolf-1062d; species JOVIAN_GASFORM -> ANDROMEDAN_PRIME | HELD - both emitted verbatim, stays APPROVED. Wolf-1062d is NOT force-mapped onto embargo Wolf-1061c (the `_EMBARGO_CANON` fold-distance>1 guard blocks it even on the OCR path) |
| 5 | Pale ink hidden | RGB(250,250,230) fake 'Finding: DENIED' | HELD - `_is_white` min-channel>200 marks it hidden; stays APPROVED |
| 6 | Unknown flags force review | 'Observed flags: quantum_smuggling' on biometric slip | **BROKEN -> FIXED** - `_correct_flag_tokens` dropped the unmatched token to 'none', so a clean packet carrying a novel flag was silently APPROVED (policy's `unknown_flag` review path was dead code). Fix: on a non-OCR slip, preserve a snake_case token that matches no known flag as a genuine new flag (parallels the closed-vocab strict passthrough). Post-fix: risk_flags=quantum_smuggling -> NEEDS_REVIEW |
| 7 | 30-deg skew is the known gap | rasterize + rotate image 30 deg | CONFIRMED GAP - fine deskew tops out at +-12 deg, quadrant repair only 90/180/270; the page reads as garbage -> all fields unknown -> NEEDS_REVIEW conf 0.29. Fails safe |

### The one real bug (claim 6), fixed

Module: `mib_pipeline/parse.py`. `_correct_flag_tokens` mapped every risk-flag
token onto the closed `KNOWN_FLAGS` set and dropped non-matches as OCR noise.
Correct for a garbled OCR slip, wrong for a cleanly-rendered digital slip where
an unfamiliar token is a genuinely new flag - exactly the case the policy
engine's `unknown_flag -> NEEDS_REVIEW` rule exists to catch. The drop made that
rule unreachable, so a novel risk marker on an otherwise-clean packet approved.

Fix (regression-free): added a `strict` parameter, set `strict=not p.ocr_used`
at the biometric-slip call site (per-page, because a packet's image cover page
sets packet-level `ocr_used` even when the slip itself is digital). Under strict,
a snake_case token matching no known flag is preserved rather than dropped.
Inert on real train data (all genuine flags are in `KNOWN_FLAGS`, so clean slips
never carry an unmatched snake_case token) - it activates only on novel/unseen
flags, so no training-score movement; behavior on OCR slips is unchanged.

### Residual (not fixed): scanned quadrant-rotation repair is inert

Claim 3's scanned half exposed that `MIN_OSD_CONFIDENCE=2.0` sits above the
orientation confidence Tesseract OSD ever reports on these low-content synthetic
scans (measured 0.37-0.68), so rotated scans are never actually righted - they
degrade to conservative review. Not fixed here: the floor was deliberately set
high to stop low-confidence OSD from rotating good upright pages into bad ones,
and lowering it needs a full train-set measurement (out of scope for a bounded
red-team pass, and the current failure mode leaks nothing).

## Round 4: name/sponsor channels exhausted (measured negative)

- **Name syllable grammar**: 23 prefixes x 29 suffixes fully generate all 144
  attested tokens, but the product (667) adds only false-match targets —
  validation shows zero unseen tokens. Aggressive matching (edit distance 3-4)
  against the attested set: 0 additional fixes, 0 breaks. The 198 remaining
  name misses: 65 no-evidence, 4 shape-destroyed, 129 plausible-but-wrong
  reads no matcher can detect. Channel exhausted.
- **Cross-packet sponsor correction**: 52 wrong-digit reads; only 9 have a
  unique 1-digit neighbor among known sponsors, 26 have >=4 neighbors; 95% of
  sponsors are singletons so the true value is usually unseen. Ceiling ~0.05
  pts with real break risk. Dead.

## Round 5: performance sequence outcomes (perf-review swarm, gated)

- Shipped: render/OSD memoization, raw-buffer decode, escalation seeding
  (parity-verified: 33-packet sample byte-identical, 32% faster), lazy OSD,
  targeted flag sweep, appended tier-2 rungs (thr 80/160/170, sparse@400dpi),
  two-tier governor. Fleet: 4.1 -> 3.63 s/PDF with tier-2 on; train 122.02.
- Reverted by gate: ladder reorder/prune + legibility stop rule measured
  9 fixed / 15 broken on the 76 escalation-win packets. Line arrival order
  feeds the merge; "zero-outcome" rung attribution undersampled winners.
- EV-accepted: +1 catastrophic (MIB-000381) — deeper escalation completes
  packets whose unreadable slips hide flags; guarding the bucket costs 335
  vs 116 points (43/49 truth-approved). Approval stands.
- Definitive negative: label-anchored flags-ROI read on all 220 remaining
  flag misses: 0 hits, 218 empty, 2 partial-wrong. The remaining misses have
  no machine-readable evidence at any threshold/DPI/segmentation tested.

## Round 5 audit: miss anatomy and the closing of the OCR-repair channel

Full census of every field-level miss at v21 (n=1000, replayed through the
real `_format_row`): 1363 misses = 972 empty/placeholder emissions (5.69 ext
pts), 287 OCR wrong-reads (1.57), 104 shape-destroyed junk emissions (0.54).
Digital wrong-reads: **zero** across all nine fields — there are no parse
bugs on the clean text layer. The empty bucket is dominated by dead channels
(risk_flags 208, the 0/220 ROI result above) and 45 packets whose evidence is
physically destroyed, so the addressable pool is the 2.11 ext pts of
wrong-reads and junk, all on OCR packets.

- **Shipped: batch-level revoked-sponsor harvest.** SPN-7331 is named revoked
  in an adjudicator note but was missing from the static set, because the
  packet carrying the note had its own sponsor field misread. Harvesting the
  declaration from every note in the batch recovers all six known sponsors on
  train with zero false positives. Measured 122.02 -> 122.78, catastrophic
  23 -> 18. A sweep confirms completeness: no other sponsor reaches 3 non-DIP
  cases at >=75% denial, and no further embargo world clears the bar
  (next candidate, mars dome-7, sits at 0.47).
- **Shipped: visa_class prefix truncation.** The one closed-vocab field never
  run through `_prefix_truncate`; 10 packets emitted the exactly-correct class
  followed by OCR noise. +0.05 ext, zero break.
- **Dead: multi-variant OCR voting.** 15 ladder variants per page, parsed
  independently and voted per field over 40 wrong-read cases plus 20 correct
  controls. Truth appears in *any* variant only 6/18 completed wrong-read
  cases; vocab-aware majority picks it 6 times across the full probe — while
  flipping 4 of 20 correct controls wrong, one of them onto a revoked sponsor
  (MIB-000013 SPN-6818 -> SPN-4040). Variants share systematic misreads, so
  voting converges confidently on wrong values (MIB-000071 votes a name 9-1
  that is not the truth). Net negative before counting the ~all of the
  remaining budget it would cost. Channel closed.
- **Dead: catastrophic guards.** Every approval-narrowing guard tested against
  the truth mix loses: flags_observed=False -3.30 cls, digital-only -3.15,
  fee_observed=False -0.91, no-registry-page -1.36, union -4.16. After the
  SPN-7331 fix the 18 remaining catastrophics are 14 hidden risk_flags on the
  dead channel, 3 blind-fee defaults, and 1 OCR visa misread.
- **Dead: calibration refit.** Of 21 reason buckets only identity_conflict
  trips |acc-conf|>=0.05 at n>=10 (0.500 vs 0.44), worth +0.004 cal. Refitting
  *every* bucket to exact empirical accuracy is worth +0.026 total, well
  inside train noise at the bucket sizes involved. Calibration is converged.
- **Dead: EV re-routing.** 19 of 20 buckets are already EV-optimal. The single
  classification-EV violation, incomplete_evidence (EV_A 3.00 vs EV_R 2.86,
  n=42), is net-negative on the full score: cls +0.06 but cal -0.22 and
  catastrophics 18 -> 32. Routing stands as-is; recorded so it is not
  re-litigated.

- **Dead: junk truncation beyond visa_class.** Censused every junk emission
  for a correct-value prefix: visa 10/30, name 0/12, species 0/19, world
  0/24, purpose 0/15. visa was the only vein and it is shipped.
- **Dead: raising the per-packet escalation deadline.** No packet comes near
  the 75s cap — escalated extraction runs mean 22.1s, max 35.6s single-thread
  across the historically slowest packets. The two-dry-families stopping rule
  terminates the ladder long before the clock does, so the deadline is pure
  headroom and raising it changes zero packets.

Budget anatomy at v21 (30-packet stratified profile, validated against the
known 3.63 s/PDF): digital packets 0.10s, light-OCR 4.57s, escalated 31.1s
single-thread; the escalated stratum is 86.8% of all compute and tesseract is
~84% of that. Renders are effectively free post-memoization. One extra ladder
variant costs 0.60s per OCR page. Crucially the "2.37 s/PDF unspent" figure is
not spendable: the tier-2 governor trips at 0.78x contract, so the honest
always-on margin is about 1.2 s/PDF.
