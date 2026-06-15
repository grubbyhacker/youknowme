# Curator Single Live Profile Milestone

## Summary

Create one production launch profile, `ykm-curator-live`, that runs the full live Curator loop:
reconcile existing PR/issue state, process feedback, process uploads, and repair Curator PRs. Keep
the implementation Codex-based today, but make the deployment/profile naming agent-neutral so future
Hermes or larger-agent experiments can plug in without changing the production trigger surface.

Use simple per-run limits:

- Upload PRs: `1`
- Feedback GitHub outcomes: `2`
- PR repairs: `1`
- Total new GitHub objects: `3`
- Codex attempts: `2` for upload and feedback, `1` PR repair per run

The existing hourly timer should launch this single profile. Future upload/feedback-triggered
launches should reuse the same profile rather than introduce a separate code path.

## Key Changes

- In `vps-ops`, add sandbox-broker launch profile `ykm-curator-live` using the existing
  model-capable Curator template and current YouKnowMe image.
- Configure the embedded Curator task:
  - `mode: "manual_live"`
  - `enabled_actions: ["reconcile", "plan_feedback", "plan_uploads", "repair_prs"]`
  - `feedback_executor: "codex_proxy"`
  - `upload_review_executor: "codex_proxy"`
  - `pr_repair_executor: "codex_proxy"`
  - `feedback_agent_model`, `upload_review_agent_model`, and `pr_repair_model`:
    `ykm-codex-gpt-5-mini`
  - `feedback_agent_max_attempts: 2`
  - `upload_review_max_attempts: 2`
  - `pr_repair_max_per_run: 1`
  - validation commands: `["mise", "run", "validate"]`
- Update the timer principal to allow launching only the production-safe live profile plus existing
  dry/state inspection profiles:
  - keep `ykm-curator-dry-run`
  - keep `ykm-curator-state-only`
  - add `ykm-curator-live`
  - remove `ykm-curator-upload-pr-timer` from timer usage
- Update `ykm-curator-launch.service` to POST
  `/v1/launch-profiles/ykm-curator-live/launch`.
- Keep the old manual profiles during the milestone for rollback and operator diagnostics:
  - `ykm-curator-upload-pr-live`
  - `ykm-curator-feedback-live`
  - `ykm-curator-repair-live`
- Do not introduce a generic agent-executor abstraction yet. Keep `codex_proxy` as today's executor
  value, but use neutral deployment names (`ykm-curator-live`, "agent model") so a future Hermes
  executor can be added as a new executor implementation without renaming the production trigger.

## Product Code And Docs

- Add or update Curator tests in `youknowme` proving a combined `manual_live` task can:
  - reconcile PR state and select a requested-change Curator PR for repair
  - process feedback with `feedback_executor=codex_proxy`
  - process one upload with `upload_review_executor=codex_proxy`
  - report all enabled actions and per-area result counts in one run
- Update Curator docs/runbook to describe the single live profile as the production shape.
- Document that timer and future immediate triggers both launch `ykm-curator-live`; concurrency is
  handled by the existing Curator lock, so trigger callers do not need separate scheduling logic in
  this milestone.
- Do not implement upload/feedback immediate triggers in this milestone. Leave the upload/feedback
  write paths unchanged.

## Deployment Plan

- Make source-of-truth changes in `vps-ops`, not by hand-editing `hermes-vps`.
- Deploy through the existing Ansible/GitHub workflow:
  - deploy `gh-agent-broker` config so sandbox-broker gets `ykm-curator-live`
  - deploy `curator` systemd units so the timer launches the live profile
- Before enabling the timer change, manually launch `ykm-curator-live` once with the operator token
  and inspect `run-report.json`.
- Expected smoke outcome when work exists:
  - `enabled_actions` includes all four actions
  - `reconciliation.pr_reconciliation_count` is nonzero when Curator PRs exist
  - PR #18 is classified as repairable if still open with requested changes
  - at most one PR repair is attempted
  - at most one upload PR is opened
  - at most two feedback outcomes are created
- After smoke passes, enable/restart the timer and confirm the next timer-launched run uses
  `ykm-curator-live`.

## Test Plan

- In `youknowme`:
  - targeted Curator tests for combined live task behavior
  - existing `tests/test_curator.py`
  - `mise run lint`
  - `mise run test`
- In `vps-ops`:
  - template/render or Ansible syntax validation for sandbox-broker config and curator systemd role
  - deploy dry-run/check mode if available
  - production smoke through read-only report inspection after deploy
- Acceptance checks:
  - hourly run no longer reports `enabled_actions: plan_uploads` only
  - PR repair results can become nonzero from the timer path
  - feedback processing can append durable outcomes from the timer path
  - upload processing still creates no more than one PR per run

## Assumptions

- `ykm_curator_image` should remain the shared current YouKnowMe image; do not maintain separate
  upload/feedback/repair images.
- The existing broker/proxy credential bundle is sufficient for all three Codex executor paths.
- Starvation control is per-run limits only; no priority queue or fairness scheduler in this
  milestone.
- Future immediate upload/feedback triggers will call the same `ykm-curator-live` launch profile and
  rely on the existing Curator lock for concurrency.

