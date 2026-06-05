#!/usr/bin/env bash
set -euo pipefail

IMAGE="${IMAGE:-roger-knowledge-mcp:phase0}"
NAME="${NAME:-roger-knowledge-mcp-phase0}"
NETWORK="${DOCKER_NETWORK:-roger-knowledge-private}"
NETWORK_ALIAS="${DOCKER_NETWORK_ALIAS:-roger-knowledge-mcp}"

docker network create "${NETWORK}" >/dev/null 2>&1 || true
docker rm -f "${NAME}" >/dev/null 2>&1 || true
exec docker run \
  --name "${NAME}" \
  --restart unless-stopped \
  --network "${NETWORK}" \
  --network-alias "${NETWORK_ALIAS}" \
  "${IMAGE}"
