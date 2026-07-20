# Experiment: does an ML model on the residual beat the rules?

**Short answer: no.** A well-regularized, cross-validated model on the ambiguous
(non-adjudicator-note) cases does not beat the rule engine on the challenge
score. The residual denials are effectively irreducible noise given the visible
packet evidence, so the rules' "route uncertain cases to `NEEDS_REVIEW`" is
already close to optimal.

Reproduce: `python3 solution/experiments/residual_model_cv.py`

## Setup

- **Cases split:** 237 have a visible/OCR-recovered adjudicator finding (handled
  deterministically, 100% accurate). Of the remaining 763, the rule engine
  resolves 459 through *high-precision* reasons (disqualifying flags, `TRANSIT-7`,
  embargo world, revoked sponsor, unpaid/unknown fee, staleness, review flags).
  That leaves **541 ambiguous cases** — clean-looking packets with no visible
  disqualifier.
- **Model:** features are generalizable only (visa/fee/flag categories, page
  composition, completeness, staleness, learned embargo/revoked indicators — no
  packet identity). Labels chosen to **maximize expected challenge score** from
  the model's probabilities (not argmax probability). 5-fold stratified CV, both
  gradient-boosted trees and calibrated logistic regression.

## Result (5-fold out-of-fold, full training set, notes by rule)

| System | cls /80 | cal /20 | cls+cal | catastrophic FA | acc |
| --- | ---: | ---: | ---: | ---: | ---: |
| **Rules** | **56.50** | **14.94** | **71.44** | **6** | 0.625 |
| Hybrid + GBT | 52.98 | 13.80 | 66.78 | 47 | 0.636 |
| Hybrid + LogReg | 55.19 | 13.91 | 69.10 | 59 | 0.681 |

The models reach **higher raw accuracy** but **lower score**, because the extra
"correct" calls come with many `DENIED→APPROVED` catastrophic false approvals
(−4 each) and false denials (0) that the rules avoid by declining to guess.

A calibration-only variant — keep the rule's *label*, replace confidence with the
model's per-case probability — was also worse (Brier 0.20 vs 0.127): the
per-reason bucket confidences are already better calibrated than a per-case model
on this noisy residual.

## Why

On the ambiguous bucket, ~25–30% of packets are `DENIED` in the ground truth with
**no distinguishing visible feature** — the denial evidence isn't in the packet
(the field manual is "incomplete by design"; private labels carry admin-only
`traps`/`damage_profile`/`unrecoverable_fields`). A model can only regress toward
base rates there, and under this scoring matrix the expected-value-optimal move
for an unpredictable case is `NEEDS_REVIEW`, which is exactly what the rules do.

## Takeaways

1. **~106/150 is close to the information ceiling** for a system limited to
   visible packet evidence — not an artifact of using rules instead of ML.
2. The rule engine's conservatism (uncertain → review) is *validated* as the
   score-optimal policy, not just a safe heuristic.
3. Real remaining upside is in **extraction/OCR** (recovering more fields on
   damaged/illegible pages), not in a smarter adjudication classifier.

The production pipeline is therefore left rule-based. `mib_pipeline/features.py`
is kept because it is a clean, reusable feature space if a future dataset (with a
learnable residual) warrants revisiting this.
