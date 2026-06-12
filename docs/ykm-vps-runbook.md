# YouKnowMe VPS Deployment Runbook

This runbook records the current production deployment on `hermes-vps`.

## Current State

- Public MCP URL: `https://mcp.fleiglabs.cc/mcp`.
- Production Compose project: `/docker/youknowme`.
- Production Compose is generated from the `vps-ops` Ansible role; this repository no longer carries
  a manual production Compose artifact.
- Production image tag on the VPS: `youknowme:phase1e`.
- Production origin container: `youknowme-phase1e`.
- Tunnel container managed by the same Compose project: `roger-knowledge-cloudflared-phase0`.
- Production private Docker network: `roger-knowledge-private`.
- Production network aliases for the origin container: `roger-knowledge-mcp`, `youknowme`.
- Production runtime env file: `/docker/youknowme/runtime.env`, mode `0600`.
- Tunnel token env file: `/docker/youknowme/.env`, mode `0600`, containing
  `CLOUDFLARED_TUNNEL_TOKEN`.
- Production data root: `/docker/youknowme/data`.
- Production index mount: `/docker/youknowme/data/index-current:/data/index:ro`.
- Production log mount: `/docker/youknowme/data/logs:/data/logs`.
- Production intake mount: `/docker/youknowme/data/intake:/data/intake`.
- GitHub App watcher key: `/docker/youknowme/secrets/ykmcorpus-build-watcher.private-key.pem`.

Target tree:

```text
/docker/youknowme/
  docker-compose.yml
  .env
  runtime.env
  secrets/
  data/
    incoming/
    index-builds/
    index-current -> index-builds/<active-build>
    index-previous -> index-builds/<previous-build>
    intake/
    logs/
    watcher-state/
```

## Rebuild And Redeploy

From the repository root on the development machine:

```bash
docker build --platform linux/amd64 -t youknowme:phase1e .
docker save youknowme:phase1e | ssh hermes-vps 'docker load'
ssh hermes-vps '
cd /docker/youknowme
docker compose config >/tmp/youknowme-compose-rendered.yaml
docker compose up -d --force-recreate
docker compose ps
'
```

The Compose project manages both `youknowme` and `cloudflared`. Do not start another tunnel
container with the same token outside this Compose project.

## Promote A Corpus Index Artifact

Production index artifacts are ZIPs containing exactly one tarball, checksum, and build report:

```text
youknowme-index-<source_commit>-<build_id>.tar.gz
youknowme-index-<source_commit>-<build_id>.sha256
youknowme-index-<source_commit>-<build_id>.build-report.json
```

Copy a downloaded artifact ZIP to the VPS:

```bash
scp youknowme-index-<run>.zip hermes-vps:/docker/youknowme/data/incoming/
```

Promote it manually from this repository checkout on the development machine:

```bash
ssh hermes-vps '
cd /path/to/youknowme-checkout
sudo scripts/relaunch-container-with-new-index.sh \
  --artifact /docker/youknowme/data/incoming/youknowme-index-<run>.zip \
  --compose-dir /docker/youknowme
'
```

The utility:

- verifies the ZIP shape and tarball checksum;
- validates the unpacked index with the production image;
- installs the build under `/docker/youknowme/data/index-builds/`;
- updates `index-current` and `index-previous` as relative symlinks;
- recreates the Compose-managed `youknowme` service;
- verifies `/livez` and fail-closed unauthenticated `/mcp`.

For local E2E verification of the same promotion path:

```bash
mise run index-promotion-smoke
```

## Download The Latest Official Corpus Artifact

The downloader checks `grubbyhacker/ykmcorpus` for the newest successful `main` Actions artifact and
compares it with `/docker/youknowme/data/index-current/manifest.json`.

Required GitHub App permissions:

- `Metadata`: mandatory.
- `Actions`: read.

Store the private key outside the repository:

```bash
sudo install -d -m 0700 /docker/youknowme/secrets
sudo install -m 0600 ykmcorpus-build-watcher.private-key.pem \
  /docker/youknowme/secrets/ykmcorpus-build-watcher.private-key.pem
```

From a YKM checkout, download without promotion:

```bash
export YKM_GITHUB_APP_ID=4001682
export YKM_GITHUB_INSTALLATION_ID=138954168
export YKM_GITHUB_PRIVATE_KEY_PATH=/docker/youknowme/secrets/ykmcorpus-build-watcher.private-key.pem

mise run download-latest-corpus-index
```

The former root cron watcher has been removed. Reintroduce scheduled promotion only through a new
explicit systemd unit or another owner-approved operator path.

## Runtime Env

`/docker/youknowme/runtime.env` is intentionally not committed. It should contain:

```text
YKM_AUTH_MODE=public
YKM_INDEX_PATH=/data/index
YKM_LOG_PATH=/data/logs/query-log.jsonl
YKM_LOG_RETENTION_DAYS=90
YKM_INTAKE_PATH=/data/intake
YKM_EMBEDDING_PROVIDER=openrouter
YKM_EMBEDDING_MODEL=openai/text-embedding-3-small
YKM_EMBEDDING_DIMENSIONS=1536
YKM_OWNER_EMAIL=<owner email>
YKM_CLOUDFLARE_TEAM_DOMAIN=<existing Access team domain>
YKM_CLOUDFLARE_AUD=<existing Access application audience>
YKM_ALLOWED_SERVICE_COMMON_NAMES=<Hermes Cloudflare Access service token Client ID>
YKM_MCP_RESOURCE_URL=https://mcp.fleiglabs.cc/mcp
OPENROUTER_API_KEY=<runtime key>
```

Hermes secrets live in `/docker/hermes-agent-6aso/.env`. Hermes config should reference
`${YKM_CF_ACCESS_CLIENT_ID}` and `${YKM_CF_ACCESS_CLIENT_SECRET}` placeholders rather than literal
secret values.

## Smoke Checks

Private origin liveness:

```bash
ssh hermes-vps 'docker run --rm --network roger-knowledge-private curlimages/curl:latest -fsS http://roger-knowledge-mcp:8765/livez'
```

Private origin fail-closed check:

```bash
ssh hermes-vps 'docker run --rm --network roger-knowledge-private curlimages/curl:latest -sS -i -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/list\",\"params\":{}}" http://roger-knowledge-mcp:8765/mcp | sed -n "1,10p"'
```

Expected origin response without an Access token:

```text
HTTP/1.1 401 Unauthorized
{"detail":"unauthorized"}
```

Public unauthenticated front-door check:

```bash
curl -sS -I https://mcp.fleiglabs.cc/mcp | sed -n '1,16p'
```

Expected without an Access session:

```text
HTTP/2 401
server: cloudflare
```

Protected query log:

```bash
ssh hermes-vps 'ls -l /docker/youknowme/data/logs && tail -n 20 /docker/youknowme/data/logs/query-log.jsonl'
```

Protected intake:

```bash
ssh hermes-vps 'find /docker/youknowme/data/intake -maxdepth 4 -type f | sort | tail -n 20'
```

Query logs must contain source IDs, result counts, latency, build ID, and errors. They must not
contain raw query text or returned content. Intake may contain staged upload markdown and agent
feedback, so treat it as sensitive input.
