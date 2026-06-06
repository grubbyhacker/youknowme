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

Status: done.

Completed:

- Local server runs against `.ykm/real-index`.
- `/livez` exposes process liveness only.
- Local MCP path requires shared-secret auth.
- Public MCP path fails closed without Cloudflare Access JWT.
- Authenticated local MCP smoke verified `health`, `query`, and `retrieve`.
- Query logs record returned source IDs but not raw query text or returned content.
- `mise run local-mcp-smoke` provides a repeatable one-command local MCP smoke.
- Smoke output stays aggregate and source-pointer-only.

## Phase 1C: Container Packaging

Status: done.

Goal: run the validated local serving path in a container without changing product scope.

Completed:

- Added `Dockerfile` and `compose.yaml` for the local container run path.
- Mounted the existing official index artifact at `/data/index` read-only.
- Kept corpus content, generated indexes, secrets, `.git/`, and `POC/` out of the image build
  context.
- Runs as an unprivileged `ykm` user with a read-only root filesystem in Compose.
- Container healthcheck uses `/livez`.
- `mise run container-smoke` builds/runs the container locally against `.ykm/real-index`, verifies
  local MCP auth, `health`, `query`, `retrieve`, and protected query-log shape.
- Build provenance remains manifest-backed; no signing yet.

## Phase 1D: Existing Cloudflare Path Discovery And Cutover Plan

Status: done.

Goal: make production YKM ready for the existing Cloudflare Tunnel / Access contract without
disrupting the running POC.

Important constraint:

- The POC is still actively serving from the VPS through the existing Cloudflare Tunnel / Access
  setup.
- The production system will reuse that existing Cloudflare configuration.
- Do not create another Cloudflare Tunnel for YKM.
- Do not start a second `cloudflared` with the existing tunnel token. Running the same tunnel token
  from a second place can disrupt or contend with the active POC route.

Work in this phase:

- Inspect `POC/` as reference only; do not modify or restart the running POC.
- Document the existing Cloudflare contract:
  - public hostname / route shape
  - origin path expected by the tunnel
  - Access application assumptions
  - `Cf-Access-Jwt-Assertion` behavior
  - team domain / issuer
  - audience tag required by YKM
  - owner email claim used for authorization
- Confirm production YKM `public` mode matches that contract:
  - validates signed Cloudflare Access JWT through JWKS
  - checks issuer, audience, expiry, and owner email
  - fails closed on missing/invalid/mismatched JWT
- Keep local shared-secret auth separate from public Cloudflare auth.
- Write the cutover plan:
  - how the existing tunnel origin moves from POC to production YKM on the VPS
  - how to verify remote MCP after cutover
  - how to roll back by restoring the POC origin

Remote live verification through ChatGPT/Claude likely happens during Phase 1E, because the existing
tunnel is currently attached to the running POC.

Completed:

- Documented the existing contract and cutover/rollback plan in `docs/ykm-cloudflare-cutover.md`.
- Confirmed public YouKnowMe serves the same `/mcp` path and validates Cloudflare Access JWTs through
  JWKS with issuer, audience, expiry, and owner-email checks.
- Added fail-closed tests for wrong audience, wrong issuer, expired tokens, and public-mode rejection
  of the local shared-secret header.

## Phase 1E: VPS Deployment

Status: done.

Goal: run the containerized read-only YKM service on the VPS and cut over the existing Cloudflare
route from the POC to production YKM.

- Deploy the service container.
- Load only the official/local pipeline artifact.
- Persist protected query logs.
- Document rebuild, restart, and smoke-test runbook.
- Confirm no source repo write credential is present in the serve container.
- Retarget the existing Cloudflare Tunnel origin from the POC service to the production YKM service.
- Verify remote MCP through the existing Cloudflare Access app with ChatGPT and Claude.
- Verify missing/wrong Access JWT still fails closed.
- Keep the POC available as rollback/reference until the production path is stable.

Completed so far:

- Built and loaded `youknowme:phase1e` for `linux/amd64` on `hermes-vps`.
- Deployed `.ykm/real-index` to `/opt/youknowme/index` read-only.
- Wrote root-only runtime env on the VPS using the existing POC Cloudflare Access app values.
- Started production `youknowme-phase1e` on the existing `roger-knowledge-private` network.
- Cut over the existing tunnel origin alias `roger-knowledge-mcp` from the stopped POC container to
  production YouKnowMe without restarting or duplicating `cloudflared`.
- Verified private `/livez` succeeds and private `/mcp` fails closed without
  `Cf-Access-Jwt-Assertion`.
- Verified public unauthenticated `https://mcp.fleiglabs.cc/mcp` receives Cloudflare `401`.
- Later OAuth reset direction: use direct Cloudflare Access protection for
  `https://mcp.fleiglabs.cc/mcp`; do not use Cloudflare MCP Portal or any unauthenticated public
  upstream URL.
- Added `search` and `fetch` compatibility aliases backed by the Phase 1 index so the existing
  Phase 0 ChatGPT tool registration can call production successfully while native `query`,
  `retrieve`, and `health` remain available.
- Optimized Docker build and transfer time: dependency installation is cached before source copy, the
  runtime image no longer includes `uv` or uv cache, and the VPS image measured about 194 MB.
- Rebuilt and deployed the production index from `~/src/ykmcorpus`.
- Current deployed build ID is `f1fa6a81d97e4650b5775f717ad8c5dd` with 339 chunks and source
  provenance `65607eb0e5d152506d76fb74205f7eed108655f2`.
- Verified ChatGPT and Claude can retrieve new production data through the live remote MCP route.
- Documented the rebuild, restart, smoke-test, logging, and rollback runbook in
  `docs/ykm-vps-runbook.md`.
- Added a deliberate Hermes service-token path for the public Cloudflare Access route:
  YouKnowMe accepts verified owner email tokens as `cloudflare` and accepts verified service-token
  JWTs as `cloudflare-service` only when JWT `common_name` is listed in
  `YKM_ALLOWED_SERVICE_COMMON_NAMES`.
- Deployed the Hermes service-token allowlist on `hermes-vps`; verified Hermes Agent and Claude.ai
  work, and verified public/private red-team probes still fail closed.

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
