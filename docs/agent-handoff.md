# Agent Handoff

## Current State

YouKnowMe is live in production on `hermes-vps` behind the existing Cloudflare Tunnel / Access route:

```text
https://mcp.fleiglabs.cc/mcp
-> roger-knowledge-cloudflared-phase0
-> youknowme-phase1e
```

`POC/` remains reference-only and is still the rollback target. Do not create new production content
inside `POC/`, and do not start another `cloudflared` with the existing tunnel token.

Production container state:

- `youknowme-phase1e` is the live origin container.
- `roger-knowledge-cloudflared-phase0` remains the existing tunnel container and was not replaced.
- `roger-knowledge-mcp-phase0` is stopped and retained for rollback.
- Runtime directory on the VPS: `/opt/youknowme`.
- Runtime env on the VPS: `/opt/youknowme/runtime.env` with mode `0600`; do not print or commit it.
- Index mount: `/opt/youknowme/index:/data/index:ro`.
- Protected log mount: `/opt/youknowme/logs:/data/logs`.

Current deployed index:

- Build ID: `f1fa6a81d97e4650b5775f717ad8c5dd`.
- Chunk count: `339`.
- Embedding model: `openai/text-embedding-3-small`.
- Source commit: `65607eb0e5d152506d76fb74205f7eed108655f2`.

## Code Surface

- `src/ykm/build.py` loads markdown, parses frontmatter including simple block lists, performs
  structural chunking, quarantines high-confidence secrets, embeds chunks, writes LanceDB artifacts,
  and marks dirty Git corpus builds with a markdown content digest.
- `src/ykm/index.py` loads the artifact and supports semantic `query`, deterministic `retrieve`, and
  health provenance.
- `src/ykm/server.py` exposes native FastMCP tools `query`, `retrieve`, and `health`.
- `src/ykm/server.py` also exposes `search` and `fetch` compatibility aliases for the existing
  Phase 0 ChatGPT tool registration; both are backed by the Phase 1 index.
- `src/ykm/auth.py` separates local shared-secret auth from public Cloudflare auth. Public mode
  validates Cloudflare Access tokens from `Cf-Access-Jwt-Assertion` or `Authorization: Bearer` and
  authorizes either the configured owner email or a verified Cloudflare Access service-token
  `common_name` listed in `YKM_ALLOWED_SERVICE_COMMON_NAMES`.
- `src/ykm/logging.py` writes protected JSONL query logs that record returned source IDs, not query
  text or returned content.
- `Dockerfile` is minimized with a builder stage. Dependency installation is cached before app source
  copy; runtime contains the installed virtualenv but not `uv`, source tree, or uv cache.

## Useful Commands

```bash
mise run test
mise run lint
mise run eval
YKM_EMBEDDING_PROVIDER=openrouter mise run real-smoke
YKM_EMBEDDING_PROVIDER=openrouter mise run local-mcp-smoke
YKM_EMBEDDING_PROVIDER=openrouter mise run container-smoke
```

Build and deploy the production image from the development machine:

```bash
docker build --platform linux/amd64 -t youknowme:phase1e .
docker save youknowme:phase1e | ssh hermes-vps 'docker load'
COPYFILE_DISABLE=1 tar -C .ykm/real-index -cf - . | ssh hermes-vps 'mkdir -p /opt/youknowme/index && tar -C /opt/youknowme/index -xf - && chmod -R a+rX /opt/youknowme/index && find /opt/youknowme/index -name "._*" -delete'
```

Restart production on the VPS:

```bash
ssh hermes-vps '
docker rm -f youknowme-phase1e >/dev/null 2>&1 || true
docker run -d \
  --name youknowme-phase1e \
  --restart unless-stopped \
  --network roger-knowledge-private \
  --network-alias roger-knowledge-mcp \
  --network-alias youknowme \
  --env-file /opt/youknowme/runtime.env \
  -v /opt/youknowme/index:/data/index:ro \
  -v /opt/youknowme/logs:/data/logs \
  --read-only \
  --tmpfs /tmp \
  --cap-drop ALL \
  --security-opt no-new-privileges:true \
  youknowme:phase1e
'
```

## Verification Completed

- `mise run lint`: Ruff passing.
- `mise run test`: `244` tests passing as of the Curator upload PR title-context follow-up.
- `YKM_EMBEDDING_PROVIDER=openrouter mise run real-smoke`: rebuilt `.ykm/real-index` from
  `~/src/ykmcorpus`.
- `YKM_EMBEDDING_PROVIDER=openrouter mise run container-smoke`: passed against the rebuilt real
  index and listed `fetch`, `health`, `query`, `retrieve`, and `search`.
- VPS `/livez`: healthy.
- VPS MCP `health`: reports build ID `f1fa6a81d97e4650b5775f717ad8c5dd`.
- VPS MCP `search` for thermostat content returns
  `thermostat-bryant-ksacn1401aaa-heat-pump`.
- ChatGPT and Claude have both verified that new production data is serving through the live remote
  MCP route.
- Hermes service-token MCP is enabled through the public Cloudflare Access route. The service-token
  JWT was observed with `email=None` and a `common_name`; that `common_name` is configured in
  `/opt/youknowme/runtime.env` as `YKM_ALLOWED_SERVICE_COMMON_NAMES`.
- Hermes Agent and Claude.ai were tested successfully after the service-token rollout.
- Red-team probes after rollout all failed closed: public no-auth `401`, public fake service-token
  headers `401`, private origin no-JWT `401`, spoofed email header `401`, local-secret header in
  public mode `401`, malformed bearer `401`, and malformed `Cf-Access-Jwt-Assertion` `401`.
- YKM logs show Hermes requests as `reason=cloudflare-service` and owner OAuth requests as
  `reason=cloudflare`.
- Query/search logs remain source-pointer-only. They include event, latency, build ID, result count,
  result source IDs, and errors, not raw query text or returned content.
- Minimized image size on the VPS measured about `194 MB`; previous image was about `354 MB`.
- Recent measured image transfer to `hermes-vps` was about `10.5s`.

## Curator Current State

The upload-review observe and live PR execution path was merged through
`grubbyhacker/youknowme#10` and deployed to `hermes-vps`.

Deployment details:

- Curator launcher image on the VPS: `youknowme:curator-upload-pr-live-20260610-34350f0`.
- The Curator image contains the app virtualenv plus `git`, `mise`, and `uv`; the corpus validator's
  `uv@0.11.18` and managed Python `3.12.13` are preinstalled so `mise run validate` can run without
  network access.
- sandbox-broker config: `/docker/gh-agent-broker/configs/sandbox-beta.yaml`.
- Config backups from launch: `sandbox-beta.yaml.bak-live-upload-20260610T072100Z` and
  `sandbox-beta.yaml.bak-live-upload-budget-20260610T072245Z`.
- Read-only corpus validation source on the VPS: `/opt/youknowme/ykmcorpus`, refreshed from
  `grubbyhacker/ykmcorpus` `main` at `c691e77`.
- Manual live profile: `ykm-curator-upload-pr-live`.
- The live profile uses the model-backed Curator template, mounts `/opt/youknowme/ykmcorpus` at
  `/data/ykmcorpus:ro`, and runs `curator run ... --corpus-checkout /data/ykmcorpus`.
- Operator principal `ykm-curator-operator` can launch `ykm-curator-upload-pr-live`.
- Timer principal `ykm-curator-timer` cannot launch the live profile; it remains scoped to
  `ykm-curator-dry-run` and `ykm-curator-state-only`.
- The hourly timer is enabled and active. Last verified scheduled dry-run smoke:
  `20260610T073539Z-04ad5e79ed5935aa`, status `pass`, mode `dry_run`, `0` GitHub mutations,
  `0` model calls, no validation failures, no partial failures.

Live upload PR launch:

- Successful run: `20260610T072256Z-723466d21e81712e`.
- Status: `pass`.
- Mode: `manual_live`.
- Model: `anthropic/claude-sonnet-4.6`.
- Model budget used: `3` calls, `16551` tokens; budget was `3` calls / `30000` tokens.
- GitHub mutation budget: `max_new_objects_per_run=2`, `upload=2`, `feedback=0`.
- Observations: `3` upload-review observations, `2` corpus validation passes, `1` skipped draft,
  `0` validation failures.
- GitHub mutations: `2`.
- Open corpus PRs created:
  - `grubbyhacker/ykmcorpus#5`: `preferences/dev-environment.md`.
  - `grubbyhacker/ykmcorpus#6`: `homemaint/santa-cruz-freeflow-excursion-owner-manual.md`.
- The skipped upload was `upl_20260606_043954_cdda43dd`; the model did not produce an integrated
  corpus draft for it, so no PR was created.

Launch lesson:

- The first live attempt, `20260610T072155Z-8ccbb52091c6ee86`, failed closed because there were
  `3` pending upload previews and the task had only `2` model calls budgeted. The profile now budgets
  `3` model calls while keeping GitHub upload mutations bounded to `2`.

Current restart branch:

- Branch: `curator/upload-pr-title-context`.
- Purpose: make future upload-review PR titles and descriptions show the destination corpus page at
  a glance.
- Code change: `src/curator/upload_pr.py` now titles future one-file upload PRs with the draft path,
  e.g. `YouKnowMe Curator upload review: preferences/dev-environment.md`, and adds a `Page:` or
  `Pages:` line near the top of the PR body.
- Existing PRs `grubbyhacker/ykmcorpus#5` and `grubbyhacker/ykmcorpus#6` were intentionally left as
  is.
- Verification on this branch: `mise run lint` passed and `mise run test` passed with `244` tests.

## Important Lessons

- The OAuth reset uses direct Cloudflare Access protection for `https://mcp.fleiglabs.cc/mcp`.
  Cloudflare MCP Portal is not part of this reset because it requires a public upstream MCP URL and
  does not protect that direct upstream for this Docker/VPS shape.
- Missing public `/mcp` tokens must return `401`. Invalid, expired, wrong-issuer, wrong-audience, or
  unverifiable tokens must return `401`. A valid token with the wrong owner email must return `403`.
- Do not trust unverified email headers. Service-side owner email authorization is valid only when a
  signed Access JWT is present and verified.
- Hermes uses the public `https://mcp.fleiglabs.cc/mcp` route with Cloudflare Access service-token
  headers. Its secrets live in `/docker/hermes-agent-6aso/.env`, and Hermes config should use
  `${YKM_CF_ACCESS_CLIENT_ID}` / `${YKM_CF_ACCESS_CLIENT_SECRET}` placeholders.
- Only allow Hermes at YouKnowMe's second auth layer when the verified Access JWT includes
  `common_name` equal to an explicitly configured service token Client ID. Do not authorize service
  tokens from `aud` plus missing `email` alone.
- The Phase 0 ChatGPT registration still expects `search` and `fetch`; keep those compatibility
  aliases until the remote tool registry is updated to native `query` and `retrieve`.
- Tests must stay offline by default. Fake deterministic embeddings are the default for unit tests
  and demos; OpenRouter is optional runtime configuration.
- Container serving should load an existing artifact, not rebuild inside the serve container. Keep
  the index mount read-only and write only protected logs.
- The private corpus repo exists at `git@github.com:grubbyhacker/ykmcorpus.git` with a local clone at
  `~/src/ykmcorpus`. Treat it as sensitive input. Do not copy corpus content into this service repo.
- Frontmatter improves stable IDs and filters; headings/body text improve semantic matching because
  chunk embeddings currently use chunk text, not metadata. Keep both in good shape.
- Do not reshape imported writing samples or Substack posts merely to satisfy indexing warnings.
- `.env` exists locally and contains runtime secrets. It is ignored by Git. Never commit it.
- Current eval evidence does not justify a reranker.

## Rollback

Rollback restores the existing tunnel origin alias to the POC container:

```bash
ssh hermes-vps '
docker stop youknowme-phase1e >/dev/null 2>&1 || true
docker network disconnect roger-knowledge-private youknowme-phase1e >/dev/null 2>&1 || true
docker network connect --alias roger-knowledge-mcp roger-knowledge-private roger-knowledge-mcp-phase0 >/dev/null 2>&1 || true
docker start roger-knowledge-mcp-phase0
'
```

Then verify:

```bash
ssh hermes-vps 'docker run --rm --network roger-knowledge-private curlimages/curl:latest -fsS http://roger-knowledge-mcp:8765/health'
```

## Next Work

Phase 2 is complete given the small amount of real usage so far.

Current Phase 2 evidence:

- The private baseline has 19 cases and passes 19/19 against build
  `f1fa6a81d97e4650b5775f717ad8c5dd`; 18 pass at top 1, and all pass at top 3/top 5.
- The one non-top-1 case intentionally expects both a thermostat manual artifact and an owner
  thermostat writeup.
- Current evidence does not justify a reranker or payload/ranking changes.
- Triggering/tool-adoption was improved by deploying owner-specific MCP tool descriptions.
- Treat future "agent did not call YouKnowMe" reports as triggering/tool-adoption issues, not
  retrieval-ranking issues. The example is "How much bromine should I put into my home hot tub?";
  general knowledge is misleading because the private corpus says the home hot tub is chlorine-based.

- Good next Curator slices:
  - Review and merge the two open `ykmcorpus` upload PRs if their content is acceptable.
  - After those PRs merge and a new corpus index artifact/build is available, deploy the updated
    corpus index to production and run the normal MCP health/search smoke.
  - Decide whether the skipped upload should remain owner-action-only or needs a better model prompt
    / intake-quality path.
  - Consider a follow-up that marks upload bundles claimed/processed only after PR creation and merge
    policy is settled; the current launch intentionally created PRs without moving queue state.
  - Continue keeping the live upload profile manual-only until enough runs justify automation.
- Keep collecting usage-derived private eval cases as maintenance.
- If direct Cloudflare Access works for a generic MCP client but ChatGPT or Claude does not, stop and
  record the exact compatibility failure. Do not fall back to Cloudflare MCP Portal with an
  unauthenticated upstream.
