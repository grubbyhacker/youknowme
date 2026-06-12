# YouKnowMe Curator Dry-Run Harness Milestone

Status: complete; verified through sandbox-broker on `hermes-vps`.

This milestone adds a minimal Curator worker entrypoint that proves the runtime contract before the
real Curator behavior is built.

The dry-run harness does not curate content, open PRs, create issues, move upload bundles, or call a
model for decisions. It validates the sandbox and operator wiring, then writes a bounded run report.

## Purpose

The dense Curator milestone should not be blocked by uncertain infrastructure. The dry-run harness
answers these questions first:

- Can the worker read the mounted intake evidence?
- Can it read query logs when logs are mounted?
- Can it write the required output report?
- Are forbidden provider, GitHub, and VPS secrets absent from the worker environment?
- Are broker credentials present when broker probing is required?
- Is the broker health endpoint reachable when configured?
- Is the model-proxy health endpoint reachable when configured?

## Entrypoints

Local development:

```bash
mise run curator-dry-run
```

Direct CLI:

```bash
uv run ykm curator-dry-run \
  --run-id local-curator-dry-run \
  --intake .ykm/curator-dry-run/intake \
  --logs .ykm/curator-dry-run/logs \
  --output .ykm/curator-dry-run/output \
  --no-task
```

Container or sandbox shortcut:

```bash
ykm-curator-dry-run \
  --run-id "$SANDBOX_RUN_ID" \
  --intake /data/intake \
  --logs /data/logs \
  --output /output \
  --task /input/task.json \
  --broker-url "$BROKER_URL" \
  --model-proxy-url "$GH_AGENT_PROXY_URL" \
  --model-proxy-token "$GH_AGENT_PROXY_TOKEN" \
  --require-broker \
  --require-model-proxy
```

## Report Contract

The harness writes:

```text
/output/run-report.json
/output/run-report.md
```

`run-report.json` records:

- schema version;
- run id and timestamp;
- pass/fail status;
- task JSON, if present;
- intake, log, and output paths;
- upload queue counts;
- pending upload ids;
- feedback JSONL record count;
- query-log JSONL record count;
- probe statuses and bounded details.

Forbidden environment variables are reported by name only, never by value.

## Sandbox Template Shape

The sandbox-broker template should use this shape. The current `hermes-vps` template uses
`youknowme:curator-dry-run-20260608`, which is a stable tag for the verified post-PR #7 YKM image.
It runs as `1000:1000` because the live `/docker/youknowme/data/intake` and
`/docker/youknowme/data/logs` trees are
owned by the VPS `ubuntu` user.

```yaml
templates:
  ykm-curator-dry-run:
    image: "youknowme:curator-dry-run-20260608"
    command:
      - "ykm-curator-dry-run"
      - "--require-broker"
      - "--require-model-proxy"
      - "--model-proxy-url"
      - "http://gh-agent-proxy:8092"
      - "--model-proxy-token"
      - "configured-by-sandbox-template"
    user: "1000:1000"
    network_policy: "worker-net"
    max_runtime_minutes: 10
    broker_agent_id: "ykm-curator"
    broker_agent_secret_env: "YKM_CURATOR_BROKER_SECRET"
    branch_policy:
      generate_prefix: "curator"
      allowed_patterns:
        - "^curator/[A-Za-z0-9_.:-]+/[A-Za-z0-9_.:-]+$"
      base_branches: ["main"]
    deliverables:
      - "/output/run-report.json"
      - "/output/run-report.md"
    environment:
      GH_AGENT_PROXY_URL: "http://gh-agent-proxy:8092"
    extra_mounts:
      - source_path: "/docker/youknowme/data/intake"
        mount_path: "/data/intake"
        readonly: true
      - source_path: "/docker/youknowme/data/logs"
        mount_path: "/data/logs"
        readonly: true
```

The future real Curator template will need narrower write access for upload queue state moves and
Curator state. The dry-run template deliberately starts read-only for intake and logs.

## Acceptance

This milestone is complete when:

- `mise run curator-dry-run` passes locally.
- The YKM image contains `ykm-curator-dry-run`.
- The command writes both report files.
- Unit tests cover report generation, forbidden-secret failure, and required broker-probe failure.
- A VPS sandbox template can launch the command and collect `/output/run-report.json`.

Live VPS verification:

- Template: `ykm-curator-dry-run`.
- Run ID: `20260608T230054Z-2256c2e7576399ca`.
- Report:
  `/srv/hermes-sandbox-broker/runs/20260608T230054Z-2256c2e7576399ca/output/run-report.json`.
- Result: `status=pass`.

## Follow-Up

After the dry-run worker is launched successfully through `sandbox-broker`, the dense Curator
milestone can start with confidence that the container, mounts, output contract, broker identity, and
model-proxy boundary are wired correctly.
