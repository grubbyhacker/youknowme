# AGENTS.md

## Project

The official product name is YouKnowMe.

This repository is the home for the production YouKnowMe project.

## Hard Boundary

The POC in 'POC/' should be ignored and never mentioned. Do not try to maintain the POC code it will be deleted.

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

