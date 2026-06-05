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

- Build ID: `4f07762808ea41e981ff2437fdf0bcb1`.
- Chunk count: `312`.
- Embedding model: `openai/text-embedding-3-small`.
- Source commit: `07c85a4113a11b98f2a27200b5822a8e2539b8ce+dirty.b09de997c23ce964`.
- The `+dirty` suffix is expected because the deployed corpus includes the owner's uncommitted
  `~/src/ykmcorpus/homemaint/san_jose_house_thermostat.md`.

## Code Surface

- `src/ykm/build.py` loads markdown, parses frontmatter including simple block lists, performs
  structural chunking, quarantines high-confidence secrets, embeds chunks, writes LanceDB artifacts,
  and marks dirty Git corpus builds with a markdown content digest.
- `src/ykm/index.py` loads the artifact and supports semantic `query`, deterministic `retrieve`, and
  health provenance.
- `src/ykm/server.py` exposes native FastMCP tools `query`, `retrieve`, and `health`.
- `src/ykm/server.py` also exposes `search` and `fetch` compatibility aliases for the existing
  Phase 0 ChatGPT tool registration; both are backed by the Phase 1 index.
- `src/ykm/auth.py` separates local shared-secret auth from public Cloudflare auth. Strict public
  mode still validates a forwarded `Cf-Access-Jwt-Assertion`; the live AI Controls route currently
  uses `YKM_CLOUDFLARE_TRUST_EDGE_AUTH=true` because Cloudflare authenticates at the edge but does
  not forward that JWT to the origin.
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
- `mise run test`: `33` tests passing.
- `YKM_EMBEDDING_PROVIDER=openrouter mise run real-smoke`: rebuilt `.ykm/real-index` from
  `~/src/ykmcorpus` with 312 chunks, 0 quarantines, and 14 warnings.
- `YKM_EMBEDDING_PROVIDER=openrouter mise run container-smoke`: passed against the rebuilt real
  index and listed `fetch`, `health`, `query`, `retrieve`, and `search`.
- VPS `/livez`: healthy.
- VPS MCP `health`: reports build ID `4f07762808ea41e981ff2437fdf0bcb1`.
- VPS MCP `search` for thermostat content returns
  `thermostat-bryant-ksacn1401aaa-heat-pump`.
- ChatGPT and Claude have both verified that new production data is serving through the live remote
  MCP route.
- Query/search logs remain source-pointer-only. They include event, latency, build ID, result count,
  result source IDs, and errors, not raw query text or returned content.
- Minimized image size on the VPS measured about `194 MB`; previous image was about `354 MB`.
- Recent measured image transfer to `hermes-vps` was about `10.5s`.

## Important Lessons

- The live Cloudflare AI Controls path does not currently forward `Cf-Access-Jwt-Assertion` to the
  origin. `YKM_CLOUDFLARE_TRUST_EDGE_AUTH=true` is a compatibility fallback, not true defense in
  depth. If Cloudflare can be configured to forward the Access JWT on all calls, turn this fallback
  off and restore strict owner-email verification for missing-JWT requests.
- Do not trust unverified email headers. Service-side owner email authorization is valid only when a
  signed Access JWT is present and verified.
- The Phase 0 ChatGPT registration still expects `search` and `fetch`; keep those compatibility
  aliases until the remote tool registry is updated to native `query` and `retrieve`.
- Tests must stay offline by default. Fake deterministic embeddings are the default for unit tests
  and demos; OpenRouter is optional runtime configuration.
- Container serving should load an existing artifact, not rebuild inside the serve container. Keep
  the index mount read-only and write only protected logs.
- The private corpus repo exists at `git@github.com:grubbyhacker/ykmcorpus.git` with a local clone at
  `~/src/ykmcorpus`. Treat it as sensitive input. Do not copy corpus content into this service repo.
- The currently deployed corpus includes an uncommitted owner-approved thermostat markdown file. If
  the corpus is later committed, rebuild the index so `source_commit` returns to a plain Git SHA.
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

Proceed to Phase 2: retrieval quality and corpus loop.

Good next slices:

- Add usage-derived private eval cases from protected logs and observed ChatGPT/Claude behavior.
- If Cloudflare has a setting to forward `Cf-Access-Jwt-Assertion` for all AI Controls calls, enable
  it, set `YKM_CLOUDFLARE_TRUST_EDGE_AUTH=false`, redeploy, and verify owner-email auth.
- Commit the thermostat file in `ykmcorpus` when ready, rebuild the index, and redeploy so provenance
  returns to a clean Git SHA.
