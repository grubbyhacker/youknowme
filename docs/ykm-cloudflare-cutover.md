# YouKnowMe Cloudflare Contract And Cutover

Phase 1D records the existing Cloudflare Tunnel / Access contract that production YouKnowMe must
reuse. The checked-in `POC/` files are the reference. Do not modify them, restart the running POC, or
start another `cloudflared` with the existing tunnel token during Phase 1D.

## Existing Contract

Current public flow:

```text
Remote MCP client
-> https://mcp.fleiglabs.cc/mcp
-> Cloudflare Access application for mcp.fleiglabs.cc
-> existing Cloudflare Tunnel
-> cloudflared on hermes-vps
-> http://roger-knowledge-mcp:8765/mcp on Docker network roger-knowledge-private
-> POC roger-knowledge-mcp container
```

Discovered from `POC/README.md`, `POC/.env.example`, `POC/.env.cloudflare.example`,
`POC/docker-compose.yml`, and `POC/src/roger_knowledge_mcp/server.py`:

- Public hostname: `mcp.fleiglabs.cc`.
- Public MCP route: `https://mcp.fleiglabs.cc/mcp`.
- POC origin service target: `http://roger-knowledge-mcp:8765`.
- POC origin MCP path: `/mcp`.
- POC private Docker network: `roger-knowledge-private`.
- POC MCP container name: `roger-knowledge-mcp-phase0`.
- POC cloudflared container name: `roger-knowledge-cloudflared-phase0`.
- Origin health endpoint in the POC: `/health`.
- Production health endpoint: `/livez`.
- No MCP host port is expected to be published on the VPS.

The existing tunnel token belongs to the live POC route. It must continue to run only on
`hermes-vps`; starting a second `cloudflared` with that token can contend with the active route.

## Access Assertion

Cloudflare Access is expected to authenticate the remote client and forward the signed identity
assertion in this header:

```text
Cf-Access-Jwt-Assertion
```

The assertion is a Cloudflare Access JWT. Production YouKnowMe public mode validates it by:

- fetching JWKS from `${YKM_CLOUDFLARE_TEAM_DOMAIN}/cdn-cgi/access/certs`;
- accepting only RS256 signatures from those keys;
- checking `iss` against `YKM_CLOUDFLARE_TEAM_DOMAIN`;
- checking `aud` against `YKM_CLOUDFLARE_AUD`;
- checking token expiry, with the service's small decode leeway;
- checking verified claim `email` against `YKM_OWNER_EMAIL`.

Do not authorize from unverified email headers. The strict public path fails closed when the Access
JWT is missing, invalid, expired, has the wrong issuer, has the wrong audience, or has the wrong owner
email.

The existing Cloudflare AI Controls path is a compatibility exception: Cloudflare authenticates at the
edge but does not forward `Cf-Access-Jwt-Assertion` to the origin. For that deployed path only,
`YKM_CLOUDFLARE_TRUST_EDGE_AUTH=true` lets YouKnowMe trust the existing Cloudflare Tunnel / Access
edge when the JWT is absent. If a JWT is present, YouKnowMe still validates it and rejects invalid or
mismatched claims.

Production environment mapping:

```text
POC CLOUDFLARE_ACCESS_TEAM_DOMAIN -> YKM_CLOUDFLARE_TEAM_DOMAIN
POC CLOUDFLARE_ACCESS_AUD         -> YKM_CLOUDFLARE_AUD
Production-only owner allowlist   -> YKM_OWNER_EMAIL
Existing AI Controls compatibility -> YKM_CLOUDFLARE_TRUST_EDGE_AUTH=true
```

Local/Hermes auth stays separate. `YKM_LOCAL_AUTH_SECRET` is valid only in `local` mode and is not a
public-mode fallback.

## Production Readiness

Production YouKnowMe already serves MCP at `/mcp`, matching the existing public route path. Its
container listens on port `8765` by default, matching the existing tunnel service port.

Before cutover, the VPS deployment must provide:

- the official generated index mounted read-only at `/data/index`;
- writable protected query logs at `/data/logs/query-log.jsonl`;
- `YKM_AUTH_MODE=public`;
- `YKM_OWNER_EMAIL` set to the authorized owner email;
- `YKM_CLOUDFLARE_TEAM_DOMAIN` set to the same Access team domain as the POC;
- `YKM_CLOUDFLARE_AUD` set to the same Access application AUD tag as the POC;
- `YKM_CLOUDFLARE_TRUST_EDGE_AUTH=true` when using the existing AI Controls path that does not
  forward `Cf-Access-Jwt-Assertion`;
- `OPENROUTER_API_KEY` only if the mounted index uses OpenRouter embeddings at query time.

## Cutover Plan

Phase 1E should deploy the production container on `hermes-vps` beside the POC without changing the
Cloudflare tunnel first.

1. Create or reuse the private Docker network used by the tunnel.
2. Start the production YouKnowMe container on that network with a temporary network alias, for
   example `youknowme`.
3. From inside the Docker network, verify `http://youknowme:8765/livez`.
4. From inside the Docker network, verify `http://youknowme:8765/mcp` fails closed without
   `Cf-Access-Jwt-Assertion`.
5. Retarget the existing Cloudflare Tunnel public hostname service from
   `http://roger-knowledge-mcp:8765` to `http://youknowme:8765`. Do this by updating the existing
   tunnel route/configuration, not by creating a new tunnel.
6. Verify the remote MCP URL remains `https://mcp.fleiglabs.cc/mcp`.
7. Verify authenticated remote MCP through the existing Access app with ChatGPT and Claude.
8. Verify missing or mismatched Access JWT still receives 403 from the production origin.
9. Verify query logs are written and contain source pointers only.

The route path should not change during cutover. Clients should continue to use
`https://mcp.fleiglabs.cc/mcp`.

## Rollback Plan

Rollback is restoring the existing tunnel origin service target to the POC service:

```text
http://roger-knowledge-mcp:8765
```

After rollback:

1. Confirm `https://mcp.fleiglabs.cc/mcp` again reaches the POC through the existing Access app.
2. Confirm the POC container still has no host MCP port published.
3. Keep the production container stopped or detached from the tunnel route until the issue is fixed.

Do not delete the POC during initial production stabilization. It remains the rollback target and the
reference for the validated Phase 0 tunnel path.
