# Agent Handoff

## Current State

YouKnowMe is now a greenfield production codebase outside `POC/`. The `POC/` directory is preserved
as reference-only for the working Cloudflare Tunnel / Cloudflare Access / MCP prototype and should
not receive new production code.

Phase 1 currently implements a local-first RAG path over markdown:

- `src/ykm/build.py` loads markdown, parses simple frontmatter, performs structural chunking, emits
  warnings, quarantines high-confidence secrets, embeds chunks, and writes a LanceDB-backed artifact.
- `src/ykm/index.py` loads the artifact and supports semantic `query`, deterministic `retrieve`, and
  health provenance.
- `src/ykm/server.py` exposes FastMCP tools for `query`, `retrieve`, and authenticated `health`, plus
  a private `/livez` endpoint.
- `src/ykm/auth.py` separates public Cloudflare JWT auth from local/Hermes shared-secret auth.
- `src/ykm/logging.py` writes protected JSONL query logs that record returned source IDs, not query
  text or returned content.
- `fixtures/corpus/` is synthetic-only and exercises ambiguous same-kind subjects, preferences,
  writing, projects, bare markdown, and secret quarantine.

Useful commands:

```bash
mise run test
mise run lint
mise run demo
YKM_EMBEDDING_PROVIDER=fake uv run ykm build --corpus fixtures/corpus --out .ykm/demo-index
YKM_EMBEDDING_PROVIDER=fake uv run ykm query "weekly spa maintenance" --index .ykm/demo-index --tag spa
```

## Verification Completed

- `mise run test`: 17 tests passing.
- `mise run lint`: Ruff passing.
- `mise run demo`: builds the synthetic corpus and returns distinct spa maintenance results.
- Local server smoke: `/livez` returned process liveness, unauthenticated `/mcp` returned 403.

## Important Lessons

- Keep milestones demonstrable. Every implementation slice should end with a CLI command, test, or
  local server behavior that proves it works without the VPS.
- Tests must stay offline by default. Fake deterministic embeddings are the default for unit tests
  and demos; OpenRouter is optional runtime configuration.
- Do not log raw query text or returned content by default. Logs intentionally record source IDs
  because future Curator work needs that signal, but logs are a protected asset.
- Do not relax public Cloudflare JWT auth to support local Hermes. The local path has a separate
  shared-secret mechanism.
- LanceDB is a pragmatic embedded vector store choice for now. Keep vector access behind `YkmIndex`
  so it can be replaced if evidence points to sqlite-vec, FAISS, Chroma, pgvector, or a service.
- The current fake embedding rankings are good enough for contract/eval testing, not a quality proxy
  for real retrieval. Use OpenRouter before judging retrieval quality.

## Next Work

1. Point the build at the new private content repo using a local checkout path.
2. Load `.env` automatically or document a preferred `mise`/shell flow for runtime env loading.
3. Build a real OpenRouter index using `OPENROUTER_API_KEY` from local `.env`; never commit `.env`.
4. Run smoke queries against the real corpus and record retrieval quality gaps.
5. Expand the eval harness with private, uncommitted golden cases or committed synthetic cases that
   mirror observed real-corpus failures.
6. Improve retrieval quality only after observing real OpenRouter behavior.
7. Add container packaging later, after local RAG behavior is complete and tested. Do not move to the
   VPS yet.

