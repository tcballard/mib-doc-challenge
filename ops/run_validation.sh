#!/bin/bash
# Supervised, restart-proof 5000-packet validation run through the pinned image.
# Rerun this same script after ANY interruption; the checkpoint mount resumes it.
# Done when it prints VALIDATION_RUN_DONE. Then check the governor lines it echoes:
# expected at most one "tier-2 shed" and NO "escalation off" on this corpus.
set -u
REPO="$(cd "$(dirname "$0")/.." && pwd)"
IMG=mib-solution:submit
mkdir -p /tmp/val_out /tmp/ckpt5000
revive_docker() {
  docker info >/dev/null 2>&1 && return 0
  # Clear stale state from an unclean container restart, then start fresh.
  pkill -9 -x dockerd 2>/dev/null; pkill -9 -x containerd 2>/dev/null; sleep 2
  rm -f /var/run/docker.sock /var/run/docker.pid /run/containerd/containerd.sock
  setsid nohup dockerd --iptables=false --bridge=none > /tmp/dockerd.log 2>&1 < /dev/null &
  for _ in $(seq 1 30); do docker info >/dev/null 2>&1 && return 0; sleep 4; done
  return 1
}
revive_docker || { echo "dockerd failed to start; see /tmp/dockerd.log"; exit 1; }
docker image inspect "$IMG" >/dev/null 2>&1 || docker build --network=host -t "$IMG" "$REPO/solution"
while true; do
  revive_docker || { sleep 30; continue; }
  n=0; [ -f /tmp/val_out/predictions.jsonl ] && n=$(wc -l < /tmp/val_out/predictions.jsonl)
  [ "$n" -ge 5000 ] && break
  # setsid detaches from the terminal on Linux; macOS has no setsid, plain background works.
  SETSID=""; command -v setsid >/dev/null 2>&1 && SETSID="setsid"
  docker ps -q --filter ancestor="$IMG" | grep -q . || \
    $SETSID docker run --rm --network none --cpus "${MIB_CPUS:-4}" --memory 8g \
      ${MIB_WORKERS:+-e MIB_WORKERS="$MIB_WORKERS"} \
      -v "$REPO/data/validation":/in:ro -v /tmp/val_out:/out -v /tmp/ckpt5000:/tmp \
      "$IMG" /in /out/predictions.jsonl > /tmp/docker_val.log 2>&1 &
  sleep 120
done
cd "$REPO"
python3 scripts/validate_submission.py --submission /tmp/val_out/predictions.jsonl --pdf-dir data/validation | tail -2
echo "governor lines:"; grep "governor:" /tmp/docker_val.log || echo "  (none)"
echo VALIDATION_RUN_DONE
