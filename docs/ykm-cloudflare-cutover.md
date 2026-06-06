# YouKnowMe Cloudflare Contract And Cutover

Phase 1D records the existing Cloudflare Tunnel / Access contract that production YouKnowMe must
reuse. The checked-in `POC/` files are the reference. Do not modify them, restart the running POC, or
start another `cloudflared` with the existing tunnel token during Phase 1D.

## Direct Access Contract

Current public flow:

```text
Remote MCP client
-> https://mcp.fleiglabs.cc/mcp
-> Cloudflare Access / Managed OAuth application for mcp.fleiglabs.cc
-> Cloudflare Tunnel
-> cloudflared on hermes-vps
-> http://roger-knowledge-mcp:8765/mcp on Docker network roger-knowledge-private
-> YouKnowMe MCP container
```

Discovered from `POC/README.md`, `POC/.env.example`, `POC/.env.cloudflare.example`,
`POC/docker-compose.yml`, and `POC/src/roger_knowledge_mcp/server.py`:

- Public hostname: `mcp.fleiglabs.cc`.
- Public MCP route: `https://mcp.fleiglabs.cc/mcp`.
- Origin service target: `http://roger-knowledge-mcp:8765`.
- Origin MCP path: `/mcp`.
- POC private Docker network: `roger-knowledge-private`.
- POC MCP container name: `roger-knowledge-mcp-phase0`.
- POC cloudflared container name: `roger-knowledge-cloudflared-phase0`.
- Production liveness endpoints: `/livez` and `/health`.
- No MCP host port is expected to be published on the VPS.

The tunnel token must continue to run only on `hermes-vps`; starting a second `cloudflared` with
that token can contend with the active route. Do not create `origin-mcp.fleiglabs.cc` or any second
public upstream hostname.

## Access Assertion

Cloudflare Access authenticates the remote client. YouKnowMe authorizes the request by validating a
Cloudflare Access identity token from either this header:

```text
Cf-Access-Jwt-Assertion
```

or this standard OAuth bearer header:

```text
Authorization: Bearer <token>
```

The token is a Cloudflare Access JWT. Production YouKnowMe public mode validates it by:

- fetching JWKS from `${YKM_CLOUDFLARE_TEAM_DOMAIN}/cdn-cgi/access/certs`;
- accepting only RS256 signatures from those keys;
- checking `iss` against `YKM_CLOUDFLARE_TEAM_DOMAIN`;
- checking `aud` against `YKM_CLOUDFLARE_AUD`;
- checking token expiry, with the service's small decode leeway;
- accepting verified claim `email` only when it matches `YKM_OWNER_EMAIL`;
- otherwise accepting verified claim `common_name` only when it matches one of the comma-separated
  Cloudflare Access service token Client IDs in `YKM_ALLOWED_SERVICE_COMMON_NAMES`.

Do not authorize from unverified email headers. The strict public path returns `401` when the token
is missing, malformed, expired, has the wrong issuer, has the wrong audience, or cannot be verified.
It returns `403` only when the token is valid but neither the owner email nor an explicitly allowed
service token identity is authorized.

Hermes uses this same public Cloudflare Access path. Its gateway sends
`CF-Access-Client-Id` / `CF-Access-Client-Secret` to Cloudflare; Cloudflare validates the service
token and forwards a signed Access JWT to YouKnowMe. YouKnowMe's second-layer authorization must use
the verified JWT `common_name` claim and must not authorize service tokens from `aud` plus a missing
email alone.

YouKnowMe also exposes OAuth protected-resource metadata at:

```text
/.well-known/oauth-protected-resource
/.well-known/oauth-protected-resource/mcp
```

Both metadata endpoints identify `https://mcp.fleiglabs.cc/mcp` as the protected resource, list
`${YKM_CLOUDFLARE_TEAM_DOMAIN}` as the authorization server, and support bearer tokens in headers.

Production environment mapping:

```text
POC CLOUDFLARE_ACCESS_TEAM_DOMAIN -> YKM_CLOUDFLARE_TEAM_DOMAIN
POC CLOUDFLARE_ACCESS_AUD         -> YKM_CLOUDFLARE_AUD
Production-only owner allowlist   -> YKM_OWNER_EMAIL
Hermes service token Client ID    -> YKM_ALLOWED_SERVICE_COMMON_NAMES
Public resource URL               -> YKM_MCP_RESOURCE_URL=https://mcp.fleiglabs.cc/mcp
```

Local shared-secret auth stays separate. `YKM_LOCAL_AUTH_SECRET` is valid only in `local` mode and is
not a public-mode fallback.

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
- `YKM_ALLOWED_SERVICE_COMMON_NAMES` set to the Hermes Cloudflare Access service token Client ID,
  after confirming Cloudflare includes that ID as the verified JWT `common_name`;
- `YKM_MCP_RESOURCE_URL=https://mcp.fleiglabs.cc/mcp`;
- `OPENROUTER_API_KEY` only if the mounted index uses OpenRouter embeddings at query time.

## Cutover Plan

Phase 1E should deploy the production container on `hermes-vps` beside the POC without changing the
Cloudflare tunnel first.

1. Create or reuse the private Docker network used by the tunnel.
2. Start the production YouKnowMe container on that network with a temporary network alias, for
   example `youknowme`.
3. From inside the Docker network, verify `http://youknowme:8765/livez`.
4. From inside the Docker network, verify `http://youknowme:8765/mcp` returns `401` without an
   Access token.
5. Retarget the existing Cloudflare Tunnel public hostname service from
   `http://roger-knowledge-mcp:8765` to `http://youknowme:8765`. Do this by updating the existing
   tunnel route/configuration, not by creating a new tunnel.
6. Verify the remote MCP URL remains `https://mcp.fleiglabs.cc/mcp`.
7. Verify unauthenticated public `https://mcp.fleiglabs.cc/mcp` returns `401` or an OAuth challenge,
   never `200`.
8. Verify authenticated remote MCP through the direct Access app.
9. Run ChatGPT and Claude only as compatibility tests after unauthenticated `/mcp` is blocked.
10. Verify query logs are written and contain source pointers only.

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
