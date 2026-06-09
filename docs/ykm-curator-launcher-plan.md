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

## Key Changes

- Add a private sandbox-broker launch profile, likely `ykm-curator-dry-run`, with a complete fixed
  launch request:
  - template: Curator worker template;
  - repo and base branch: `grubbyhacker/ykmcorpus` and `main`;
  - task: a JSON string containing the Curator task payload for `mode: dry_run`, enabled actions
    `reconcile`, `plan_feedback`, and `plan_uploads`;
  - deliverables: `/output/run-report.json` and `/output/run-report.md`;
  - no caller overrides in v1.
- Treat `/input/task.json` as broker-owned forever. Sandbox-broker writes its generic task wrapper
  there; Curator reads the wrapper and parses Curator-specific JSON from the wrapper's `task` string.
  Future worker-specific config, if needed, should use a worker-owned path such as
  `/config/curator-task.json`, not `/input/*`.
- Add a scoped operator token for the timer:
  - allowed profile: `ykm-curator-dry-run`;
  - allowed actions: `launch`, optionally `dry_run`;
  - no `logs`, `artifacts`, `stop`, or `cleanup` for the timer token;
  - a separate human operator token can allow status, logs, artifact collection, stop, and cleanup.
- Add VPS `systemd` units:
  - `ykm-curator-launch.service`: one-shot `curl -fsS -X POST` to
    `http://127.0.0.1:8091/v1/launch-profiles/ykm-curator-dry-run/launch` with
    `Authorization: Bearer $YKM_CURATOR_OPERATOR_TOKEN`;
  - `ykm-curator-launch.timer`: conservative periodic schedule, initially disabled or low-frequency
    until the first YKM-specific dry-run smoke is reviewed;
  - environment file outside git, such as `/etc/youknowme/curator-launch.env`, containing only the
    operator REST token.
- Keep Curator worker behavior unchanged:
  - short-lived container;
  - single-flight Curator lock prevents overlapping meaningful work;
  - `dry_run` first; no checkpoint advancement, queue movement, model calls, GitHub mutation, branch
    edits, or PR/issue creation;
  - dry-run plan artifacts may be written to Curator-owned run paths if the intake mount is writable;
  - `--enable-broker-reads` may be enabled in the worker template/profile only after broker-read E2E
    is intended.

## Test Plan

- Sandbox-broker side:
  - validate the profile with the `dry-run` REST endpoint before enabling the timer;
  - verify the timer token can launch only the Curator profile and cannot read logs/artifacts or
    stop/cleanup runs;
  - verify missing or invalid token fails with no container launch.
- VPS smoke:
  - manually start `ykm-curator-launch.service`;
  - confirm sandbox-broker returns a run ID;
  - retrieve run status/artifacts with a human operator token;
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
