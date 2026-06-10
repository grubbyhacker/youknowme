# YouKnowMe Curator Implementation Status

Status: initial production-safe manual workflow implemented; live mutation/model execution remains
contract-blocked.

Use this file to restart Curator implementation work without relying on chat history. The contract
source of truth remains `docs/ykm-phase4-curator.md` and `docs/ykm-curator-contracts.md`.

## Safety Boundaries

- Production Curator code lives under `src/curator`.
- `POC/` is reference-only and must stay untouched unless explicitly requested.
- YKM serving remains passive.
- Do not add merge, deploy, index rebuild, queue movement, provider-key use, direct GitHub token use,
  direct GitHub mutations, or live model calls.
- Prefer deterministic and offline behavior first.
- Broker/model behavior is allowed only through the documented broker/proxy contracts and currently
  remains preflight-only unless explicitly enabled by a future contract.

## Implemented

- Native `curator` CLI with `run`, `inspect-task`, and `inspect-report`.
- Compatibility path for `ykm-curator-dry-run`.
- Task loading and validation for `dry_run`, `state_only`, and guarded `manual_live`.
- Single-flight run locking with stale-lock recovery guard.
- Forbidden secret environment checks.
- Feedback checkpoint loading, window freezing, and bounded malformed-record reporting.
- Deterministic feedback planning without model calls.
- Skipping terminal feedback decisions from `curator-decisions.jsonl`.
- Re-entry for ready `deferred` and `capacity_deferred` feedback.
- Feedback grouping by durable source, section, and upload evidence.
- Feedback soft-cap reporting through `capacity_deferred_feedback_ids`.
- State-only decision appends for no-op, upload-linked, and capacity-deferred feedback only.
- Upload queue discovery across `pending`, `claimed`, `processed`, `rejected`, `archive`, and
  `deferred`.
- Upload `manifest.json` and `curator.json` parsing and validation.
- Upload review previews without queue moves or metadata writes.
- Deferred upload re-entry for `next_run` and elapsed `retry_after`.
- Upload ID mismatch validation between `manifest.json` and `curator.json`.
- PR and issue snapshot reconciliation from broker fixtures.
- PR state classification and per-state reconciliation counts.
- Terminal PR previews for upload and feedback state.
- Closed issue previews for owner-input re-entry.
- State-only reconciliation writes for accepted feedback decisions and existing upload metadata only.
- Deterministic branch and idempotency helpers with offline collision checks.
- Broker fixture preflight for branch and idempotency collisions.
- HTTP broker read-preflight descriptor generation.
- Opt-in HTTP broker PR and issue reads for reconciliation through `--enable-broker-reads`, using
  only `BROKER_AGENT_ID` and `BROKER_AGENT_SECRET`.
- HTTP model proxy health probing only; no live model calls.
- Model fixture budget preflight and typed fixture response validation.
- Upload-review model evals with sanitized dev-environment and hot-tub/manual scenarios. Live eval
  evidence currently supports Sonnet for the first upload-review implementation.
- Run reports in JSON and Markdown with plans, summaries, failures, preflights, policy results,
  reconciliation, referenced evidence, capacity deferrals, and model budget fields.

## Current Verification

Latest known green checks:

```bash
uv run pytest tests/test_curator.py -q
mise run lint
mise run test
```

Observed result at the latest Curator handoff:

- Full suite: 238 passed.
- Lint: passed.
- Full suite has one existing Starlette/httpx deprecation warning in `tests/test_server.py`.

## Contract-Blocked Or Future Work

- Broker-backed GitHub reads for PR and issue reconciliation are implemented behind explicit
  `--enable-broker-reads` opt-in. They remain read-only and do not create branches, PRs, issues, or
  comments.
- Broker-backed PR and issue creation remains disabled. The Curator emits policy-checked execution
  intents and fixture simulation results only; real creation requires a future execution contract.
- Model-backed feedback planning and upload review remain disabled in production execution. Offline
  and live-proxy eval harnesses exist; production live model calls require a future planning
  execution contract.
- Upload-review PR creation must first add an observe step that applies model-produced drafts to a
  temporary `ykmcorpus` checkout and runs `mise run validate`. That corpus validation is the
  authoritative structural/policy/security gate before any PR is considered ready.
- Add real upload claim/process/reject/archive queue movement only after the queue mutation contract is
  explicitly enabled.
- Add PR maintenance actions for owner comments, requested changes, failed checks, and stale/blocked
  PRs after the edit/comment execution contract is enabled.
- Add the production launcher outside the Curator worker: an owner-controlled `systemd` timer or
  equivalent VPS operator wrapper that invokes sandbox-broker with the Curator template and task
  contract. Curator is not an always-on daemon, YKM serving does not launch it, and broker services do
  not decide when it should run.

## Completion Audit

- Safety boundary: satisfied. No production code or docs were added under `POC/`; the Curator does
  not use direct GitHub tokens, provider keys, merge/deploy/index rebuild operations, queue moves, or
  live model calls.
- Deterministic controller: satisfied for the contracted initial manual workflow. It validates task
  contracts, locks runs, freezes feedback windows, plans feedback/uploads, reconciles fixture and
  opt-in broker-read snapshots, applies state-only safe decisions, and writes JSON/Markdown reports.
- Broker boundary: satisfied for reads and preflight. Live reads are opt-in and authenticated through
  broker agent credentials only. Mutations are represented as policy-checked intents or fixture
  simulations until a future execution contract enables them.
- Model boundary: satisfied for health, budgets, and typed fixture validation. Live model planning is
  intentionally closed until a future model execution contract enables it.
- Upload queue mutation: intentionally deferred by contract. Upload plans and reconciliation previews
  do not move queue directories.

## Next Implementation Targets

1. Wire the VPS Curator launcher: sandbox-broker template plus an owner-controlled `systemd` timer or
   manual wrapper that invokes sandbox-broker and collects `/output/run-report.json`.
2. Define the future broker mutation execution contract for PR/issue creation, comments, branch
   edits, idempotency reuse, and budget-denial persistence.
3. Implement upload-review observe for draft corpus changes: sandbox must provide `mise`, `uv`, and
   Python, apply the proposed markdown/policy patch to `ykmcorpus`, run `mise run validate`, and
   record pass/fail output in the Curator report.
4. Define the future model execution contract for feedback planning, upload review, PR comment
   classification, and PR body drafting.
5. Define the future queue movement contract before any upload directory moves are enabled.
