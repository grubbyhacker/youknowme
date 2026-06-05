# YouKnowMe VPS Deployment Runbook

This runbook records the Phase 1E production deployment on `hermes-vps`.

## Current State

As of the Phase 1E cutover:

- Existing public MCP URL: `https://mcp.fleiglabs.cc/mcp`.
- Existing `cloudflared` container remains running: `roger-knowledge-cloudflared-phase0`.
- Production origin container: `youknowme-phase1e`.
- Production image tag on the VPS: `youknowme:phase1e`.
- Production private Docker network: `roger-knowledge-private`.
- Production network aliases: `roger-knowledge-mcp`, `youknowme`.
- Production runtime directory: `/opt/youknowme`.
- Production index mount: `/opt/youknowme/index:/data/index:ro`.
- Production log mount: `/opt/youknowme/logs:/data/logs`.
- Production env file: `/opt/youknowme/runtime.env`, mode `0600`.
- POC origin container is stopped and kept for rollback: `roger-knowledge-mcp-phase0`.

The tunnel was not recreated and `cloudflared` was not restarted during cutover. The cutover moved
the existing tunnel's Docker-network origin alias from the POC container to the production container.

## Rebuild And Redeploy

From the repository root on the development machine:

```bash
docker build --platform linux/amd64 -t youknowme:phase1e .
docker save youknowme:phase1e | ssh hermes-vps 'docker load'
COPYFILE_DISABLE=1 tar -C .ykm/real-index -cf - . | ssh hermes-vps 'mkdir -p /opt/youknowme/index && tar -C /opt/youknowme/index -xf - && chmod -R a+rX /opt/youknowme/index'
```

The Dockerfile uses a builder stage so dependency installation is cached separately from application
source installation. The runtime image contains the finished virtualenv but not `uv`, the source
tree, or uv's build cache. The Phase 1E minimized image measured about 194 MB on `hermes-vps`.

When deploying an index built from an uncommitted corpus working tree, `source_commit` is marked as:

```text
<git-sha>+dirty.<markdown-content-digest>
```

That is expected for owner-approved local corpus changes that have not been committed yet.

The production runtime env file is intentionally not committed. It should contain:

```text
YKM_AUTH_MODE=public
YKM_INDEX_PATH=/data/index
YKM_LOG_PATH=/data/logs/query-log.jsonl
YKM_LOG_RETENTION_DAYS=90
YKM_EMBEDDING_PROVIDER=openrouter
YKM_EMBEDDING_MODEL=openai/text-embedding-3-small
YKM_EMBEDDING_DIMENSIONS=1536
YKM_OWNER_EMAIL=<owner email>
YKM_CLOUDFLARE_TEAM_DOMAIN=<existing Access team domain>
YKM_CLOUDFLARE_AUD=<existing Access application audience>
YKM_CLOUDFLARE_TRUST_EDGE_AUTH=true
OPENROUTER_API_KEY=<runtime key>
```

Restart production:

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

## Smoke Checks

Private origin liveness:

```bash
ssh hermes-vps 'docker run --rm --network roger-knowledge-private curlimages/curl:latest -fsS http://roger-knowledge-mcp:8765/livez'
```

Expected:

```json
{"status":"ok","service":"YouKnowMe"}
```

Private origin fail-closed check:

```bash
ssh hermes-vps 'docker run --rm --network roger-knowledge-private curlimages/curl:latest -sS -i -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/list\",\"params\":{}}" http://roger-knowledge-mcp:8765/mcp | sed -n "1,10p"'
```

Expected origin response without the Access JWT when strict JWT enforcement is active:

```text
HTTP/1.1 403 Forbidden
{"detail":"forbidden","reason":"missing Cloudflare Access JWT"}
```

The current `hermes-vps` deployment uses `YKM_CLOUDFLARE_TRUST_EDGE_AUTH=true` because the existing
Cloudflare AI Controls flow authenticates at the Access edge but does not forward
`Cf-Access-Jwt-Assertion` to the origin.

Public unauthenticated front-door check:

```bash
curl -sS -I https://mcp.fleiglabs.cc/mcp | sed -n '1,16p'
```

Expected without an Access session:

```text
HTTP/2 401
server: cloudflare
```

Authenticated remote MCP verification still requires a fresh Cloudflare Access session from each
remote client. Verify with ChatGPT and Claude after reauthentication.

Production exposes the native Phase 1 tools:

```text
query
retrieve
health
```

It also exposes compatibility aliases for the existing Phase 0 ChatGPT registration:

```text
search
fetch
```

`search` maps to `query` and returns result pointers shaped like the Phase 0 search contract.
`fetch` maps to `retrieve` by `source_id`.

## Logs

Container logs:

```bash
ssh hermes-vps 'docker logs --tail 100 youknowme-phase1e'
```

Protected query log:

```bash
ssh hermes-vps 'ls -l /opt/youknowme/logs && tail -n 20 /opt/youknowme/logs/query-log.jsonl'
```

Query logs should contain source IDs, result counts, latency, build ID, and errors. They must not
contain raw query text or returned content.

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

The expected POC response includes:

```json
{"status":"ok","service":"roger-knowledge-mcp","transport":"streamable-http","mcp_path":"/mcp"}
```

After rollback, keep `youknowme-phase1e` stopped until the production issue is fixed.
