#!/usr/bin/env bash
# Starts the nine-container stack on every Codespace boot (postStartCommand).
set -euo pipefail

LOG=/tmp/tt-stack-start.log
LOCK=/tmp/tt-stack-start.lock

# Serialize against a second copy of this script — a Codespace that is stopped
# and restarted quickly can overlap two runs, and two `docker compose up` calls
# racing each other create the project network twice. The stack then refuses to
# start at all with "network ..._tt-network is ambiguous (2 matches found on
# name)", which is much harder to read than the wait this lock imposes.
exec 9>"$LOCK"
flock 9

exec > >(tee -a "$LOG") 2>&1
echo "==> $(date -Is) starting stack (log: $LOG)"

# Wait for the daemon. Under docker-in-docker dockerd is started by the
# container entrypoint, which races postStartCommand: a bare `docker compose up`
# here loses that race on a cold boot and dies on "Cannot connect to the Docker
# daemon", leaving the Codespace with no stack and the reason buried in the
# creation log.
echo "==> Waiting for the Docker daemon..."
for _ in $(seq 1 60); do
  if docker info > /dev/null 2>&1; then
    daemon_ready=1
    break
  fi
  sleep 2
done

if [ "${daemon_ready:-0}" != "1" ]; then
  echo "!! Docker daemon did not come up within 120s."
  echo "!! Start the stack by hand once it does: docker compose up -d"
  exit 1
fi
echo "==> Docker daemon ready."

# --wait turns "containers created" into "containers healthy", so a service that
# starts and then dies is reported here rather than discovered later in the UI.
# The timeout is generous because a brand-new Codespace builds three images and
# pulls six before anything can report healthy.
echo "==> Bringing up the stack (first boot builds and pulls; several minutes)..."
if docker compose up -d --wait --wait-timeout 600; then
  echo "==> Stack healthy. War Room on port 3000, Grafana on 3001, Jaeger on 16686."
  exit 0
fi

echo "!! Stack did not reach a healthy state. Current state:"
docker compose ps || true
echo "!! Full log: $LOG   Per-service detail: docker compose logs <service>"
exit 1
