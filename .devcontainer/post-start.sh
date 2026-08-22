#!/usr/bin/env bash
# Starts the nine-container stack on every Codespace boot.
#
# This waits for the daemon first. Under the docker-in-docker feature dockerd is
# started by the container's own entrypoint, which races postStartCommand: a bare
# `docker compose up -d` here lost that race and the Codespace came up with no
# stack and a "Cannot connect to the Docker daemon" line buried in the log.
set -euo pipefail

echo "==> Waiting for the Docker daemon..."
for _ in $(seq 1 60); do
  if docker info > /dev/null 2>&1; then
    echo "==> Docker daemon ready."
    docker compose up -d
    exit 0
  fi
  sleep 2
done

echo "!! Docker daemon did not come up within 120s. Start the stack manually with: docker compose up -d" >&2
exit 1
