# AGENTS.md

## Project

The official product name is YouKnowMe.

This repository is the home for the production YouKnowMe project. The previous working prototype has been preserved under `POC/`.

## Hard Boundary

Do not create new production content inside `POC/`.

`POC/` is reference-only. It may be read to understand the working Phase 0 Cloudflare Tunnel, Cloudflare Access, Docker, VPS, and MCP transport pattern. It may only be modified when the user explicitly asks to maintain or repair the running prototype on the VPS.

Production YouKnowMe work must happen outside `POC/`.

## Documentation

Place the PRD and supporting documentation in `docs/`.

Prefer small, explicit documents with stable names over burying project requirements in chat history.

## Tooling

Use `mise` for local tool versions and `uv` for Python dependency management.

Common commands:

```bash
mise install
mise run sync
mise run lint
mise run test
```

## Secrets

Do not commit `.env`, `.env.*`, tunnel tokens, Access credentials, VPS credentials, or generated private runtime files.

