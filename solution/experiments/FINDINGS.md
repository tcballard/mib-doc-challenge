
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
