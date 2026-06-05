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
```

Manual equivalent:

```bash
YKM_EMBEDDING_PROVIDER=fake uv run ykm build --corpus fixtures/corpus --out .ykm/demo-index
YKM_EMBEDDING_PROVIDER=fake uv run ykm query "weekly spa maintenance" --index .ykm/demo-index --tag spa
```

To use real embeddings later, create `.env` from `.env.example`, set `OPENROUTER_API_KEY`, and use:

```bash
YKM_EMBEDDING_PROVIDER=openrouter uv run ykm build --corpus /path/to/content-repo --out .ykm/openrouter-index
```

Generated indexes and logs live under `.ykm/` and are ignored.
