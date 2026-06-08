#!/usr/bin/env bash
set -euo pipefail

if ! command -v zip >/dev/null 2>&1; then
  echo "Required command not found: zip" >&2
  exit 1
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

SMOKE_ROOT=".ykm/index-promotion-smoke"
DEPLOY_ROOT="$SMOKE_ROOT/deploy"
NETWORK="ykm-index-promotion-smoke"
CONTAINER="ykm-index-promotion-smoke"
IMAGE="youknowme:index-promotion-smoke"
PORT="${YKM_INDEX_PROMOTION_SMOKE_PORT:-8876}"
SECRET="index-promotion-smoke-secret"

cleanup() {
  docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
  docker network rm "$NETWORK" >/dev/null 2>&1 || true
}
trap cleanup EXIT

make_zip() {
  local artifacts_dir="$1"
  local zip_path="$2"
  local absolute_zip
  absolute_zip="$(cd "$(dirname "$zip_path")" && pwd)/$(basename "$zip_path")"
  (
    cd "$artifacts_dir"
    zip -q "$absolute_zip" ./*
  )
}

container_index_field() {
  local field="$1"
  docker exec -i "$CONTAINER" python - "$field" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

field = sys.argv[1]
manifest = json.loads(Path("/data/index/manifest.json").read_text(encoding="utf-8"))
print(manifest[field])
PY
}

rm -rf "$SMOKE_ROOT"
mkdir -p "$SMOKE_ROOT/corpus-a" "$SMOKE_ROOT/corpus-b" "$SMOKE_ROOT/artifacts-a" "$SMOKE_ROOT/artifacts-b" "$DEPLOY_ROOT"

cp -a fixtures/corpus/. "$SMOKE_ROOT/corpus-a/"
cp -a fixtures/corpus/. "$SMOKE_ROOT/corpus-b/"
cat > "$SMOKE_ROOT/corpus-b/notes/promotion-smoke.md" <<'EOF'
---
id: promotion-smoke-note
type: note
tags: [promotion-smoke]
---

# Promotion Smoke Note

This document exists only in the promoted index.
EOF

export YKM_EMBEDDING_PROVIDER=fake
uv run ykm build --corpus "$SMOKE_ROOT/corpus-a" --out "$SMOKE_ROOT/index-a" >/dev/null
uv run ykm package-index --index "$SMOKE_ROOT/index-a" --out "$SMOKE_ROOT/artifacts-a" >/dev/null
uv run ykm build --corpus "$SMOKE_ROOT/corpus-b" --out "$SMOKE_ROOT/index-b" >/dev/null
uv run ykm package-index --index "$SMOKE_ROOT/index-b" --out "$SMOKE_ROOT/artifacts-b" >/dev/null

ZIP_A="$SMOKE_ROOT/index-a.zip"
ZIP_B="$SMOKE_ROOT/index-b.zip"
make_zip "$SMOKE_ROOT/artifacts-a" "$ZIP_A"
make_zip "$SMOKE_ROOT/artifacts-b" "$ZIP_B"

cat > "$DEPLOY_ROOT/runtime.env" <<EOF
YKM_AUTH_MODE=local
YKM_INDEX_PATH=/data/index
YKM_LOG_PATH=/data/logs/query-log.jsonl
YKM_LOG_RETENTION_DAYS=90
YKM_INTAKE_PATH=/data/intake
YKM_EMBEDDING_PROVIDER=fake
YKM_EMBEDDING_MODEL=fake-hashing-v1
YKM_EMBEDDING_DIMENSIONS=64
YKM_LOCAL_AUTH_SECRET=$SECRET
EOF

docker build -t "$IMAGE" .
docker network create "$NETWORK" >/dev/null

scripts/relaunch-container-with-new-index.sh \
  --artifact "$ZIP_A" \
  --deploy-root "$DEPLOY_ROOT" \
  --image "$IMAGE" \
  --container "$CONTAINER" \
  --network "$NETWORK" \
  --alias youknowme \
  --env-file "$DEPLOY_ROOT/runtime.env" \
  --host-port "$PORT" \
  --no-restart-policy

BUILD_A="$(container_index_field build_id)"
echo "Mounted build after first promotion: $BUILD_A"

scripts/relaunch-container-with-new-index.sh \
  --artifact "$ZIP_B" \
  --deploy-root "$DEPLOY_ROOT" \
  --image "$IMAGE" \
  --container "$CONTAINER" \
  --network "$NETWORK" \
  --alias youknowme \
  --env-file "$DEPLOY_ROOT/runtime.env" \
  --host-port "$PORT" \
  --no-restart-policy

BUILD_B="$(container_index_field build_id)"
echo "Mounted build after second promotion: $BUILD_B"
if [[ "$BUILD_A" == "$BUILD_B" ]]; then
  echo "Expected build_id to change after promotion" >&2
  exit 1
fi

uv run python - "$PORT" "$SECRET" <<'PY'
from __future__ import annotations

import json
import sys
from datetime import timedelta

import anyio
from mcp.client.session_group import ClientSessionGroup, StreamableHttpParameters

port = sys.argv[1]
secret = sys.argv[2]


async def main() -> None:
    async with ClientSessionGroup() as group:
        session = await group.connect_to_server(
            StreamableHttpParameters(
                url=f"http://127.0.0.1:{port}/mcp",
                headers={"X-YKM-Local-Secret": secret},
                timeout=timedelta(seconds=30),
                sse_read_timeout=timedelta(seconds=30),
            )
        )
        query = await session.call_tool(
            "query",
            {"query": "promotion smoke note", "tags": ["promotion-smoke"], "limit": 1},
        )
        payload = json.loads(query.content[0].text)
        assert payload["results"], payload
        assert payload["results"][0]["source_id"] == "promotion-smoke-note"


anyio.run(main)
PY

CORRUPT_DIR="$SMOKE_ROOT/corrupt"
mkdir -p "$CORRUPT_DIR"
cp "$SMOKE_ROOT/artifacts-b"/* "$CORRUPT_DIR/"
printf '\ncorruption\n' >> "$CORRUPT_DIR"/*.tar.gz
CORRUPT_ZIP="$SMOKE_ROOT/corrupt.zip"
make_zip "$CORRUPT_DIR" "$CORRUPT_ZIP"

if scripts/relaunch-container-with-new-index.sh \
  --artifact "$CORRUPT_ZIP" \
  --deploy-root "$DEPLOY_ROOT" \
  --image "$IMAGE" \
  --container "$CONTAINER" \
  --network "$NETWORK" \
  --alias youknowme \
  --env-file "$DEPLOY_ROOT/runtime.env" \
  --host-port "$PORT" \
  --no-restart-policy; then
  echo "Expected corrupt artifact promotion to fail" >&2
  exit 1
fi

BUILD_AFTER_CORRUPT="$(container_index_field build_id)"
if [[ "$BUILD_AFTER_CORRUPT" != "$BUILD_B" ]]; then
  echo "Corrupt artifact changed active build_id" >&2
  exit 1
fi

echo "{\"status\":\"ok\",\"initial_build\":\"$BUILD_A\",\"promoted_build\":\"$BUILD_B\"}"
