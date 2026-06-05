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
- `src/ykm/eval.py` and `ykm eval` provide a local retrieval eval harness with top 1 / top 3 /
  top 5 measurement over committed synthetic cases and ignored private cases.
- `docs/ykm-corpus-authoring.md` captures current frontmatter and markdown authoring guidance for
  `ykmcorpus`.
- `docs/ykm-phased-plan.md` captures the high-level project phases and current status.

Useful commands:

```bash
mise run test
mise run lint
mise run demo
mise run eval
mise run real-smoke
YKM_EMBEDDING_PROVIDER=openrouter mise run local-mcp-smoke
YKM_EMBEDDING_PROVIDER=fake uv run ykm build --corpus fixtures/corpus --out .ykm/demo-index
YKM_EMBEDDING_PROVIDER=fake uv run ykm query "weekly spa maintenance" --index .ykm/demo-index --tag spa
YKM_EMBEDDING_PROVIDER=openrouter mise run real-smoke
YKM_EMBEDDING_PROVIDER=openrouter uv run ykm eval --index .ykm/real-index --cases .ykm/private-eval/ykmcorpus.json
```

## Verification Completed

- `mise run test`: 22 tests passing.
- `mise run lint`: Ruff passing.
- `mise run eval`: committed synthetic eval passes 6/6; last observed top 1 = 5/6, top 3 = 6/6,
  top 5 = 6/6 with fake embeddings.
- `mise run demo`: builds the synthetic corpus and returns distinct spa maintenance results.
- `mise run real-smoke`: builds the private local corpus checkout at `~/src/ykmcorpus` into
  `.ykm/real-index` and prints aggregate build metadata only. Last observed real-corpus smoke:
  18 markdown files, 287 chunks, 0 quarantined files, 14 structural warnings, OpenRouter
  embeddings.
- Private real-corpus eval in ignored `.ykm/private-eval/ykmcorpus.json`: last observed 16/16
  passing, top 1 = 16/16, top 3 = 16/16, top 5 = 16/16 with
  `openai/text-embedding-3-small`.
- Local server smoke against `.ykm/real-index`: `/livez` returned process liveness,
  unauthenticated `/mcp` returned 403, authenticated local MCP listed `query`/`retrieve`/`health`,
  `health` returned provenance, `query` returned a real source pointer, `retrieve` resolved it, and
  the query log recorded source IDs without raw query/content. This is now repeatable with
  `YKM_EMBEDDING_PROVIDER=openrouter mise run local-mcp-smoke`.

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
- `ykmcorpus` now has frontmatter IDs/types/tags and allowed `homemaint/` + `workhistory/`
  structure cleanup committed and pushed at `07c85a4` (`chore:more cleanup for better indexing`).
  Rebuild the index after pulling.
- Frontmatter improves stable IDs and filters; headings/body text improve semantic matching because
  chunk embeddings currently use chunk text, not metadata. Keep both in good shape.
- Do not reshape imported writing samples or Substack posts merely to satisfy indexing warnings.
  Treat `substack/` and `writingsamples/` as canonical unless the owner explicitly asks for edits or
  evals show a real retrieval failure. Current cleanup permission is limited to `homemaint/` and
  `workhistory/`.
- `.env` exists locally and contains the OpenRouter API key. It is ignored by Git. Never commit it.
- The agreed first real embedding model is `openai/text-embedding-3-small` through OpenRouter at
  1536 dimensions. Current evals do not justify a reranker.
- Reranker trigger remains evidence-driven: consider it only if correct sources often appear in top
  5 but not top 1/top 3 after frontmatter, headings, and filters are in good shape.

## Restart Instructions

After restart, continue from local Phase 1 hardening. Stay in this repo and do not work on VPS
deployment unless explicitly asked.

1. Run `git status --short --branch` and confirm the branch is clean except ignored `.env`, `.ykm/`,
   caches, and POC runtime files. The expected `ykmcorpus` source commit for the latest local real
   index is `07c85a4113a11b98f2a27200b5822a8e2539b8ce`.
2. Run `mise run test`, `mise run lint`, and `mise run eval`.
3. Run `YKM_EMBEDDING_PROVIDER=openrouter mise run real-smoke` to build the private corpus with real
   embeddings:

   ```bash
   YKM_EMBEDDING_PROVIDER=openrouter mise run real-smoke
   ```

   This reads `OPENROUTER_API_KEY` from local `.env` and writes ignored artifacts under
   `.ykm/real-index`.
4. Run the private real-corpus eval if `.ykm/private-eval/ykmcorpus.json` exists:

   ```bash
   YKM_EMBEDDING_PROVIDER=openrouter uv run ykm eval --index .ykm/real-index --cases .ykm/private-eval/ykmcorpus.json
   ```

5. Improve remaining `oversized-parent` / `headerless-or-single-section` warnings by corpus
   structure first. See `docs/ykm-corpus-authoring.md`.
6. Add more private eval cases as real usage appears. Do not paste sensitive corpus content into
   commits or docs; summarize by aggregate results and source IDs/paths.
7. `mise run local-mcp-smoke` completes Phase 1B local serving hardening. Next phase is container
   packaging; see `docs/ykm-phased-plan.md`.
