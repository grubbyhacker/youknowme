#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  scripts/relaunch-container-with-new-index.sh --artifact ARTIFACT_ZIP [options]

Options:
  --artifact PATH       GitHub Actions artifact ZIP containing one .tar.gz, .sha256, and build-report.json.
  --deploy-root PATH    Deployment root. Default: /opt/youknowme
  --image NAME          Docker image to run and validate with. Default: youknowme:phase1e
  --container NAME      Container name. Default: youknowme-phase1e
  --network NAME        Docker network. Default: roger-knowledge-private
  --alias NAME          Docker network alias. May be repeated. Defaults: roger-knowledge-mcp, youknowme
  --env-file PATH       Runtime env file. Default: <deploy-root>/runtime.env
  --host-port PORT      Publish host PORT to container port 8765 and smoke via localhost.
  --no-restart-policy   Do not set --restart unless-stopped. Useful for local smoke tests.
  --help                Show this help.
EOF
}

ARTIFACT_ZIP=""
DEPLOY_ROOT="/opt/youknowme"
IMAGE="youknowme:phase1e"
CONTAINER="youknowme-phase1e"
NETWORK="roger-knowledge-private"
ENV_FILE=""
HOST_PORT=""
RESTART_POLICY="--restart unless-stopped"
ALIASES=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --artifact)
      ARTIFACT_ZIP="${2:?--artifact requires a path}"
      shift 2
      ;;
    --deploy-root)
      DEPLOY_ROOT="${2:?--deploy-root requires a path}"
      shift 2
      ;;
    --image)
      IMAGE="${2:?--image requires a name}"
      shift 2
      ;;
    --container)
      CONTAINER="${2:?--container requires a name}"
      shift 2
      ;;
    --network)
      NETWORK="${2:?--network requires a name}"
      shift 2
      ;;
    --alias)
      ALIASES+=("${2:?--alias requires a name}")
      shift 2
      ;;
    --env-file)
      ENV_FILE="${2:?--env-file requires a path}"
      shift 2
      ;;
    --host-port)
      HOST_PORT="${2:?--host-port requires a port}"
      shift 2
      ;;
    --no-restart-policy)
      RESTART_POLICY=""
      shift
      ;;
    --help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ -z "$ARTIFACT_ZIP" ]]; then
  echo "--artifact is required" >&2
  usage >&2
  exit 2
fi

if [[ ${#ALIASES[@]} -eq 0 ]]; then
  ALIASES=(roger-knowledge-mcp youknowme)
fi
if [[ -z "$ENV_FILE" ]]; then
  ENV_FILE="$DEPLOY_ROOT/runtime.env"
fi

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Required command not found: $1" >&2
    exit 1
  fi
}

require_command docker
require_command python3
require_command tar
require_command unzip

ARTIFACT_ZIP="$(cd "$(dirname "$ARTIFACT_ZIP")" && pwd)/$(basename "$ARTIFACT_ZIP")"
DEPLOY_ROOT="$(mkdir -p "$DEPLOY_ROOT" && cd "$DEPLOY_ROOT" && pwd)"
ENV_FILE="$(cd "$(dirname "$ENV_FILE")" && pwd)/$(basename "$ENV_FILE")"

if [[ ! -f "$ARTIFACT_ZIP" ]]; then
  echo "Artifact ZIP does not exist: $ARTIFACT_ZIP" >&2
  exit 1
fi
if [[ ! -f "$ENV_FILE" ]]; then
  echo "Runtime env file does not exist: $ENV_FILE" >&2
  exit 1
fi

LOCK_DIR="$DEPLOY_ROOT/deploy.lock"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "Another index deployment appears to be running: $LOCK_DIR" >&2
  exit 1
fi
cleanup_lock() {
  rmdir "$LOCK_DIR" >/dev/null 2>&1 || true
}
trap cleanup_lock EXIT

WORK_DIR="$(mktemp -d "$DEPLOY_ROOT/.incoming.XXXXXX")"
cleanup_work() {
  rm -rf "$WORK_DIR"
}
trap 'cleanup_work; cleanup_lock' EXIT

ARTIFACT_DIR="$WORK_DIR/artifact"
UNPACK_DIR="$WORK_DIR/unpacked"
mkdir -p "$ARTIFACT_DIR" "$UNPACK_DIR" "$DEPLOY_ROOT/index-builds" "$DEPLOY_ROOT/logs" "$DEPLOY_ROOT/intake"

unzip -q "$ARTIFACT_ZIP" -d "$ARTIFACT_DIR"

TARBALLS=()
while IFS= read -r path; do
  TARBALLS+=("$path")
done < <(find "$ARTIFACT_DIR" -maxdepth 1 -type f -name "*.tar.gz" | sort)

SHAS=()
while IFS= read -r path; do
  SHAS+=("$path")
done < <(find "$ARTIFACT_DIR" -maxdepth 1 -type f -name "*.sha256" | sort)

REPORTS=()
while IFS= read -r path; do
  REPORTS+=("$path")
done < <(find "$ARTIFACT_DIR" -maxdepth 1 -type f -name "*.build-report.json" | sort)

if [[ ${#TARBALLS[@]} -ne 1 || ${#SHAS[@]} -ne 1 || ${#REPORTS[@]} -ne 1 ]]; then
  echo "Artifact ZIP must contain exactly one .tar.gz, one .sha256, and one .build-report.json" >&2
  printf 'tarballs=%s sha256=%s reports=%s\n' "${#TARBALLS[@]}" "${#SHAS[@]}" "${#REPORTS[@]}" >&2
  exit 1
fi

TARBALL="${TARBALLS[0]}"
SHA_FILE="${SHAS[0]}"
REPORT_FILE="${REPORTS[0]}"

EXPECTED_SHA="$(awk '{print $1}' "$SHA_FILE")"
ACTUAL_SHA="$(python3 - "$TARBALL" <<'PY'
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

print(hashlib.sha256(Path(sys.argv[1]).read_bytes()).hexdigest())
PY
)"
if [[ "$EXPECTED_SHA" != "$ACTUAL_SHA" ]]; then
  echo "Checksum mismatch for $(basename "$TARBALL")" >&2
  echo "expected: $EXPECTED_SHA" >&2
  echo "actual:   $ACTUAL_SHA" >&2
  exit 1
fi

tar -xzf "$TARBALL" -C "$UNPACK_DIR"
INDEX_DIR="$UNPACK_DIR/index"
if [[ ! -f "$INDEX_DIR/manifest.json" || ! -f "$INDEX_DIR/chunks.jsonl" || ! -d "$INDEX_DIR/lancedb" ]]; then
  echo "Unpacked artifact does not contain index/manifest.json, index/chunks.jsonl, and index/lancedb" >&2
  exit 1
fi

docker run --rm \
  -v "$INDEX_DIR:/data/index:ro" \
  "$IMAGE" \
  ykm validate-index --index /data/index >/dev/null

read -r SOURCE_COMMIT BUILD_ID EMBEDDING_PROVIDER EMBEDDING_MODEL < <(python3 - "$INDEX_DIR/manifest.json" <<'PY'
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

manifest = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
source_commit = manifest["source_commit"]
build_id = manifest["build_id"]
provider = manifest["embedding_provider"]
model = manifest["embedding_model"]
safe_source = re.sub(r"[^A-Za-z0-9._-]+", "-", source_commit)[:120]
print(safe_source, build_id, provider, model)
PY
)

BUILD_NAME="${SOURCE_COMMIT}-${BUILD_ID}"
BUILD_DIR="$DEPLOY_ROOT/index-builds/$BUILD_NAME"
if [[ -e "$BUILD_DIR" ]]; then
  echo "Index build already exists: $BUILD_DIR" >&2
  exit 1
fi

STAGED_BUILD_DIR="$DEPLOY_ROOT/index-builds/.$BUILD_NAME.tmp"
rm -rf "$STAGED_BUILD_DIR"
mkdir -p "$STAGED_BUILD_DIR"
cp -a "$INDEX_DIR"/. "$STAGED_BUILD_DIR"/
cp "$REPORT_FILE" "$STAGED_BUILD_DIR/build-report.json"
mv "$STAGED_BUILD_DIR" "$BUILD_DIR"

CURRENT_LINK="$DEPLOY_ROOT/index-current"
PREVIOUS_LINK="$DEPLOY_ROOT/index-previous"
OLD_TARGET=""
if [[ -L "$CURRENT_LINK" ]]; then
  OLD_TARGET="$(readlink "$CURRENT_LINK")"
elif [[ -e "$CURRENT_LINK" ]]; then
  echo "$CURRENT_LINK exists but is not a symlink; refusing to replace it" >&2
  exit 1
fi

repoint_current() {
  local target="$1"
  local tmp_link="$DEPLOY_ROOT/.index-current.next"
  rm -f "$tmp_link"
  ln -s "$target" "$tmp_link"
  rm -f "$CURRENT_LINK"
  mv "$tmp_link" "$CURRENT_LINK"
}

if [[ -n "$OLD_TARGET" ]]; then
  tmp_prev="$DEPLOY_ROOT/.index-previous.next"
  rm -f "$tmp_prev"
  ln -s "$OLD_TARGET" "$tmp_prev"
  rm -f "$PREVIOUS_LINK"
  mv "$tmp_prev" "$PREVIOUS_LINK"
fi
repoint_current "$BUILD_DIR"

UID_GID="$(docker run --rm --entrypoint id "$IMAGE" -u):$(docker run --rm --entrypoint id "$IMAGE" -g)"
if chown -R "$UID_GID" "$DEPLOY_ROOT/logs" "$DEPLOY_ROOT/intake" 2>/dev/null; then
  chmod 700 "$DEPLOY_ROOT/logs" "$DEPLOY_ROOT/intake"
else
  echo "Could not chown logs/intake to container uid; using local smoke-compatible permissions" >&2
  chmod 777 "$DEPLOY_ROOT/logs" "$DEPLOY_ROOT/intake"
fi
chmod -R a+rX "$BUILD_DIR"

NETWORK_ARGS=(--network "$NETWORK")
for alias in "${ALIASES[@]}"; do
  NETWORK_ARGS+=(--network-alias "$alias")
done
PORT_ARGS=()
if [[ -n "$HOST_PORT" ]]; then
  PORT_ARGS=(-p "127.0.0.1:$HOST_PORT:8765")
fi

run_container() {
  local active_index
  active_index="$(readlink "$CURRENT_LINK")"
  if [[ -z "$active_index" ]]; then
    echo "Current index symlink is not set: $CURRENT_LINK" >&2
    return 1
  fi
  echo "Starting $CONTAINER with index: $active_index"
  docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
  # shellcheck disable=SC2086
  docker run -d \
    --name "$CONTAINER" \
    $RESTART_POLICY \
    "${NETWORK_ARGS[@]}" \
    "${PORT_ARGS[@]}" \
    --env-file "$ENV_FILE" \
    -v "$active_index:/data/index:ro" \
    -v "$DEPLOY_ROOT/logs:/data/logs" \
    -v "$DEPLOY_ROOT/intake:/data/intake" \
    --read-only \
    --tmpfs /tmp \
    --cap-drop ALL \
    --security-opt no-new-privileges:true \
    "$IMAGE" >/dev/null
}

smoke_url() {
  local path="$1"
  if [[ -n "$HOST_PORT" ]]; then
    curl -fsS "http://127.0.0.1:$HOST_PORT$path" >/dev/null
  else
    docker run --rm --network "$NETWORK" curlimages/curl:latest -fsS "http://${ALIASES[0]}:8765$path" >/dev/null
  fi
}

smoke_mcp_fails_closed() {
  local status
  if [[ -n "$HOST_PORT" ]]; then
    status="$(curl -sS -o /dev/null -w "%{http_code}" -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' "http://127.0.0.1:$HOST_PORT/mcp")"
  else
    status="$(docker run --rm --network "$NETWORK" curlimages/curl:latest -sS -o /dev/null -w "%{http_code}" -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' "http://${ALIASES[0]}:8765/mcp")"
  fi
  [[ "$status" == "401" || "$status" == "403" ]]
}

wait_for_smoke() {
  for _ in {1..80}; do
    if smoke_url /livez 2>/dev/null && smoke_mcp_fails_closed 2>/dev/null; then
      return 0
    fi
    if ! docker ps --format '{{.Names}}' | grep -qx "$CONTAINER"; then
      docker logs "$CONTAINER" >&2 || true
      return 1
    fi
    sleep 0.5
  done
  docker logs "$CONTAINER" >&2 || true
  return 1
}

rollback() {
  if [[ -n "$OLD_TARGET" ]]; then
    echo "Promotion smoke failed; rolling back to $OLD_TARGET" >&2
    repoint_current "$OLD_TARGET"
    run_container
    wait_for_smoke || true
  else
    echo "Promotion smoke failed and no previous index exists; stopping $CONTAINER" >&2
    docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
  fi
}

run_container
if ! wait_for_smoke; then
  rollback
  exit 1
fi

cat <<EOF
Promoted YouKnowMe index:
  artifact: $(basename "$ARTIFACT_ZIP")
  build:    $BUILD_NAME
  provider: $EMBEDDING_PROVIDER
  model:    $EMBEDDING_MODEL
  current:  $CURRENT_LINK -> $BUILD_DIR
EOF
