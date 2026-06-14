# Agent Handoff

## Current State

YouKnowMe is live in production on `hermes-vps` behind the existing Cloudflare Tunnel / Access route:

```text
https://mcp.fleiglabs.cc/mcp
-> roger-knowledge-cloudflared-phase0
-> youknowme-mcp
```

The tunnel container and origin container are managed by the `/docker/youknowme` Compose project.
Do not start another `cloudflared` with the existing tunnel token.

Production container state:

- `youknowme-mcp` is the live origin container.
- Production management surface: `/docker/youknowme/docker-compose.yml`.
- The production Compose file is generated from the `vps-ops` Ansible role; this repository no
  longer carries a manual production Compose artifact.
- Runtime env on the VPS: `/docker/youknowme/runtime.env` with mode `0600`; do not print or commit it.
- Tunnel token env on the VPS: `/docker/youknowme/.env` with mode `0600`; do not print or commit it.
- Data root on the VPS: `/docker/youknowme/data`.
- Index mount: `/docker/youknowme/data/index-current:/data/index:ro`.
- Protected log mount: `/docker/youknowme/data/logs:/data/logs`.
- Protected intake mount: `/docker/youknowme/data/intake:/data/intake`.

Corpus index releases are installed by `scripts/install-corpus-index.sh` after CI pushes the
artifact tarball and checksum to the VPS. The script verifies the checksum, extracts the index into
`/docker/youknowme/data/index-builds/<source_commit>-<build_id>`, atomically repoints
`/docker/youknowme/data/index-current`, recreates the `youknowme` Compose service, waits for the
`youknowme-mcp` container to become healthy, and prunes older index builds beyond the newest three.

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

Production deploys run through the GitHub Actions production deployment workflow after CI passes on
`main`. Do not push images or restart production directly from a development machine.

## Verification Completed

- `mise run lint`: Ruff passing.
- `mise run test`: `289` tests passing as of the Curator feedback reporter/excerpt follow-up.
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
  `/docker/youknowme/runtime.env` as `YKM_ALLOWED_SERVICE_COMMON_NAMES`.
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

Curator upload intake is agentic and enabled on the hourly timer. Feedback handling is also live
behind an operator-only launch profile.

Deployment details:

- Current deployed feedback Curator image on the VPS:
  `youknowme:curator-feedback-20260611-a9ff7c7`.
- Upload/timer templates still use the earlier upload image:
  `youknowme:curator-agentic-upload-20260611-1cc9b97`.
- Merged code PRs:
  - `grubbyhacker/youknowme#20`: agentic Codex upload-review executor.
  - `grubbyhacker/youknowme#21`: upload mutation-budget cap and report cleanup.
  - `grubbyhacker/youknowme#24`: simplified feedback processing.
  - `grubbyhacker/youknowme#25`: fixture result handling for simulated feedback executions.
  - `grubbyhacker/youknowme#30`: reporter MCP issue creation and bounded feedback excerpts in
    private corpus PR/issue bodies.
- sandbox-broker config: `/docker/gh-agent-broker/configs/sandbox-beta.yaml`.
- Recent config/service backups:
  - `configs/sandbox-beta.yaml.bak-agentic-upload-20260611T062504Z`
  - `configs/sandbox-beta.yaml.bak-agentic-upload-pr21-20260611T063819Z`
  - `configs/sandbox-beta.yaml.bak-enable-live-upload-timer-20260611T064759Z`
  - `/etc/systemd/system/ykm-curator-launch.service.bak-live-upload-timer-20260611T064812Z`
- Curator upload/repair profiles still use `ykm-curator-dry-run-model`; feedback uses the separate
  `ykm-curator-feedback-model` template.
- Manual live upload profile `ykm-curator-upload-pr-live` remains available to
  `ykm-curator-operator` and accepts required `parameters.upload_ids`.
- Manual live feedback profile `ykm-curator-feedback-live` is available to `ykm-curator-operator`.
  It uses template `ykm-curator-feedback-model`, image `youknowme:curator-feedback-20260611-a9ff7c7`,
  `YKM_REPORTER_MCP_URL=http://issue-reporter:8090/mcp`, `feedback_executor: "codex_proxy"`, and a
  two-object feedback mutation budget.
- Timer profile `ykm-curator-upload-pr-timer` is live and unscoped. It runs:
  - mode: `manual_live`
  - enabled actions: `["plan_uploads"]`
  - executor: `upload_review_executor: "codex_proxy"`
  - model: `ykm-codex-gpt-5-mini`
  - attempts: `upload_review_max_attempts: 2`
  - validation: `["mise", "run", "validate"]`
  - GitHub mutation budget: `max_new_objects_per_run=1`, `upload=1`, `feedback=0`
- `ykm-curator-timer` is allowed to launch
  `ykm-curator-dry-run`, `ykm-curator-state-only`, and `ykm-curator-upload-pr-timer`.
- `ykm-curator-launch.service` now launches
  `/v1/launch-profiles/ykm-curator-upload-pr-timer/launch`.
- `ykm-curator-launch.timer` is enabled and active.
- The timer token was rotated on June 11, 2026 after it was exposed in terminal output during
  service inspection; do not print timer/operator env files.

Live feedback proof:

- Broker issue `grubbyhacker/gh-agent-broker#41` tracked the reporter MCP installation/config
  mismatch. It is fixed and was verified from Curator.
- Run `20260611T082510Z-6a3f130502890fc2` used the pre-merge feedback image and created
  `grubbyhacker/ykmcorpus#12` via broker PR creation and `grubbyhacker/youknowme#29` via reporter
  MCP issue creation. The run's overall status was `fail` only because unresolved feedback remained
  beyond the two-mutation budget, so the checkpoint correctly did not advance.
- PR `grubbyhacker/ykmcorpus#11` was updated in place to include the original feedback excerpt. PR
  #30 now makes future private corpus-targeted Curator PR/issue bodies include bounded feedback
  excerpts; public product issues remain marker-only.
- Latest production feedback run after deploying merged commit `a9ff7c7`:
  `20260611T185959Z-fc0addf29a750f5a`.
  - Image: `youknowme:curator-feedback-20260611-a9ff7c7`.
  - Status: `fail` because unresolved feedback remains after the mutation budget.
  - GitHub mutations: `2`.
  - Feedback decisions appended: `2`.
  - Created `grubbyhacker/youknowme#31` for `fb_20260606_213443_4d6413ec`.
  - Created `grubbyhacker/youknowme#32` for `fb_20260606_213447_05359cd6`.
  - Remaining unresolved feedback IDs are still queued for future runs.
- Operational caveat: after broker/reporter restarts, confirm `issue-reporter` resolves from
  `gh-agent-broker_default` because the feedback worker uses
  `http://issue-reporter:8090/mcp`. A missing network alias breaks reporter MCP issue filing.

Feedback architecture lesson:

- The current feedback queue works at low QPS, but it is acting like a small issue tracker. A likely
  future simplification is to have feedback intake auto-file private `grubbyhacker/ykmcorpus`
  issues, then let humans or maintenance agents decide whether a corpus PR, corpus issue, or public
  YKM issue is appropriate.
- Until that is redesigned, keep the live feedback profile operator-triggered and budgeted.

Agentic upload and repair proof:

- Upload `upl_20260611_053713_9d1639ef` was processed by live run
  `20260611T062556Z-37bb16ff3829516f`.
- Run result: `pass`, `manual_live`, `1` GitHub mutation, `0` model calls,
  `0` validation failures.
- Curator opened `grubbyhacker/ykmcorpus#10` for `final/the-narrow-pipe-summary.md`.
- Owner left inline review comments only, without labels. Repair run
  `20260611T063859Z-8891fe53f993ef1d` still classified PR #10 as
  `commented_needs_triage`.
- Curator repaired PR #10 by moving the document to `writing/the-narrow-pipe-summary.md`, changing
  the corpus policy root from `final` to `writing`, validating, pushing commit
  `b492bee7e1419b2104d0cea2f1f8fa13bce9275d`, resolving the two review threads, adding
  `ym-curator: waiting-review`, and posting the repair handoff comment.
- `grubbyhacker/ykmcorpus#10` is now merged. The corpus index has not yet been rebuilt/deployed from
  that merge in this handoff.

Latest live timer smoke:

- Run: `20260611T064818Z-84a12a675b996a89`.
- Status: `pass`.
- Mode: `manual_live`.
- Upload previews: `0`.
- GitHub mutations: `0`.
- Model calls: `0`.
- Validation failures: `0`.

Known Curator follow-up:

- `grubbyhacker/youknowme#22`: Curator repair should refresh upload PR title/body metadata after a
  file move. In PR #10, repair moved the file to `writing/`, but the PR title still referenced the
  old `final/` path.

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
docker stop youknowme-mcp >/dev/null 2>&1 || true
docker network disconnect roger-knowledge-private youknowme-mcp >/dev/null 2>&1 || true
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
  - Rebuild and deploy the production corpus index after the `ykmcorpus#10` merge, preserving the
    existing index symlink/rollback shape and the timerd index-promotion task.
  - Watch the next few hourly `ykm-curator-upload-pr-timer` runs for duplicate PRs, queue-state
    drift, validation failures, and whether one upload per run is enough.
  - Fix `grubbyhacker/youknowme#22` so Curator repair refreshes upload PR title/body metadata after
    file moves.
  - Decide whether PR repair should also be timer-enabled, or remain manual/operator-triggered until
    a few more owner-review loops are observed.
  - Continue collecting usage-derived private eval cases as maintenance.
- If direct Cloudflare Access works for a generic MCP client but ChatGPT or Claude does not, stop and
  record the exact compatibility failure. Do not fall back to Cloudflare MCP Portal with an
  unauthenticated upstream.
