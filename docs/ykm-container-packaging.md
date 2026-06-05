# YouKnowMe Container Packaging

Phase 1C packages the validated local serving path without changing product scope.

## Image

The production image is built from `Dockerfile`.

- Runtime command: `ykm serve --index "$YKM_INDEX_PATH" --mode "$YKM_AUTH_MODE" --host 0.0.0.0`.
- Default index path: `/data/index`.
- Default log path: `/data/logs/query-log.jsonl`.
- Runtime user: unprivileged `ykm`.
- Healthcheck: `GET /livez`.

The Docker build context excludes `.git/`, `.env*`, `.ykm/`, `artifacts/`, and `POC/`. The image
contains application code and dependencies only; it does not include corpus content, generated index
artifacts, local secrets, or repository write credentials.

## Local Compose

`compose.yaml` is the local container run path.

- Mounts `${YKM_CONTAINER_INDEX_PATH:-.ykm/real-index}` at `/data/index` as read-only.
- Mounts `${YKM_CONTAINER_LOG_DIR:-.ykm/container-smoke/logs}` at `/data/logs` as writable.
- Uses a read-only root filesystem with `/tmp` as tmpfs.
- Requires `YKM_LOCAL_AUTH_SECRET` for local mode.

Build and run manually:

```bash
export YKM_LOCAL_AUTH_SECRET=local-dev-secret
docker compose up --build youknowme
```

The service listens on `${YKM_CONTAINER_PORT:-8765}` on the host and exposes MCP at `/mcp`.

## Smoke Test

The repeatable Phase 1C smoke is:

```bash
mise run container-smoke
```

The smoke script:

- uses `.ykm/real-index` by default, or `YKM_CONTAINER_INDEX_PATH` when set;
- derives embedding provider, model, and dimensions from the mounted artifact manifest unless the
  caller explicitly overrides them;
- starts the Compose service;
- checks `/livez`;
- verifies unauthenticated `/mcp` fails closed;
- authenticates with `X-YKM-Local-Secret`;
- verifies MCP `health`, `query`, and `retrieve`;
- verifies query logs contain returned source IDs and not raw query text or returned content.

For real OpenRouter-backed indexes, `.env` must contain `OPENROUTER_API_KEY`.
