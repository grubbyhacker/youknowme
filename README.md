# YouKnowMe

YouKnowMe is the production project that will supersede the Phase 0 MCP proof of concept.

The preserved prototype lives in `POC/` and is reference-only unless explicitly being maintained as the running prototype on the VPS.

## Repository Layout

- `docs/` - PRD and supporting design documentation.
- `POC/` - preserved Phase 0 reference implementation.

## Local Tooling

This repository uses `mise` and `uv`.

```bash
mise trust
mise install
mise run sync
```

## Local RAG Demo

Phase 1 is local-first. The synthetic corpus in `fixtures/corpus/` exercises the full build and query path without real personal data, OpenRouter, or the VPS.

```bash
mise run test
mise run lint
mise run demo
mise run eval
YKM_EMBEDDING_PROVIDER=openrouter mise run local-mcp-smoke
mise run container-smoke
```

GitHub CI runs `mise run lint` and `mise run test`. Run both before opening or updating a PR that
changes code, tests, packaging, or workflows.

Manual equivalent:

```bash
YKM_EMBEDDING_PROVIDER=fake uv run ykm build --corpus fixtures/corpus --out .ykm/demo-index
YKM_EMBEDDING_PROVIDER=fake uv run ykm query "weekly spa maintenance" --index .ykm/demo-index --tag spa
YKM_EMBEDDING_PROVIDER=fake uv run ykm eval --index .ykm/demo-index --cases fixtures/eval/synthetic.json
```

To use real embeddings later, create `.env` from `.env.example`, set `OPENROUTER_API_KEY`, and use:

```bash
YKM_EMBEDDING_PROVIDER=openrouter uv run ykm build --corpus /path/to/content-repo --out .ykm/openrouter-index
```

Private eval cases can live outside this repo and be passed with `--cases /path/to/private-cases.json`.

Generated indexes and logs live under `.ykm/` and are ignored.

## Live MCP CLI

Use `ykm live` to call the production MCP route from a development machine with local Cloudflare
Access credentials in `.env`.

```bash
uv run ykm live health --pretty
uv run ykm live query "thermostat setup" --limit 2 --pretty
uv run ykm live feedback --category agent_note --comment "live CLI smoke" --dry-run --pretty
```

See `docs/ykm-live-cli.md` for auth variables, upload examples, and write-safety behavior.

## Reusable Index Artifact Tools

Private corpus repositories can install this package and use the public YKM build tooling without
publishing private corpus content through this public repository.

```bash
YKM_EMBEDDING_PROVIDER=openrouter uv run ykm build --corpus /path/to/ykmcorpus --out .ykm/prod-index
uv run ykm validate-index --index .ykm/prod-index
uv run ykm package-index --index .ykm/prod-index --out artifacts
```

`package-index` writes a versioned `.tar.gz` bundle, `.sha256`, and `build-report.json`. Production
corpus artifacts should be produced by the private corpus repository CI, not by this public repo CI.

## Container Packaging

Phase 1C runs the same local serving path in a container:

```bash
mise run container-smoke
```

The compose path mounts `.ykm/real-index` read-only and writes protected query logs under
`.ykm/container-smoke/logs`. See `docs/ykm-container-packaging.md` for the packaging contract and
manual `docker compose` commands.
