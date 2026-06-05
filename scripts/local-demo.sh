#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INDEX_DIR="$ROOT_DIR/.ykm/demo-index"

cd "$ROOT_DIR"
rm -rf "$INDEX_DIR"

YKM_EMBEDDING_PROVIDER=fake uv run ykm build --corpus fixtures/corpus --out "$INDEX_DIR"
YKM_EMBEDDING_PROVIDER=fake uv run ykm query "weekly spa maintenance" --index "$INDEX_DIR" --tag spa --limit 3

