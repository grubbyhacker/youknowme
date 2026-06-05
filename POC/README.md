# roger-knowledge-mcp

Phase 0 proof of concept for a private MCP server connected to ChatGPT through Cloudflare Tunnel and Cloudflare Access.

This is wiring validation only. It does not include RAG, embeddings, vector storage, Drive sync, GitHub workflows, writes, or OAuth inside the MCP server.

## Flow

```text
ChatGPT or MCP client
-> https://mcp.fleiglabs.cc/mcp
-> Cloudflare Access
-> Cloudflare Tunnel
-> cloudflared on hermes-vps
-> http://roger-knowledge-mcp:8765/mcp on a private Docker network
-> containerized roger-knowledge MCP server
```

The VPS deployment uses a private Docker network and does not publish the MCP server port to the host.

`cloudflared` must run only on `hermes-vps`, not on the development Mac. See [DEPLOYMENT.md](DEPLOYMENT.md).

## Local Development

Prerequisites on the laptop:

- `mise`
- `uv`
- Docker, for container validation

Start the Python environment and server:

```bash
cd roger-knowledge-mcp
mise trust
mise install
uv sync
./scripts/run-local.sh
```

In another shell:

```bash
curl http://127.0.0.1:8765/health
```

Expected response:

```json
{"status":"ok","service":"roger-knowledge-mcp","transport":"streamable-http","mcp_path":"/mcp"}
```

For Cloudflare AI Controls setup, origin-side Access JWT enforcement is disabled:

```text
REQUIRE_CLOUDFLARE_ACCESS_JWT=false
```

With that setting, `/mcp` is open to the Cloudflare portal upstream. Set this only if origin-side JWT validation is required:

```text
REQUIRE_CLOUDFLARE_ACCESS_JWT=true
```

## MCP Inspector

Run the current MCP Inspector and connect it to the Streamable HTTP endpoint:

```bash
npx @modelcontextprotocol/inspector
```

Use:

```text
Transport: Streamable HTTP
URL: http://127.0.0.1:8765/mcp
```

Verify the tools:

- `search`
- `fetch`
- `health`

Example `search` argument:

```json
{"query":"Hermes Phase 0"}
```

Example `fetch` argument:

```json
{"id":"phase0:hermes"}
```

## Cloudflare Setup

Create these Cloudflare resources before the final deployed test:

1. Cloudflare Tunnel for the VPS.
2. Public hostname route:
   - Hostname: `mcp.fleiglabs.cc`
   - Service target: `http://roger-knowledge-mcp:8765`
3. Cloudflare Access self-hosted application for `mcp.fleiglabs.cc`.
4. Access policy allowing the intended users or clients.
5. Access Managed OAuth enabled at the edge as appropriate for the ChatGPT/client flow.

Required runtime values:

- `CLOUDFLARE_TUNNEL_TOKEN`
- `CLOUDFLARE_ACCESS_TEAM_DOMAIN`, such as `https://your-team.cloudflareaccess.com`
- `CLOUDFLARE_ACCESS_AUD`, the Access application AUD tag from Additional settings
- `REQUIRE_CLOUDFLARE_ACCESS_JWT=false` for Cloudflare AI Controls setup

Create the ignored runtime env file:

```bash
cp .env.cloudflare.example .env.cloudflare
$EDITOR .env.cloudflare
```

When `REQUIRE_CLOUDFLARE_ACCESS_JWT=true`, the MCP origin validates the `Cf-Access-Jwt-Assertion` header on `/mcp` requests. It fetches JWKS from:

```text
<CLOUDFLARE_ACCESS_TEAM_DOMAIN>/cdn-cgi/access/certs
```

It verifies RS256 signature, issuer, and audience. When `REQUIRE_CLOUDFLARE_ACCESS_JWT=false`, `/mcp` is not checked by the origin. `/health` stays unprotected in both modes for private container health checks.

## Docker

Build and run the MCP server locally with a host port for development:

```bash
docker build -t roger-knowledge-mcp:phase0 .
docker run --rm --name roger-knowledge-mcp-phase0 -p 127.0.0.1:8765:8765 roger-knowledge-mcp:phase0
```

Health check:

```bash
curl http://127.0.0.1:8765/health
```

MCP reachability check:

```bash
curl -i \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' \
  http://127.0.0.1:8765/mcp
```

Or use Compose once `.env.cloudflare` exists:

```bash
docker compose --env-file .env.cloudflare up --build
```

Compose starts:

- `roger-knowledge-mcp-phase0`, listening on container port `8765` only
- `roger-knowledge-cloudflared-phase0`, using the official `cloudflare/cloudflared` image

## VPS Deployment

Do not install Python, `mise`, or `uv` on the VPS for this POC. Build locally, transfer the MCP image, and run it beside the official `cloudflared` image.

```bash
set -a
. ./.env.cloudflare
set +a

docker build --platform linux/amd64 -t roger-knowledge-mcp:phase0 .
docker save roger-knowledge-mcp:phase0 | ssh hermes-vps docker load
ssh hermes-vps 'docker network create roger-knowledge-private >/dev/null 2>&1 || true'
ssh hermes-vps 'docker rm -f roger-knowledge-mcp-phase0 >/dev/null 2>&1 || true'
ssh hermes-vps "docker run -d --name roger-knowledge-mcp-phase0 --restart unless-stopped --network roger-knowledge-private --network-alias roger-knowledge-mcp -e PUBLIC_HOSTNAME='$PUBLIC_HOSTNAME' -e REQUIRE_CLOUDFLARE_ACCESS_JWT='$REQUIRE_CLOUDFLARE_ACCESS_JWT' -e CLOUDFLARE_ACCESS_TEAM_DOMAIN='$CLOUDFLARE_ACCESS_TEAM_DOMAIN' -e CLOUDFLARE_ACCESS_AUD='$CLOUDFLARE_ACCESS_AUD' roger-knowledge-mcp:phase0"
ssh hermes-vps 'docker run --rm --network roger-knowledge-private curlimages/curl:latest -fsS http://roger-knowledge-mcp:8765/health'
```

The explicit `linux/amd64` platform is needed when building from Apple Silicon for the current `hermes-vps` host.

Start `cloudflared` on the VPS with the tunnel token:

```bash
ssh hermes-vps 'docker rm -f roger-knowledge-cloudflared-phase0 >/dev/null 2>&1 || true'
ssh hermes-vps "docker run -d --name roger-knowledge-cloudflared-phase0 --restart unless-stopped --network roger-knowledge-private cloudflare/cloudflared:latest tunnel --no-autoupdate run --token '$CLOUDFLARE_TUNNEL_TOKEN'"
```

Confirm no MCP port is published on the host:

```bash
ssh hermes-vps 'docker ps --filter name=roger-knowledge-mcp-phase0 --format "table {{.Names}}\t{{.Ports}}"'
ssh hermes-vps 'ss -ltnp | grep 8765 || echo no-host-listener-8765'
```

Expected container port output has no host IP/port mapping:

```text
8765/tcp
no-host-listener-8765
```

## Validation

Before Cloudflare secrets:

```bash
docker build -t roger-knowledge-mcp:phase0 .
docker network create roger-knowledge-private >/dev/null 2>&1 || true
docker rm -f roger-knowledge-mcp-phase0 >/dev/null 2>&1 || true
docker run -d --name roger-knowledge-mcp-phase0 --network roger-knowledge-private --network-alias roger-knowledge-mcp roger-knowledge-mcp:phase0
docker ps --filter name=roger-knowledge-mcp-phase0 --format "table {{.Names}}\t{{.Ports}}"
docker run --rm --network roger-knowledge-private curlimages/curl:latest -fsS http://roger-knowledge-mcp:8765/health
docker run --rm --network roger-knowledge-private curlimages/curl:latest -i -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' http://roger-knowledge-mcp:8765/mcp
```

After Cloudflare values:

1. Start both containers with `docker compose --env-file .env.cloudflare up --build`.
2. Confirm the Cloudflare Tunnel is healthy in the dashboard.
3. Confirm `https://mcp.fleiglabs.cc/mcp` reaches the server through Access.
4. Confirm Cloudflare AI Controls can reach `/mcp` with `REQUIRE_CLOUDFLARE_ACCESS_JWT=false`.
5. Confirm the MCP client can discover `search`, `fetch`, and `health`.
6. Confirm `search` and `fetch` calls appear in MCP server logs:

```bash
docker logs --tail 100 roger-knowledge-mcp-phase0
docker logs --tail 100 roger-knowledge-cloudflared-phase0
```
