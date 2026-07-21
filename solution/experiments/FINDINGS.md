
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
