# YKM Phased Plan

This plan starts from the current validated Phase 1 local RAG state and keeps deferred work explicit.

## Phase 1A: Local RAG Core

Status: done.

- Markdown ingest with frontmatter defaults.
- Structural chunking and parent-section retrieval.
- Stable IDs and deterministic `retrieve`.
- Secret quarantine.
- LanceDB-backed artifact and manifest.
- Fake embeddings for offline tests.
- OpenRouter `openai/text-embedding-3-small` for real retrieval.
- Synthetic and real-corpus evals.

Current evidence does not justify a reranker.

## Phase 1B: Local Serving Hardening

Status: current phase.

Done:

- Local server runs against `.ykm/real-index`.
- `/livez` exposes process liveness only.
- Local MCP path requires shared-secret auth.
- Public MCP path fails closed without Cloudflare Access JWT.
- Authenticated local MCP smoke verified `health`, `query`, and `retrieve`.
- Query logs record returned source IDs but not raw query text or returned content.

Remaining to finish:

- Add a repeatable one-command local MCP smoke task.
- Keep the smoke output aggregate and source-pointer-only.

## Phase 1C: Container Packaging

Goal: run the validated local serving path in a container without changing product scope.

- Add Dockerfile and local compose/dev run path.
- Mount or copy an existing official index artifact.
- Ensure the serve container has no repo-write credential.
- Container healthcheck uses `/livez`.
- Run container locally against `.ykm/real-index`.
- Keep build provenance light: manifest-backed, no signing yet.

## Phase 1D: Remote Path Reimplementation

Goal: reimplement the proven Phase 0 Cloudflare path in production code outside `POC/`.

- Use `POC/` only as reference.
- Configure Cloudflare Tunnel and Access for production YKM.
- Validate signed Cloudflare Access JWT using team JWKS, issuer, audience, expiry, and owner email.
- Verify remote MCP with ChatGPT and Claude.
- Keep local/Hermes auth separate from public Cloudflare auth.

## Phase 1E: VPS Deployment

Goal: run the containerized read-only YKM service on the VPS.

- Deploy the service container.
- Load only the official/local pipeline artifact.
- Persist protected query logs.
- Document rebuild, restart, and smoke-test runbook.
- Confirm no source repo write credential is present in the serve container.

## Phase 2: Retrieval Quality And Corpus Loop

Goal: improve retrieval using eval and usage evidence.

- Add real usage-derived private eval cases.
- Improve corpus frontmatter/headings where evals show misses.
- Tune payload breadth and source diversity if evidence supports it.
- Consider reranking only if correct sources frequently appear in top 5 but not top 1/top 3 after
  corpus structure and filters are healthy.

## Phase 3: Write Paths

Goal: add controlled write inputs while preserving the spine rule.

- `upload`: markdown in, sanitize, open PR.
- `feedback`: inert observation log for future Curator use.
- Human reviews and merges all source changes.
- Build provenance and trust rigor become more important here because input is less controlled.

## Phase 4: Curator

Goal: external agent proposes improvements; YKM remains passive.

- Separate actor with no merge rights.
- Reads protected usage/feedback logs.
- Proposes corpus PRs.
- Maintains identity/entity refinements over time.

## Later: Non-Text Serving

Deferred until evidence justifies it.

- Blob/PDF indexing and serving.
- Image reference and retrieval behavior.
- Possible multimodal embeddings.
