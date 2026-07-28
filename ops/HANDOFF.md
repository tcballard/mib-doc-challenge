# Handoff: finishing the MIB Doc Challenge submission

State: branch `claude/challenge-solution-8jh06w`, verified train score
**126.19 host / 126.10 in-image**, 17 catastrophic false-approvals,
validator clean. `solution/experiments/FINDINGS.md` holds 14 rounds of
measured decisions -- read it before proposing pipeline changes; most
obvious ideas are already measured dead there.

## The one remaining task

`submissions/tcballard/predictions.jsonl` is stale. Regenerate and refresh:

1. `bash ops/run_validation.sh`  (supervised, restart-proof, ~4-7 h; rerun
   the same script after any interruption -- it resumes from checkpoint).
2. When it prints VALIDATION_RUN_DONE, check its governor report: at most
   one "tier-2 shed" line and NO "escalation off" line is expected. An
   escalation-off line means investigate before shipping (see FINDINGS
   rounds 12 and 14 for the two governor bugs this run has caught).
3. Copy `/tmp/val_out/predictions.jsonl` to `submissions/tcballard/`,
   commit, push to the work branch.
4. Fast-forward fork `main`; rebuild `submission/tcballard` from the
   `upstream-base` tag plus one commit adding `submissions/tcballard/`
   (mirror commit e6ea719's structure); push --force-with-lease.

## Rules

- No per-case hardcoding, ever. Mechanisms trigger on document properties.
- Commits authored `Tom Ballard <tom@armytage.co>`, no AI trailers, no
  model identifiers in any pushed artifact.
- Pipeline changes ship only through a production-entrypoint gate against
  126.19 / 17 catastrophics, with the revert condition stated in advance.
- Never measure OCR or timing on a loaded box (loadavg < 2 first): under
  contention Tesseract hits its 12 s timeout and returns empty -- false
  zeros that have nearly caused wrong reverts twice.
- Kill processes by PID, never `pkill -f` with a pattern that matches the
  killing shell.
