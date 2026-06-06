#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

REQUESTED_YKM_EMBEDDING_PROVIDER="${YKM_EMBEDDING_PROVIDER:-}"
REQUESTED_YKM_EMBEDDING_MODEL="${YKM_EMBEDDING_MODEL:-}"
REQUESTED_YKM_EMBEDDING_DIMENSIONS="${YKM_EMBEDDING_DIMENSIONS:-}"
REQUESTED_YKM_CONTAINER_INDEX_PATH="${YKM_CONTAINER_INDEX_PATH:-}"
REQUESTED_YKM_CONTAINER_LOG_DIR="${YKM_CONTAINER_LOG_DIR:-}"
REQUESTED_YKM_CONTAINER_INTAKE_DIR="${YKM_CONTAINER_INTAKE_DIR:-}"
REQUESTED_YKM_CONTAINER_PORT="${YKM_CONTAINER_PORT:-}"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

if [[ -n "$REQUESTED_YKM_EMBEDDING_PROVIDER" ]]; then
  YKM_EMBEDDING_PROVIDER="$REQUESTED_YKM_EMBEDDING_PROVIDER"
fi
if [[ -n "$REQUESTED_YKM_EMBEDDING_MODEL" ]]; then
  YKM_EMBEDDING_MODEL="$REQUESTED_YKM_EMBEDDING_MODEL"
fi
if [[ -n "$REQUESTED_YKM_EMBEDDING_DIMENSIONS" ]]; then
  YKM_EMBEDDING_DIMENSIONS="$REQUESTED_YKM_EMBEDDING_DIMENSIONS"
fi
if [[ -n "$REQUESTED_YKM_CONTAINER_INDEX_PATH" ]]; then
  YKM_CONTAINER_INDEX_PATH="$REQUESTED_YKM_CONTAINER_INDEX_PATH"
fi
if [[ -n "$REQUESTED_YKM_CONTAINER_LOG_DIR" ]]; then
  YKM_CONTAINER_LOG_DIR="$REQUESTED_YKM_CONTAINER_LOG_DIR"
fi
if [[ -n "$REQUESTED_YKM_CONTAINER_INTAKE_DIR" ]]; then
  YKM_CONTAINER_INTAKE_DIR="$REQUESTED_YKM_CONTAINER_INTAKE_DIR"
fi
if [[ -n "$REQUESTED_YKM_CONTAINER_PORT" ]]; then
  YKM_CONTAINER_PORT="$REQUESTED_YKM_CONTAINER_PORT"
fi

INDEX_DIR="${YKM_CONTAINER_INDEX_PATH:-${YKM_REAL_INDEX_PATH:-.ykm/real-index}}"
LOG_DIR="${YKM_CONTAINER_LOG_DIR:-.ykm/container-smoke/logs}"
INTAKE_DIR="${YKM_CONTAINER_INTAKE_DIR:-.ykm/container-smoke/intake}"
PORT="${YKM_CONTAINER_PORT:-8765}"
SECRET="${YKM_LOCAL_AUTH_SECRET:-container-smoke-secret}"

if [[ ! -f "$INDEX_DIR/manifest.json" ]]; then
  echo "Index does not exist: $INDEX_DIR" >&2
  echo "Build one first, for example: YKM_EMBEDDING_PROVIDER=openrouter mise run real-smoke" >&2
  exit 1
fi

if [[ -z "$REQUESTED_YKM_EMBEDDING_PROVIDER" ]]; then
  YKM_EMBEDDING_PROVIDER="$(
    uv run python - "$INDEX_DIR/manifest.json" <<'PY'
import json
import sys
from pathlib import Path

print(json.loads(Path(sys.argv[1]).read_text())["embedding_provider"])
PY
  )"
fi
if [[ -z "$REQUESTED_YKM_EMBEDDING_MODEL" ]]; then
  YKM_EMBEDDING_MODEL="$(
    uv run python - "$INDEX_DIR/manifest.json" <<'PY'
import json
import sys
from pathlib import Path

print(json.loads(Path(sys.argv[1]).read_text())["embedding_model"])
PY
  )"
fi
if [[ -z "$REQUESTED_YKM_EMBEDDING_DIMENSIONS" ]]; then
  YKM_EMBEDDING_DIMENSIONS="$(
    uv run python - "$INDEX_DIR/manifest.json" <<'PY'
import json
import sys
from pathlib import Path

print(json.loads(Path(sys.argv[1]).read_text())["embedding_dimensions"])
PY
  )"
fi

PROVIDER="${YKM_EMBEDDING_PROVIDER:-fake}"

if [[ "$PROVIDER" == "openrouter" && -z "${OPENROUTER_API_KEY:-}" ]]; then
  echo "OPENROUTER_API_KEY is required when YKM_EMBEDDING_PROVIDER=openrouter" >&2
  exit 1
fi

mkdir -p "$LOG_DIR"
rm -rf "$INTAKE_DIR"
mkdir -p "$INTAKE_DIR"
rm -f "$LOG_DIR/query-log.jsonl"

export YKM_CONTAINER_INDEX_PATH="$INDEX_DIR"
export YKM_CONTAINER_LOG_DIR="$LOG_DIR"
export YKM_CONTAINER_INTAKE_DIR="$INTAKE_DIR"
export YKM_CONTAINER_PORT="$PORT"
export YKM_LOCAL_AUTH_SECRET="$SECRET"
export YKM_EMBEDDING_PROVIDER="$PROVIDER"
export YKM_EMBEDDING_MODEL
export YKM_EMBEDDING_DIMENSIONS

cleanup() {
  docker compose down --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker compose up --build -d youknowme

for _ in {1..80}; do
  if curl -fsS "http://127.0.0.1:$PORT/livez" >/dev/null 2>&1; then
    break
  fi
  if [[ "$(docker compose ps -q youknowme | wc -l | tr -d ' ')" == "0" ]]; then
    docker compose logs youknowme >&2
    exit 1
  fi
  sleep 0.5
done

uv run python - "$PORT" "$SECRET" "$LOG_DIR/query-log.jsonl" "$INTAKE_DIR" <<'PY'
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
intake_dir = Path(sys.argv[4])
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
        assert {"query", "retrieve", "health", "upload", "feedback"}.issubset(tool_names), tool_names
        tool_descriptions = {tool.name: tool.description or "" for tool in tools.tools}
        assert "owner-specific" in tool_descriptions["query"]
        assert "hot tub chemistry" in tool_descriptions["query"]
        assert "owner-specific" in tool_descriptions["search"]
        assert "does not publish, index, or merge" in tool_descriptions["upload"]
        assert "not indexed" in tool_descriptions["feedback"]

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

        upload = await session.call_tool(
            "upload",
            {
                "files": [
                    {
                        "filename": "smoke-note.md",
                        "content": "# Smoke Note\n\nA bounded staged upload for container smoke.",
                    }
                ],
                "purpose": "container smoke",
                "suggested_type": "note",
                "suggested_tags": ["smoke"],
            },
        )
        upload_payload = json.loads(upload.content[0].text)
        assert upload_payload["accepted"] is True
        assert upload_payload["status"] == "pending"
        staged_path = intake_dir / upload_payload["staged_path"]
        assert (staged_path / "manifest.json").exists()
        assert (staged_path / "files" / "smoke-note.md").exists()

        feedback = await session.call_tool(
            "feedback",
            {
                "category": "agent_note",
                "comment": "Container smoke verified bounded feedback logging.",
                "upload_id": upload_payload["upload_id"],
            },
        )
        feedback_payload = json.loads(feedback.content[0].text)
        assert feedback_payload["accepted"] is True
        assert (intake_dir / feedback_payload["path"]).exists()

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
