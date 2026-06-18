# YouKnowMe Live MCP CLI

The `ykm live` command calls the live YouKnowMe MCP over streamable HTTP from a development
machine. It is intended for operator smoke tests, debugging client behavior, and explicitly staged
intake writes.

## Configuration

Create local `.env` entries for the live route and Cloudflare Access credentials:

```bash
YKM_LIVE_MCP_URL=https://mcp.fleiglabs.cc/mcp
YKM_CF_ACCESS_CLIENT_ID=...
YKM_CF_ACCESS_CLIENT_SECRET=...
```

Advanced alternatives:

```bash
YKM_CF_ACCESS_JWT=...
YKM_LIVE_BEARER_TOKEN=...
YKM_LIVE_TIMEOUT_SECONDS=30
```

For HTTPS URLs, the CLI fails before calling MCP if no auth is configured. Service-token headers are
preferred when `YKM_CF_ACCESS_CLIENT_ID` and `YKM_CF_ACCESS_CLIENT_SECRET` are present.

## Read Commands

```bash
uv run ykm live tools --pretty
uv run ykm live health --pretty
uv run ykm live query "thermostat setup" --limit 2 --pretty
uv run ykm live query "home hot tub bromine" --tag spa --limit 3 --pretty
uv run ykm live retrieve thermostat-bryant-ksacn1401aaa-heat-pump --pretty
uv run ykm live retrieve some-section-id --kind section_id --pretty
uv run ykm live search "weekly spa maintenance" --pretty
uv run ykm live fetch thermostat-bryant-ksacn1401aaa-heat-pump --pretty
```

JSON is the default output format. Use `--summary` after `live` for compact operator output:

```bash
uv run ykm live health --summary
uv run ykm live query "thermostat setup" --limit 2 --summary
```

## Intake Write Commands

Upload and feedback are intentionally guarded. `--dry-run` prints the exact MCP payload without
calling the server. Live writes require `--yes`.

```bash
uv run ykm live upload \
  --file /path/to/new-note.md \
  --purpose "operator CLI smoke" \
  --suggested-type note \
  --suggested-tag maintenance \
  --dry-run \
  --pretty
```

```bash
uv run ykm live upload \
  --file /path/to/new-note.md \
  --purpose "operator CLI smoke" \
  --yes \
  --pretty
```

```bash
uv run ykm live corpus-change \
  --intent add_to_existing \
  --instruction "Live CLI smoke: verify protected corpus change intake." \
  --dry-run \
  --pretty
```

```bash
uv run ykm live corpus-change \
  --intent add_to_existing \
  --instruction "Live CLI smoke: verify protected corpus change intake." \
  --yes \
  --pretty
```

These write tools stage protected intake only. They do not publish, index, merge, rebuild, or deploy
corpus content.
