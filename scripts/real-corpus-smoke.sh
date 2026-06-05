#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

REQUESTED_YKM_CORPUS_PATH="${YKM_CORPUS_PATH:-}"
REQUESTED_YKM_REAL_INDEX_PATH="${YKM_REAL_INDEX_PATH:-}"
REQUESTED_YKM_EMBEDDING_PROVIDER="${YKM_EMBEDDING_PROVIDER:-}"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

if [[ -n "$REQUESTED_YKM_CORPUS_PATH" ]]; then
  YKM_CORPUS_PATH="$REQUESTED_YKM_CORPUS_PATH"
fi
if [[ -n "$REQUESTED_YKM_REAL_INDEX_PATH" ]]; then
  YKM_REAL_INDEX_PATH="$REQUESTED_YKM_REAL_INDEX_PATH"
fi
if [[ -n "$REQUESTED_YKM_EMBEDDING_PROVIDER" ]]; then
  YKM_EMBEDDING_PROVIDER="$REQUESTED_YKM_EMBEDDING_PROVIDER"
fi

CORPUS_PATH="${YKM_CORPUS_PATH:-../ykmcorpus}"
INDEX_DIR="${YKM_REAL_INDEX_PATH:-.ykm/real-index}"
PROVIDER="${YKM_EMBEDDING_PROVIDER:-fake}"

if [[ ! -d "$CORPUS_PATH" ]]; then
  echo "Corpus path does not exist: $CORPUS_PATH" >&2
  exit 1
fi

if [[ "$PROVIDER" == "openrouter" && -z "${OPENROUTER_API_KEY:-}" ]]; then
  echo "OPENROUTER_API_KEY is required when YKM_EMBEDDING_PROVIDER=openrouter" >&2
  exit 1
fi

BUILD_OUTPUT="$(mktemp -t ykm-real-corpus-smoke.XXXXXX)"
trap 'rm -f "$BUILD_OUTPUT"' EXIT

rm -rf "$INDEX_DIR"
uv run ykm build --corpus "$CORPUS_PATH" --out "$INDEX_DIR" > "$BUILD_OUTPUT"

python - <<'PY'
import json
import os
from pathlib import Path

index_dir = Path(os.getenv("YKM_REAL_INDEX_PATH", ".ykm/real-index"))
manifest = json.loads((index_dir / "manifest.json").read_text())
warnings_path = index_dir / "warnings.jsonl"
quarantine_path = index_dir / "quarantine.jsonl"
warnings = [line for line in warnings_path.read_text().splitlines() if line.strip()] if warnings_path.exists() else []
quarantined = [line for line in quarantine_path.read_text().splitlines() if line.strip()] if quarantine_path.exists() else []

print(
    json.dumps(
        {
            "build_id": manifest["build_id"],
            "source_commit": manifest["source_commit"],
            "embedding_provider": manifest["embedding_provider"],
            "embedding_model": manifest["embedding_model"],
            "chunk_count": manifest["chunk_count"],
            "warning_count": len(warnings),
            "quarantined_count": len(quarantined),
            "index_path": str(index_dir),
        },
        indent=2,
        sort_keys=True,
    )
)
PY
