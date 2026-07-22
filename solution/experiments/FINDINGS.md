
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
