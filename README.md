# YouKnowMe

YouKnowMe gives AI agents durable, private memory about one person.

It is a single-tenant MCP service that turns a curated Markdown corpus into searchable,
source-backed context. An agent can start a fresh conversation, ask YouKnowMe about the owner's
projects, writing, work history, preferences, equipment, or procedures, and retrieve the relevant
material without relying on a long-running chat thread.

YouKnowMe is running in production on a private VPS. Its MCP endpoint is protected by Cloudflare
Access and the service's own owner-only authorization check.

## What It Does

- Searches a private knowledge corpus with semantic queries and optional type, tag, and source
  filters.
- Retrieves exact documents or sections by stable source pointers.
- Returns source material and build provenance; the calling agent does the reasoning and writes the
  answer.
- Accepts bounded uploads and change requests into a protected intake queue.
- Uses a separate Curator agent to turn useful intake into reviewable corpus pull requests or
  issues.
- Rebuilds and deploys the index only after a human reviews and merges a corpus change.

The result is long-term memory that is independent of any chat client or model. The corpus remains
the source of truth, GitHub records every accepted change, and the live index can always be rebuilt.

## How It Works

```text
Private Markdown corpus
        |
        | build, validate, and package
        v
Versioned vector index
        |
        | deploy atomically
        v
Authenticated YouKnowMe MCP  <----  AI agents
        |
        | uploads and change requests
        v
Protected intake -> Curator -> pull request -> human review -> corpus rebuild
```

The production serving path is deliberately narrow:

- The service reads an official, versioned index artifact.
- The serving container has no corpus checkout, GitHub write credential, or merge capability.
- Cloudflare Access authenticates callers, and YouKnowMe validates the signed identity before
  serving private content.
- Query logs and intake are protected runtime data; staged content is never served directly.

## MCP Tools

| Tool | Purpose |
| --- | --- |
| `query` | Find ranked, source-backed context with optional filters. |
| `retrieve` | Fetch an exact source or section by stable ID or path. |
| `search` / `fetch` | Compatibility tools for clients that use a search-and-fetch shape. |
| `health` | Report index readiness and build provenance. |
| `upload` | Stage new Markdown for review; it does not publish or index it. |
| `corpus_change` | Request a bounded update to existing knowledge for Curator processing. |

## Repository Layout

- `src/ykm/` — index building, retrieval, MCP serving, authentication, logging, and intake.
- `src/curator/` — the bounded agent workflow that reviews intake and proposes corpus changes.
- `scripts/` — local demos, smoke tests, artifact installation, and operational helpers.
- `tests/` — unit, integration, packaging, security, and Curator workflow tests.
- `docs/` — product contracts, architecture decisions, and operator runbooks.
- `fixtures/` — synthetic content and evaluation cases for safe local development.

## Local Development

The project uses Python 3.12, `mise`, and `uv`.

```bash
mise trust
mise install
mise run sync
mise run lint
mise run test
```

Run the end-to-end retrieval demo against the synthetic corpus:

```bash
mise run demo
mise run eval
```

The synthetic path uses a deterministic fake embedding provider, so it needs no private corpus,
external model key, or production access. Generated indexes and logs are written under `.ykm/` and
ignored by Git.

To exercise the container packaging path:

```bash
mise run container-smoke
```

## Production Operations

The `ykm live` CLI provides authenticated production health, query, retrieval, and guarded intake
commands for operators:

```bash
uv run ykm live health --summary
uv run ykm live query "thermostat setup" --limit 2 --pretty
uv run ykm live retrieve <source-id> --pretty
```

Live write commands require explicit confirmation, and uploads require an idempotency key. See
[`docs/ykm-live-cli.md`](docs/ykm-live-cli.md) for configuration and safe usage.

Application changes are tested and packaged by GitHub Actions. A successful change on `main`
deploys the service through the managed VPS configuration in `grubbyhacker/vps-ops`. Corpus changes
follow a separate pipeline: the private corpus repository builds and validates the index, then the
deployment process verifies and atomically promotes that artifact in production.
