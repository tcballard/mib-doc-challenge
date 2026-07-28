# Local run: finishing the submission off the cloud box

The remaining task in `HANDOFF.md` is a 4-7 hour validation run. Cloud sessions
are reclaimed after inactivity and the environment is not recoverable, so the
run belongs on a machine you control. `claude --teleport` moves the session;
it carries the branch and conversation history, **not** the container's disk.
So the corpus and the built image do not come with it -- rebuild both locally
with the steps below.

## 0. Prerequisites

- Docker, running.
- ~8 GB free disk (2.9 GB zip + ~3 GB expanded + ~600 MB image).
- Docker must be able to give the container **4 CPUs and 8 GB RAM** --
  `run_validation.sh` passes `--cpus 4 --memory 8g`. On Docker Desktop the
  default VM allocation is often lower; raise it in Settings > Resources
  first, or the run gets OOM-killed hours in. Check with
  `docker info --format '{{.NCPU}} CPUs / {{.MemTotal}} bytes'`.
- Timing and OCR are only valid on an idle box: under contention Tesseract
  hits its 12 s per-page timeout and returns empty, which reads as a real
  score drop. Keep loadavg < 2 and do not run this alongside other heavy work.
- A checkout of `tcballard/mib-doc-challenge` (teleport requires the same
  repository, clean git state, and the branch already pushed).

## 1. Teleport in

```bash
claude --teleport            # interactive picker
# or: claude --teleport <session-id>
```

## 2. Get the corpus

Not in git -- it is a versioned zip distributed separately.

```bash
cd <repo root>
curl -L -o mib-doc-challenge-public-data-v2026-07-07.zip \
  "https://huggingface.co/datasets/arjun-krishna1/mib-doc-challenge-data/resolve/main/mib-doc-challenge-public-data-v2026-07-07.zip"

shasum -a 256 -c data/downloads.sha256   # must print OK
unzip -q mib-doc-challenge-public-data-v2026-07-07.zip
```

Expands to `data/train/`, `data/validation/`, and the two label/manifest CSVs
(the CSVs are already in git; the zip overwrites them with identical content).

Verify before spending four hours on it:

```bash
ls data/validation | wc -l    # expect 5000
```

## 3. Run it

```bash
bash ops/run_validation.sh
```

Builds `mib-solution:submit` from `solution/` and processes all 5000 packets.
Restart-proof: rerun the same command after any interruption and it resumes
from `/tmp/ckpt5000`. Done when it prints `VALIDATION_RUN_DONE`.

`ops/healthcheck.sh` gives one-glance state (checkpoint rows, docker, load) at
any time and changes nothing.

On a local box the image builds straight from `solution/Dockerfile`. The CA
overlay used in the cloud session was a workaround for that environment's
TLS-intercepting egress proxy and is not needed here.

## 4. Check the governor report

The script echoes the governor lines when it finishes. Expected on this corpus:

- at most one `tier-2 shed` line
- **no** `escalation off` line

An `escalation off` line means investigate before shipping -- see FINDINGS
rounds 12 and 14 for the two governor bugs this run has already caught.

## 5. Ship

```bash
cp /tmp/val_out/predictions.jsonl submissions/tcballard/predictions.jsonl
python3 scripts/validate_submission.py \
  --submission submissions/tcballard/predictions.jsonl \
  --manifest data/validation_manifest.csv --require-complete
```

Expect `Valid submission records: 5000` and `Missing expected case ids: 0`.
Commit and push to the work branch, then rebuild the submission branch so its
diff touches nothing outside `submissions/tcballard/`:

```bash
git checkout -B submission/tcballard upstream-base   # tag = 38ce888, upstream merge base
git checkout <work-branch> -- submissions/tcballard
git commit -m "Add submission: predictions, memo, submission notes"
git push --force-with-lease -u origin submission/tcballard
```

If the `upstream-base` tag is missing (it is local-only and dies with a
container), recreate it: `git tag -f upstream-base 38ce888`.

`MEMO.md` already reports 126.2/150 and 17 catastrophics; only
`predictions.jsonl` is stale, so no memo edit is expected. If the regenerated
run moves the numbers, reconcile the memo before pushing.
