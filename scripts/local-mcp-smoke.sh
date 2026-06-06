#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

REQUESTED_YKM_EMBEDDING_PROVIDER="${YKM_EMBEDDING_PROVIDER:-}"
REQUESTED_YKM_REAL_INDEX_PATH="${YKM_REAL_INDEX_PATH:-}"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

if [[ -n "$REQUESTED_YKM_EMBEDDING_PROVIDER" ]]; then
  YKM_EMBEDDING_PROVIDER="$REQUESTED_YKM_EMBEDDING_PROVIDER"
fi
if [[ -n "$REQUESTED_YKM_REAL_INDEX_PATH" ]]; then
  YKM_REAL_INDEX_PATH="$REQUESTED_YKM_REAL_INDEX_PATH"
fi

INDEX_DIR="${YKM_REAL_INDEX_PATH:-.ykm/real-index}"
PROVIDER="${YKM_EMBEDDING_PROVIDER:-fake}"
PORT="${YKM_LOCAL_SMOKE_PORT:-8765}"
SECRET="${YKM_LOCAL_AUTH_SECRET:-local-smoke-secret}"
LOG_PATH="${YKM_LOCAL_SMOKE_LOG_PATH:-.ykm/local-smoke/query-log.jsonl}"
SERVER_LOG="$(mktemp -t ykm-local-mcp-smoke-server.XXXXXX)"

cleanup() {
  if [[ -n "${SERVER_PID:-}" ]] && kill -0 "$SERVER_PID" 2>/dev/null; then
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
  rm -f "$SERVER_LOG"
}
trap cleanup EXIT

if [[ ! -f "$INDEX_DIR/manifest.json" ]]; then
  echo "Index does not exist: $INDEX_DIR" >&2
  echo "Build one first, for example: YKM_EMBEDDING_PROVIDER=openrouter mise run real-smoke" >&2
  exit 1
fi

if [[ "$PROVIDER" == "openrouter" && -z "${OPENROUTER_API_KEY:-}" ]]; then
  echo "OPENROUTER_API_KEY is required when YKM_EMBEDDING_PROVIDER=openrouter" >&2
  exit 1
fi

mkdir -p "$(dirname "$LOG_PATH")"
rm -f "$LOG_PATH"

export YKM_EMBEDDING_PROVIDER="$PROVIDER"
export YKM_LOCAL_AUTH_SECRET="$SECRET"
export YKM_LOG_PATH="$LOG_PATH"

uv run ykm serve --index "$INDEX_DIR" --mode local --host 127.0.0.1 --port "$PORT" \
  > "$SERVER_LOG" 2>&1 &
SERVER_PID=$!

for _ in {1..50}; do
  if curl -fsS "http://127.0.0.1:$PORT/livez" >/dev/null 2>&1; then
    break
  fi
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    cat "$SERVER_LOG" >&2
    exit 1
  fi
  sleep 0.2
done

uv run python - "$PORT" "$SECRET" "$LOG_PATH" <<'PY'
from __future__ import annotations

import json
import sys
from datetime import timedelta
from pathlib import Path

import anyio
import httpx
from mcp.client.session_group import ClientSessionGroup, StreamableHttpParameters


port = sys.argv[1]
secret = sys.argv[2]
log_path = Path(sys.argv[3])
base_url = f"http://127.0.0.1:{port}"


async def main() -> None:
    livez = httpx.get(f"{base_url}/livez", timeout=10)
    livez.raise_for_status()
    assert livez.json() == {"status": "ok", "service": "YouKnowMe"}

    forbidden = httpx.post(f"{base_url}/mcp", timeout=10)
    assert forbidden.status_code == 403, forbidden.text
    assert forbidden.json()["detail"] == "forbidden"

    async with ClientSessionGroup() as group:
        session = await group.connect_to_server(
            StreamableHttpParameters(
                url=f"{base_url}/mcp",
                headers={"X-YKM-Local-Secret": secret},
                timeout=timedelta(seconds=30),
                sse_read_timeout=timedelta(seconds=30),
            )
        )
        tools = await session.list_tools()
        tool_names = {tool.name for tool in tools.tools}
        assert {"query", "retrieve", "health"}.issubset(tool_names), tool_names
        tool_descriptions = {tool.name: tool.description or "" for tool in tools.tools}
        assert "owner-specific" in tool_descriptions["query"]
        assert "hot tub chemistry" in tool_descriptions["query"]
        assert "owner-specific" in tool_descriptions["search"]

        health = await session.call_tool("health", {})
        health_payload = json.loads(health.content[0].text)
        assert health_payload["status"] == "ok"
        assert health_payload["index_loaded"] is True
        assert health_payload["source_commit"]
        assert health_payload["build_id"]
        assert health_payload["embedding_model"]
        assert health_payload["created_at"]

        query = await session.call_tool(
            "query",
            {
                "query": "Cisco Security Business Group senior director AI engineering recruiter prep",
                "type": "interview-prep",
                "limit": 1,
            },
        )
        query_payload = json.loads(query.content[0].text)
        first = query_payload["results"][0]
        assert first["source_id"]
        assert first["source_path"]
        assert first["section_id"]

        retrieve = await session.call_tool(
            "retrieve",
            {"locator": first["section_id"], "kind": "section_id"},
        )
        retrieve_payload = json.loads(retrieve.content[0].text)
        assert retrieve_payload["found"] is True
        assert retrieve_payload["source_id"] == first["source_id"]
        assert retrieve_payload["section_id"] == first["section_id"]

    records = [json.loads(line) for line in log_path.read_text().splitlines() if line.strip()]
    assert records, "expected query log record"
    last = records[-1]
    assert last["result_source_ids"]
    assert last["result_count"] == 1
    assert "query" not in last
    assert "content" not in last
    assert "returned_content" not in last
    assert "matched_chunk" not in last

    print(
        json.dumps(
            {
                "status": "ok",
                "tools": sorted(tool_names),
                "source_commit": health_payload["source_commit"],
                "build_id": health_payload["build_id"],
                "embedding_model": health_payload["embedding_model"],
                "query_source_ids": last["result_source_ids"],
            },
            indent=2,
            sort_keys=True,
        )
    )


anyio.run(main)
PY
