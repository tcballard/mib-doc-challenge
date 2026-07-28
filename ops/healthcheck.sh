#!/bin/bash
# One-glance session orientation. Safe to run any time; changes nothing.
cd "$(dirname "$0")/.." || exit 1
echo "== repo =="
git log --oneline -1
git status --porcelain | head -5
echo "== expected baseline: train 126.19 host / 126.10 in-image, 17 catastrophics =="
echo "== validation run state =="
ck=/tmp/ckpt5000/mib_run_checkpoint.jsonl
[ -f "$ck" ] && echo "checkpoint rows: $(wc -l < "$ck")" || echo "no checkpoint yet"
[ -f /tmp/val_out/predictions.jsonl ] && echo "predictions rows: $(wc -l < /tmp/val_out/predictions.jsonl)"
echo "== docker =="
if docker info >/dev/null 2>&1; then
  echo "daemon: up"; docker ps --format "  container: {{.Image}} {{.Status}}"
else
  echo "daemon: DOWN (ops/run_validation.sh revives it, incl. stale-lock cleanup)"
fi
for f in /tmp/docker_val*.log; do
  [ -f "$f" ] && grep -H "governor:" "$f"
done
echo "== load =="
cat /proc/loadavg
