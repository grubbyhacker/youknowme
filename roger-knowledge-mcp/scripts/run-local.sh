#!/usr/bin/env bash
set -euo pipefail

HOST="${MCP_HOST:-127.0.0.1}"
PORT="${MCP_PORT:-8765}"

exec uv run uvicorn roger_knowledge_mcp.server:app --host "${HOST}" --port "${PORT}"

