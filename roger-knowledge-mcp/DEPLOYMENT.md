# Deployment Guardrail

`cloudflared` for this POC must run only on `hermes-vps`.

Do not start `roger-knowledge-cloudflared-phase0` on the development Mac. The dev machine may build images and transfer them to `hermes-vps`, but the live containers are expected to run on the VPS:

- `roger-knowledge-mcp-phase0`
- `roger-knowledge-cloudflared-phase0`

