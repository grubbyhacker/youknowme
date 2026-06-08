#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  scripts/watch-and-promote-corpus-index.sh [options]

Options:
  --deploy-root PATH      Deployment root. Default: /opt/youknowme
  --image NAME            YKM image containing ykm-download-latest-corpus-index. Default: youknowme:phase1e
  --app-id ID             GitHub App ID. Default: 4001682
  --installation-id ID    GitHub App installation ID. Default: 138954168
  --private-key PATH      GitHub App private key path under deploy root.
                          Default: <deploy-root>/secrets/ykmcorpus-build-watcher.private-key.pem
  --promote-script PATH   Host promotion script. Default: <deploy-root>/bin/relaunch-container-with-new-index.sh
  --watcher-arg ARG       Additional argument forwarded to the containerized watcher. May be repeated.
  --promote-arg ARG       Additional argument forwarded to the host promotion script. May be repeated.
  --help                  Show this help.
EOF
}

DEPLOY_ROOT="/opt/youknowme"
IMAGE="youknowme:phase1e"
APP_ID="4001682"
INSTALLATION_ID="138954168"
PRIVATE_KEY=""
PROMOTE_SCRIPT=""
WATCHER_ARGS=()
PROMOTE_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --deploy-root)
      DEPLOY_ROOT="${2:?--deploy-root requires a path}"
      shift 2
      ;;
    --image)
      IMAGE="${2:?--image requires a name}"
      shift 2
      ;;
    --app-id)
      APP_ID="${2:?--app-id requires a value}"
      shift 2
      ;;
    --installation-id)
      INSTALLATION_ID="${2:?--installation-id requires a value}"
      shift 2
      ;;
    --private-key)
      PRIVATE_KEY="${2:?--private-key requires a path}"
      shift 2
      ;;
    --promote-script)
      PROMOTE_SCRIPT="${2:?--promote-script requires a path}"
      shift 2
      ;;
    --watcher-arg)
      WATCHER_ARGS+=("${2:?--watcher-arg requires a value}")
      shift 2
      ;;
    --promote-arg)
      PROMOTE_ARGS+=("${2:?--promote-arg requires a value}")
      shift 2
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

if [[ -z "$PRIVATE_KEY" ]]; then
  PRIVATE_KEY="$DEPLOY_ROOT/secrets/ykmcorpus-build-watcher.private-key.pem"
fi
if [[ -z "$PROMOTE_SCRIPT" ]]; then
  PROMOTE_SCRIPT="$DEPLOY_ROOT/bin/relaunch-container-with-new-index.sh"
fi

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Required command not found: $1" >&2
    exit 1
  fi
}

require_command docker

DEPLOY_ROOT="$(cd "$DEPLOY_ROOT" && pwd)"
PRIVATE_KEY="$(cd "$(dirname "$PRIVATE_KEY")" && pwd)/$(basename "$PRIVATE_KEY")"
PROMOTE_SCRIPT="$(cd "$(dirname "$PROMOTE_SCRIPT")" && pwd)/$(basename "$PROMOTE_SCRIPT")"
INCOMING_DIR="$DEPLOY_ROOT/incoming"
STATE_DIR="$DEPLOY_ROOT/watcher-state"
ARTIFACT_PATH_FILE="$STATE_DIR/latest-artifact.path"

if [[ ! -f "$PRIVATE_KEY" ]]; then
  echo "GitHub App private key does not exist: $PRIVATE_KEY" >&2
  exit 1
fi
if [[ ! -x "$PROMOTE_SCRIPT" ]]; then
  echo "Promotion script is not executable: $PROMOTE_SCRIPT" >&2
  exit 1
fi

mkdir -p "$INCOMING_DIR" "$STATE_DIR"
rm -f "$ARTIFACT_PATH_FILE"

set +e
docker run --rm \
  --user root \
  -v "$DEPLOY_ROOT:$DEPLOY_ROOT" \
  "$IMAGE" \
  ykm-download-latest-corpus-index \
    --app-id "$APP_ID" \
    --installation-id "$INSTALLATION_ID" \
    --private-key "$PRIVATE_KEY" \
    --out-dir "$INCOMING_DIR" \
    --deploy-root "$DEPLOY_ROOT" \
    --artifact-path-file "$ARTIFACT_PATH_FILE" \
    --exit-code-current \
    "${WATCHER_ARGS[@]}"
watcher_status=$?
set -e

if [[ "$watcher_status" -eq 10 ]]; then
  echo "Latest official corpus index is already serving; no promotion needed."
  exit 0
fi
if [[ "$watcher_status" -ne 0 ]]; then
  echo "Corpus index watcher failed with exit code $watcher_status" >&2
  exit "$watcher_status"
fi
if [[ ! -s "$ARTIFACT_PATH_FILE" ]]; then
  echo "Watcher succeeded but did not write an artifact path: $ARTIFACT_PATH_FILE" >&2
  exit 1
fi

ARTIFACT_ZIP="$(head -n 1 "$ARTIFACT_PATH_FILE")"
if [[ ! -f "$ARTIFACT_ZIP" ]]; then
  echo "Downloaded artifact path does not exist: $ARTIFACT_ZIP" >&2
  exit 1
fi

"$PROMOTE_SCRIPT" --artifact "$ARTIFACT_ZIP" "${PROMOTE_ARGS[@]}"
