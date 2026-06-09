# YouKnowMe Curator Launcher Plan

Status: planned; sandbox-broker operator REST launch profiles are live and E2E tested.

This document captures how Curator should be triggered on the VPS. It depends on the generic
sandbox-broker operator REST launch profile design in
`/Users/roger/src/gh-agent-broker/plans/operator-rest-launch-profiles.md`.

## Summary

Use sandbox-broker operator REST launch profiles as the only non-agent trigger surface for Curator.
The VPS runs a `systemd.timer` that calls a fixed host-local REST profile with authenticated `curl`;
sandbox-broker launches the short-lived Curator worker container and collects
`/output/run-report.json` and `/output/run-report.md`.

YKM remains decoupled: it writes uploads, feedback, and logs only. It does not call sandbox-broker,
hold launch tokens, or wake Curator directly in v1.

## Sandbox Profile

Add a private sandbox-broker launch profile named `ykm-curator-dry-run` with a complete fixed launch
request and no caller overrides:

```yaml
repositories:
  - "grubbyhacker/ykmcorpus"

launch_profiles:
  ykm-curator-dry-run:
    template: "ykm-curator-dry-run"
    repo: "grubbyhacker/ykmcorpus"
    base_branch: "main"
    task: >
      {"schema_version":"1","run_id":"${SANDBOX_RUN_ID}","mode":"dry_run",
      "enabled_actions":["reconcile","plan_feedback","plan_uploads"],
      "github_mutation_budget":{"max_new_objects_per_run":0,"upload":0,"feedback":0},
      "model_call_budget":{"max_calls_per_run":0,"max_tokens_per_run":0},
      "feedback_soft_action_threshold":10,"stale_lock_timeout_seconds":7200}
    max_runtime_minutes: 30
    deliverables:
      - "/output/run-report.json"
      - "/output/run-report.md"

operator_principals:
  ykm-curator-timer:
    token_env: "SANDBOX_OPERATOR_YKM_CURATOR_TIMER_TOKEN"
    allowed_profiles: ["ykm-curator-dry-run"]
    allowed_actions: ["launch", "dry_run"]
  ykm-curator-operator:
    token_env: "SANDBOX_OPERATOR_YKM_CURATOR_ADMIN_TOKEN"
    allowed_profiles: ["ykm-curator-dry-run"]
    allowed_actions: ["launch", "dry_run", "status", "logs", "artifacts", "stop", "cleanup"]
```

Use a Curator worker template like:

```yaml
templates:
  ykm-curator-dry-run:
    image: "youknowme:phase4-curator"
    command: ["curator", "run", "--task", "/input/task.json"]
    user: "10000:10000"
    resources: {cpu_shares: 512, memory_mb: 4096, pids_limit: 512}
    network_policy: "worker-net"
    max_runtime_minutes: 30
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
      - source_path: "/opt/youknowme/intake"
        mount_path: "/data/intake"
        readonly: false
      - source_path: "/opt/youknowme/logs"
        mount_path: "/data/logs"
        readonly: true
```

The intake mount is writable because the current dry-run worker writes Curator-owned plan artifacts
under `feedback/runs/<run_id>/` and `uploads/runs/<run_id>/`. It must still not advance checkpoints,
append decisions, move upload queues, or mutate bundle metadata in `dry_run`.

## Task Boundary

- Treat `/input/task.json` as broker-owned forever. Sandbox-broker writes its generic task wrapper
  there; Curator reads the wrapper and parses Curator-specific JSON from the wrapper's `task` string.
  Future worker-specific config, if needed, should use a worker-owned path such as
  `/config/curator-task.json`, not `/input/*`.

Because sandbox-broker generates the run ID at launch time, the embedded Curator JSON should use
`"run_id":"${SANDBOX_RUN_ID}"`. Curator replaces that placeholder with the broker wrapper `run_id`
before validating the task. A literal run ID is accepted only when it exactly matches the broker
wrapper run ID.

`--enable-broker-reads` stays off for the first profile. Add a separate profile or template command
variant later when live read reconciliation is intentionally being E2E tested.

## Systemd Units

Store only the timer launch token in `/etc/youknowme/curator-launch.env`:

```dotenv
YKM_CURATOR_LAUNCH_TOKEN=...
```

Install the one-shot service:

```ini
[Unit]
Description=Launch YouKnowMe Curator dry run
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
EnvironmentFile=/etc/youknowme/curator-launch.env
ExecStart=/usr/bin/curl -fsS -X POST \
  -H "Authorization: Bearer ${YKM_CURATOR_LAUNCH_TOKEN}" \
  http://127.0.0.1:8091/v1/launch-profiles/ykm-curator-dry-run/launch
```

Install the timer disabled at first, then enable it after the manual smoke passes:

```ini
[Unit]
Description=Periodic YouKnowMe Curator dry run

[Timer]
OnCalendar=hourly
Persistent=false
RandomizedDelaySec=10m

[Install]
WantedBy=timers.target
```

Manual operator runs use `sudo systemctl start ykm-curator-launch.service`. The timer token cannot
read logs/artifacts or stop/cleanup runs; artifact review uses the separate human operator token.

## Test Plan

- Sandbox-broker side:
  - validate the profile with the `dry-run` REST endpoint before enabling the timer;
  - verify the dry-run output shows the Curator task JSON embedded inside broker `TaskContract.task`;
  - verify the timer token can launch only the Curator profile and cannot read logs/artifacts or
    stop/cleanup runs;
  - verify missing or invalid token fails with no container launch.
- VPS smoke:
  - manually start `ykm-curator-launch.service`;
  - confirm sandbox-broker returns a run ID;
  - retrieve run status/artifacts with a human operator token;
  - inspect the report with `curator inspect-report /path/to/run-report.json`;
  - inspect `run-report.md` and `run-report.json` and verify real upload/feedback IDs are visible
    but untouched.
- Curator safety checks:
  - production intake bundles remain in `uploads/pending`;
  - `dry_run` does not append `curator-decisions.jsonl` or advance `curator-state.json`;
  - any `feedback/runs/<run_id>/feedback-plan.json` and `uploads/runs/<run_id>/upload-plan.json`
    writes are limited to Curator-owned run artifact paths;
  - report confirms no GitHub mutations and zero model calls.

## Assumptions

- V1 trigger is timer-only plus manual `systemctl start`; no upload-triggered wake path yet.
- Host stays Docker-oriented: no Python or `uv` installed on the VPS for launching.
- Curator/YKM-specific launch choices live in this repo's docs and private VPS sandbox config, not in
  `gh-agent-broker` code.
- Later `state_only` or live-capable profiles should be separate named profiles with separate tokens,
  not edits to the dry-run profile.
