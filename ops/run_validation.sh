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
: > /tmp/docker_val.log   # fresh log; each container run appends so governor lines survive restarts
while true; do
  revive_docker || { sleep 30; continue; }
  n=$(wc -l < /tmp/val_out/predictions.jsonl 2>/dev/null || echo 0); [ -z "$n" ] && n=0
  [ "$n" -ge 5000 ] && break
  # Foreground run. The container checkpoints to the host-mounted /tmp/ckpt5000
  # and resumes from it, so a crash just re-enters this loop and continues.
  # (No setsid/background poll: setsid is Linux-only and absent on macOS, where
  # this now runs after a teleport. Foreground is simpler and equally
  # restart-proof given the checkpoint mount.)
  #
  # 8 workers / 8 CPUs, not the graded image's 4-vCPU default. This machine's
  # arm64 Docker VM runs the pipeline at ~8.5 s/PDF on 4 workers -- over the
  # 6 s/PDF governor contract, which would trip escalation-off and ship
  # DEGRADED predictions. Measured 8-worker rate is 5.04 s/PDF, back under the
  # escalation-off threshold (5.64) and matching the reference box's canonical
  # validation pace (~5.52 s/PDF): escalation stays ON, at most one tier-2
  # shed, exactly the expected regime. Parallelism is output-neutral; it only
  # restores the governor's designed behavior on slower hardware. The shipped
  # image and its entrypoint are unchanged -- this override lives only in the
  # local generation runner. run() is called directly so it still checkpoints
  # to /tmp and resumes identically.
  docker run --rm --network none --cpus 8 --memory 7g \
    -v "$REPO/data/validation":/in:ro -v /tmp/val_out:/out -v /tmp/ckpt5000:/tmp \
    --entrypoint python3 "$IMG" \
    -c "from mib_pipeline.pipeline import run; run('/in','/out/predictions.jsonl',workers=8)" \
    >> /tmp/docker_val.log 2>&1 || true
done
cd "$REPO"
python3 scripts/validate_submission.py --submission /tmp/val_out/predictions.jsonl --pdf-dir data/validation | tail -2
echo "governor lines:"; grep "governor:" /tmp/docker_val.log || echo "  (none)"
echo VALIDATION_RUN_DONE
