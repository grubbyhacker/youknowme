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
mise run real-smoke
YKM_EMBEDDING_PROVIDER=fake uv run ykm build --corpus fixtures/corpus --out .ykm/demo-index
YKM_EMBEDDING_PROVIDER=fake uv run ykm query "weekly spa maintenance" --index .ykm/demo-index --tag spa
```

## Verification Completed

- `mise run test`: 17 tests passing.
- `mise run lint`: Ruff passing.
- `mise run demo`: builds the synthetic corpus and returns distinct spa maintenance results.
- `mise run real-smoke`: builds the private local corpus checkout at `~/src/ykmcorpus` into
  `.ykm/real-index` and prints aggregate build metadata only. Last observed real-corpus smoke:
  18 markdown files, 268 chunks, 0 quarantined files, 39 structural/frontmatter warnings, fake
  embeddings.
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
- The private corpus repo exists at `git@github.com:grubbyhacker/ykmcorpus.git` with a local clone at
  `~/src/ykmcorpus`. Treat it as sensitive input. Do not copy corpus content into this service repo.
- `.env` exists locally and contains the OpenRouter API key. It is ignored by Git. Never commit it.
- The agreed first real embedding model is `openai/text-embedding-3-small` through OpenRouter at
  1536 dimensions. Do not add a reranker until an eval shows embedding-only retrieval has a specific
  failure pattern.

## Restart Instructions

After restart, continue from local real-corpus evaluation. Stay in this repo and do not work on VPS
deployment.

1. Run `git status --short --branch` and confirm the branch is clean except ignored `.env`, `.ykm/`,
   caches, and POC runtime files.
2. Run `mise run test` and `mise run lint`.
3. Run `mise run real-smoke` to confirm the private corpus still builds.
4. Build a real OpenRouter index:

   ```bash
   YKM_EMBEDDING_PROVIDER=openrouter mise run real-smoke
   ```

   This reads `OPENROUTER_API_KEY` from local `.env` and writes ignored artifacts under
   `.ykm/real-index`.
5. Run a few manual private-corpus queries with OpenRouter embeddings. Do not paste sensitive
   content into commits or docs; summarize retrieval behavior by source IDs/paths and aggregate
   observations.
6. Create a local eval command/harness next. It should support private uncommitted golden cases and
   committed synthetic regression cases. Measure expected source/section in top 1, top 3, and top 5.
7. Improve chunking/frontmatter guidance before adding model complexity. Only consider a reranker if
   the correct section often appears in top 5 but not top 1/top 3.
8. Add container packaging later, after local RAG behavior is complete and tested.
