# YouKnowMe Curator Implementation Status

Status: single live Curator runner path implemented and covered locally; production profile wiring
is owned by the deployment repository.

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
- Feedback grouping by durable source and section evidence.
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
- HTTP model proxy health probing and typed model calls through `gh-agent-proxy`; Codex PR repair
  uses the OpenAI-compatible `/v1` proxy surface with a scoped proxy token.
- Model fixture budget preflight and typed fixture response validation.
- Upload-review model evals with sanitized dev-environment and hot-tub/manual scenarios. Live eval
  evidence currently supports Sonnet for the first upload-review implementation.
- Bounded upload-review observe for integrated model drafts. The Curator applies model-produced
  markdown and additive type/tag policy changes to a temporary corpus checkout copy, runs
  `mise run validate`, and records structured pass/fail observations in JSON and Markdown reports.
- Broker-backed upload review PR creation for `manual_live` runs after validation passes. The worker
  clones through the broker Git remote, reapplies the draft, re-runs corpus validation, pushes a
  deterministic Curator branch, and opens a broker `pull.create` PR with Curator metadata.
- Agentic feedback processing for `manual_live` runs with `feedback_executor: "codex_proxy"`,
  including broker issue creation for issue outcomes and Codex-backed corpus PR creation for PR
  outcomes.
- Opt-in PR repair action for open Curator PRs. `repair_prs` can use a fixture executor in tests or
  `codex_proxy` to run `codex exec` in a broker-cloned checkout, validate the diff, and in
  `manual_live` push back to the existing Curator branch.
- Combined `manual_live` runs can reconcile PR state, process feedback, process one upload, and
  repair one Curator PR in a single report using the production `codex_proxy` executor fields.
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

- Full suite: 243 passed.
- Lint: passed.
- Full suite has one existing Starlette/httpx deprecation warning in `tests/test_server.py`.

## Contract-Blocked Or Future Work

- Broker-backed GitHub reads for PR and issue reconciliation are implemented behind explicit
  `--enable-broker-reads` opt-in. They remain read-only and separate from mutation execution.
- Broker-backed upload review PR creation is enabled only for validated `manual_live` upload-review
  observations. Feedback issue/PR creation and PR repair handoff mutations are enabled only through
  task-explicit `codex_proxy` executor settings plus broker/proxy preflight.
- Model-backed feedback planning and upload-review observe remain explicit opt-ins. Offline and
  live-proxy eval harnesses exist; production live model calls require a future planning execution
  contract.
- Upload-review PR creation does not move upload queue directories or write upload `curator.json`.
  Reconciliation can still discover the PR later from Curator markers and branch naming.
- Add real upload claim/process/reject/archive queue movement only after the queue mutation contract is
  explicitly enabled.
- Add broader PR maintenance actions for owner comments, failed checks, and stale/blocked PRs after
  their edit/comment execution contracts are explicitly enabled.
- Add or update the production launcher outside the Curator worker so the owner-controlled timer
  invokes sandbox-broker profile `ykm-curator-live` with the combined live task contract. Curator is
  not an always-on daemon, YKM serving does not launch it, and broker services do not decide when it
  should run.

## Completion Audit

- Safety boundary: satisfied. No production code or docs were added under `POC/`; the Curator does
  not use direct GitHub tokens, provider keys, merge/deploy/index rebuild operations, queue moves, or
  live model calls.
- Deterministic controller: satisfied for the contracted initial manual workflow. It validates task
  contracts, locks runs, freezes feedback windows, plans feedback/uploads, reconciles fixture and
  opt-in broker-read snapshots, applies state-only safe decisions, and writes JSON/Markdown reports.
- Broker boundary: satisfied for reads, preflight, upload PR creation, feedback issue/PR execution,
  and PR repair handoffs when the task explicitly enables the relevant executor. Live reads are
  opt-in and authenticated through broker agent credentials only. Mutations use broker Git and broker
  mutation surfaces, not direct GitHub tokens.
- Model boundary: satisfied for health, budgets, and typed fixture validation. Live model planning is
  intentionally closed until a future model execution contract enables it.
- Upload queue mutation: intentionally deferred by contract. Upload plans and reconciliation previews
  do not move queue directories.

## Next Implementation Targets

1. Wire the VPS Curator launcher to the single `ykm-curator-live` sandbox-broker profile and update
   the timer principal to allow only that live profile plus dry/state diagnostics.
2. Define any remaining broker mutation execution contracts for broader PR maintenance, branch
   edits, idempotency reuse, and budget-denial persistence.
3. Define the future model execution contract for feedback planning, upload review, PR comment
   classification, and PR body drafting.
4. Define the future queue movement contract before any upload directory moves are enabled.
