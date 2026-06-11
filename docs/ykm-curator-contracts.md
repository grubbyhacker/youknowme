# YouKnowMe Curator Contracts

Status: initial Phase 4 contract draft.

These contracts define the first manual Curator workflow. They are intentionally filesystem- and
JSON-oriented so the deterministic controller can be tested before model or GitHub mutations are
enabled.

Curator implementation code lives in this repository in its own sibling Python package namespace:
`src/curator`. Shared serve-side contracts may still be imported from `ykm.contracts`; Curator-only
state machines, task models, broker/model adapters, and CLI code belong in `curator`.

## Runtime Paths

The Curator reads evidence and writes only Curator-owned state:

```text
/data/intake/uploads/pending/
/data/intake/uploads/claimed/
/data/intake/uploads/processed/
/data/intake/uploads/rejected/
/data/intake/uploads/archive/
/data/intake/uploads/deferred/
/data/intake/feedback/feedback.jsonl
/data/intake/feedback/curator-state.json
/data/intake/feedback/curator-decisions.jsonl
/data/intake/feedback/runs/<run_id>/feedback-plan.json
/data/intake/uploads/runs/<run_id>/upload-plan.json
/data/intake/curator-run.lock
/output/run-report.json
/output/run-report.md
```

`archive/` is the directory name. `archived` is the logical terminal state recorded in metadata.

## Task Contract

The Curator task payload is a JSON object:

```json
{
  "schema_version": "1",
  "run_id": "cur_20260608T120000Z_abcd1234",
  "mode": "dry_run",
  "enabled_actions": ["reconcile", "plan_feedback", "plan_uploads"],
  "upload_ids": [],
  "github_mutation_budget": {
    "max_new_objects_per_run": 4,
    "upload": 2,
    "feedback": 2
  },
  "model_call_budget": {
    "max_calls_per_run": 0,
    "max_tokens_per_run": 0
  },
  "model_feedback_planning": false,
  "feedback_model": null,
  "model_upload_review": false,
  "upload_review_model": null,
  "pr_repair_executor": null,
  "pr_repair_model": "ykm-codex-gpt-5-mini",
  "pr_repair_max_per_run": 1,
  "pr_repair_validation_command": ["mise", "run", "validate"],
  "feedback_soft_action_threshold": 10,
  "stale_lock_timeout_seconds": 7200
}
```

When launched by sandbox-broker, `/input/task.json` is broker-owned and contains the generic
broker task wrapper. Curator-specific task JSON is embedded as the wrapper's `task` string and parsed
by the Curator worker. Local development and tests may still point `curator run --task` or
`curator inspect-task` directly at the raw Curator task JSON above. Worker-specific config must not
replace or mount over `/input/task.json`. For fixed launch profiles, the embedded Curator JSON may
use `"run_id":"${SANDBOX_RUN_ID}"`; Curator replaces that placeholder with the broker wrapper
`run_id` before validation.

Initial modes:

- `dry_run`: read evidence and emit plans/reports only.
- `state_only`: update Curator state without GitHub or model mutations.
- `manual_live`: allow policy-validated GitHub/model operations through broker/proxy boundaries.

`upload_ids` optionally scopes `plan_uploads` to specific staged uploads. The default empty list
preserves whole-queue planning. When the list is non-empty, the Curator only creates upload-review
previews for matching reviewable bundles; if any requested upload is absent or not reviewable, the
run fails closed before upload model review or PR execution.

`model_feedback_planning` is an explicit opt-in for replacing the deterministic feedback planner's
proposed actions with a single model-planned feedback action set. It requires a non-empty
`feedback_model` and a `model_call_budget.max_calls_per_run` of at least `1`. The Curator still
reads evidence locally, sends model calls only through `gh-agent-proxy`, validates the returned
action contract, and rejects any model action that cites evidence identifiers outside the current
feedback window. With the default `model_feedback_planning: false` and zero model budget, feedback
planning is fully deterministic.

`model_upload_review` is an explicit opt-in for sending included upload-review previews to the
upload-review model. It requires a non-empty `upload_review_model`, one remaining
`model_call_budget.max_calls_per_run` entry per included upload preview, and a configured corpus
checkout path supplied to the worker, for example through `curator run --corpus-checkout` or
`YKM_CORPUS_CHECKOUT`. Integrated model drafts are applied only to a temporary checkout copy. The
model may propose additive `policy_patch` changes for `corpus_roots`, `allowed_types`, and
`allowed_tags`; this is the preferred way to ask owner permission for a bounded schema expansion,
because the resulting PR is reviewable and rejectable. The Curator then runs `mise run validate` in
that copy and records a structured observation with status, command, exit code, bounded
stdout/stderr tails, draft paths, and policy additions. Failed observations fail the run and
increase `validation_failure_count`; skipped non-integrated model decisions are reported but do not
apply draft files. `needs_owner_action` should be reserved for uploads that cannot be turned into a
small reviewable markdown and policy diff from the supplied context.

`repair_prs` is an explicit `enabled_actions` value for maintaining open Curator-authored PRs after
owner feedback. It requires `reconcile` plus `pr_repair_executor`. The `fixture` executor is for
tests only. The `codex_proxy` executor clones the existing Curator branch through broker Git, writes
a task-local Codex config, runs `codex exec` against the scoped Codex proxy, validates the changed
checkout with `pr_repair_validation_command`, and records `pr_repair_results`. In `dry_run`, a
validated repair is reported but not pushed. In `manual_live`, a validated repair is committed and
pushed back to the existing Curator branch. The Curator remains responsible for selecting actionable
PRs, validation, branch ownership, and push decisions.

After a successful `manual_live` repair push, the Curator must post a PR conversation comment that
says the repair is complete and owner review is needed again. The comment must summarize what was
fixed, why the original PR broke, and what guard prevents the same class of failure from silently
repeating. It then dismisses addressed stale `CHANGES_REQUESTED` reviews, resolves addressed review
threads, adds `ym-curator: waiting-review`, and removes `ym-curator: needs work`. Labels are routing
metadata only; they are not sufficient GitHub-facing communication.

Codex PR repair must not push `.github/workflows/*` edits unless the Curator GitHub App installation
has explicit workflow write permission. With the current least-privilege App scope, workflow changes
are reported as rejected repair results so repository-maintenance fixes can be handled separately.

For upload review only, `manual_live` can now turn passing integrated observations into review PRs.
The Curator clones `ykmcorpus` through the broker Git remote, creates the deterministic
`curator/<run_id>/...` branch, reapplies the validated model draft, runs `mise run validate` again in
that exact checkout, commits, pushes through broker credentials, and opens a PR through broker
`pull.create` with Curator metadata. It does not move upload queue directories or write bundle
`curator.json` as part of PR creation. Feedback issue/PR execution and PR maintenance writes remain
guarded until their execution contracts are enabled.

The package also exposes native CLI entrypoints as `curator run`, `curator inspect-task`, and
`curator inspect-report`. `inspect-task` validates a task contract without acquiring the run lock or
touching intake. `inspect-report` validates an existing `run-report.json` and prints a compact
operator summary without rerunning Curator or touching intake, including checkpoint offsets,
validation counts, partial failure names, mutation counts, simulation counts, and model usage
counts, plus PR reconciliation counts when present. The legacy `ykm-curator-dry-run` entrypoint
remains a compatibility wrapper around the same runner.

## Upload Metadata

Each claimed upload gets `curator.json`:

```json
{
  "schema_version": "1",
  "upload_id": "upl_...",
  "state": "pr_opened",
  "decision": "integrated",
  "run_id": "cur_...",
  "branch": "curator/cur_.../upload-slug",
  "pr_number": 123,
  "issue_number": null,
  "blocking_issue_number": null,
  "claimed_at": "2026-06-08T12:00:00Z",
  "last_checked_at": "2026-06-08T12:00:00Z",
  "last_action_at": "2026-06-08T12:00:00Z",
  "reentry_trigger": null,
  "retry_after": null,
  "blocking_reason": null,
  "notes": "Curated into procedures/home/example.md"
}
```

Valid logical states are `pending`, `claimed`, `pr_opened`, `deferred`, `rejected`, `processed`, and
`archived`. Terminal bundles are retained indefinitely in `archive/` for initial scope.

Allowed logical transitions are:

```text
pending -> claimed
claimed -> pr_opened
claimed -> deferred
claimed -> rejected
pr_opened -> processed
pr_opened -> deferred
pr_opened -> rejected
processed -> archived
rejected -> archived
deferred -> claimed
```

The initial implementation exposes pure transition validation helpers only. They do not move queue
directories or write `curator.json` until queue mutation code is explicitly enabled.

`upload-plan.json` records deterministic upload planning before model-backed upload review exists:

```json
{
  "schema_version": "1",
  "run_id": "cur_...",
  "included_upload_ids": ["upl_1"],
  "review_previews": [
    {
      "upload_id": "upl_1",
      "queue": "pending",
      "action_id": "upl_act_1",
      "idempotency_key": "upload:...",
      "current_state": "pending",
      "proposed_state": "claimed",
      "branch": "curator/cur_.../upload-upl-1-...",
      "validation": "accepted",
      "reason": "Deterministic upload review preview only; no queue move or curator.json write.",
      "draft_status": "corpus_pr_candidate",
      "draft_paths": ["homemaint/example.md"],
      "blocking_reason": null,
      "warnings": []
    }
  ],
  "proposed_actions": [
    {
      "action_id": "upl_act_1",
      "action_type": "defer",
      "classification": "upload_review_pending",
      "idempotency_key": "defer:...",
      "evidence": {
        "feedback_ids": [],
        "upload_ids": ["upl_1"],
        "source_ids": [],
        "section_ids": [],
        "result_ids": []
      },
      "target_repo": null,
      "validation": "accepted",
      "execution": "not_executed"
    }
  ],
  "created_at": "2026-06-08T12:00:00Z"
}
```

The deterministic skeleton may include upload bundles from `pending`, `claimed`, and `deferred`
when their `curator.json` state is absent or non-terminal. It must not move queue directories or
write bundle metadata in dry-run or state-only upload planning.
Review previews describe the logical transition and future Curator branch/idempotency metadata that
would be used by a later broker-backed upload review. They are plan data only and do not claim,
move, reject, archive, or otherwise mutate upload bundles.
They also include a deterministic draft-readiness classification. `corpus_pr_candidate` means the
uploaded markdown already has frontmatter that can be normalized into current corpus policy and the
preview lists the proposed corpus paths. `needs_owner_action` means the upload is well-formed intake
but cannot safely become a corpus PR without owner input, such as missing frontmatter or unsupported
document type. Unsupported tags may be dropped in the proposed draft and reported as warnings so the
operator can review the normalization before any live PR is opened.

When deferred upload metadata exists, deterministic upload planning re-enters it only after a
satisfied trigger. `reentry_trigger: "next_run"` is ready immediately, and
`reentry_trigger: "retry_after"` is ready only once `retry_after` has passed. Deferred uploads blocked
on unresolved owner input remain in place until later issue/PR reconciliation records a ready trigger.
Older deferred bundles without `curator.json` are still counted and may receive deterministic review
placeholders during early discovery. Upload metadata with `reentry_trigger: "retry_after"` must
include a `retry_after` timestamp.

If a bundle has `manifest.json`, it must be parseable JSON with a non-empty `upload_id`. Malformed
manifests are reported in the bundle snapshot and run report, count as validation failures, and are
excluded from deterministic upload-review placeholders. Missing manifests remain non-fatal during
early queue discovery so old or hand-built fixtures can still be counted.
When both `manifest.json` and `curator.json` are present, their `upload_id` values must match.
Mismatches are reported as invalid upload metadata and excluded from deterministic upload-review
placeholders.

## Feedback State

`curator-state.json` records the feedback checkpoint and run history:

```json
{
  "schema_version": "1",
  "last_completed_run_id": "cur_...",
  "feedback_checkpoint": {
    "path": "feedback/feedback.jsonl",
    "byte_offset": 12345
  },
  "updated_at": "2026-06-08T12:00:00Z"
}
```

Each run freezes a feedback window before planning. Feedback appended after the frozen end offset
belongs to the next run.

Malformed feedback lines inside the frozen window do not invalidate the whole batch. The Curator
plans from valid records, reports bad lines as bounded `input_errors`, counts them as validation
failures, and leaves the run in failure status for operator review.

`feedback-plan.json` records batch-level agency:

```json
{
  "schema_version": "1",
  "run_id": "cur_...",
  "feedback_window": {
    "start_offset": 100,
    "end_offset": 12345
  },
  "included_feedback_ids": ["fb_1"],
  "reentered_feedback_ids": [],
  "referenced_upload_ids": [],
  "referenced_source_ids": [],
  "referenced_section_ids": [],
  "referenced_result_ids": [],
  "soft_action_threshold": 10,
  "capacity_deferred_feedback_ids": [],
  "proposed_actions": [
    {
      "action_id": "act_1",
      "action_type": "issue",
      "classification": "owner_action",
      "idempotency_key": "issue:owner_action:fb_1",
      "evidence": {
        "feedback_ids": ["fb_1"],
        "upload_ids": [],
        "source_ids": [],
        "section_ids": [],
        "result_ids": []
      },
      "target_repo": "grubbyhacker/ykmcorpus",
      "validation": "accepted",
      "execution": "not_executed"
    }
  ],
  "created_at": "2026-06-08T12:00:00Z"
}
```

Allowed initial action types are `no_action`, `issue`, `corpus_pr`, `link_to_upload`, and `defer`.
Every action must cite evidence and carry a stable idempotency key based on action type plus durable
evidence identifiers, not model wording. Evidence may include feedback IDs, upload IDs, source IDs,
section IDs, and result IDs.

Before model-backed planning is enabled, the deterministic planner may emit placeholder proposed
actions from feedback category and durable pointers only. It must skip feedback with a current
terminal decision, may re-enter `deferred` and `capacity_deferred` decisions, and must leave
execution as `not_executed`. Free-form feedback comments are evidence for future review, but they
must not change deterministic action type, target repository, mutation budget, or policy allowlists.
When the soft action threshold is exceeded, the plan records the threshold and the feedback IDs that
were capacity-deferred so the operator can review the cap outcome without interpreting action text.

The deterministic planner may group multiple feedback records into one proposed action only when
they share a durable target, such as the same `source_id` for corpus placeholders or the same
`upload_id` for upload-linked feedback. Grouped actions must preserve every contributing
`feedback_id` in action evidence and regenerate the idempotency key from the combined evidence.

Feedback-driven `corpus_pr` placeholders require a durable corpus target: `source_id`, `section_id`,
or upload linkage. Untargeted missing/wrong/stale/unclear feedback must be downgraded to a private
issue placeholder or another non-PR disposition rather than becoming a speculative corpus PR.

In `state_only`, the deterministic skeleton may append decisions only for actions that require no
external mutation: `no_action_*` dispositions, `linked_to_upload`, and `capacity_deferred`.
Placeholder `issue` and `corpus_pr` actions remain proposed but undecided until broker/model-backed
execution exists.
If a run has validation failures from malformed feedback records, invalid upload metadata, malformed
manifests, or branch collisions, `state_only` must still write bounded plans/reports but must not
append decisions, update upload metadata, or advance the feedback checkpoint.
When deterministic broker/model preflight fixtures are configured, their failing branch,
idempotency, reachability, or budget checks are evaluated before `state_only` commits and likewise
prevent decision appends, upload metadata updates, and checkpoint advancement.
If a state-only decision append fails, the run must report a `feedback-decision-append` partial
failure and must not advance the feedback checkpoint.
If writing `curator-state.json` fails, the run must report a `curator-state-write` partial failure
and must leave `checkpoint_advanced` false.
If writing a feedback or upload plan artifact fails, the run must report `feedback-plan-write` or
`upload-plan-write` as a partial failure and continue to emit the run report when the report output
path remains writable.

Feedback re-entry is driven by the latest decision record, not only by the current feedback byte
window. `capacity_deferred` feedback is ready for the next run and receives
`reentry_trigger: "next_run"` when appended by state-only planning. `deferred` feedback re-enters
only when its latest decision carries a satisfied trigger, such as `reentry_trigger: "retry_after"`
with a `retry_after` timestamp that has passed. Deferred owner-input work without a satisfied trigger
remains behind the checkpoint until a later reconciliation decision makes it ready. Feedback
decisions with `reentry_trigger: "retry_after"` must include a `retry_after` timestamp.

## Feedback Decisions

`curator-decisions.jsonl` is append-only. The latest decision for a `feedback_id` is current; if two
records have the same timestamp, the later JSONL line wins.

```json
{
  "schema_version": "1",
  "feedback_id": "fb_1",
  "run_id": "cur_...",
  "plan_action_id": "act_1",
  "decision": "issue_opened",
  "pr_number": null,
  "issue_number": 42,
  "source_id": null,
  "section_id": null,
  "upload_id": null,
  "reentry_trigger": null,
  "retry_after": null,
  "reason": "Owner clarification is required before corpus edit.",
  "timestamp": "2026-06-08T12:00:00Z"
}
```

Decision values are `no_action_positive`, `no_action_non_actionable`, `no_action_duplicate`,
`no_action_superseded`, `no_action_insufficient_evidence`, `issue_opened`, `pr_opened`,
`linked_to_upload`, `deferred`, and `capacity_deferred`.

## Locking And Branches

The manual Curator uses `/data/intake/curator-run.lock` as a single-flight lock. A live lock causes
the run to exit before planning. A stale lock is older than 2 hours and requires an explicit recovery
mode before removal.

Until broker issue `#27` is fixed, branch names must be globally unique per action and preflighted
against existing branches plus existing PR/issue markers before pushing:

```text
curator/<run_id>/<action-type>-<evidence-slug>-<short-id>
```

Before broker branch queries are enabled, the deterministic skeleton performs an offline preflight
against Curator branches already recorded in upload `curator.json` metadata and reports collisions as
validation failures. This includes both feedback action branch previews and upload review preview
branches.

## Reports

Every run writes `/output/run-report.json` and `/output/run-report.md`. Reports must include:

- run id, mode, effective enabled actions, status, and timestamps;
- feedback window and checkpoint advancement;
- upload queue counts;
- upload review preview counts;
- upload review validation observations when model upload review is enabled;
- explicit referenced upload/source/section/result ID lists where present;
- PR reconciliation summaries when PR snapshots are supplied;
- proposed and executed action counts;
- GitHub mutations, capacity deferrals, and validation failures;
- bounded input errors for malformed feedback or other input records;
- model call counts, token usage, and budget exhaustion when available;
- partial failure details bounded enough for operator review.

## PR Maintenance State

The implementation exposes pure validation for the documented Curator PR states and transitions:
`open_waiting_review`, `changes_requested`, `commented_needs_triage`, `checks_failed`,
`checks_missing`, `ready_for_owner`, `merged`, `closed_unmerged`, and `stale_or_blocked`. This
validation does not read GitHub or mutate PRs yet; live PR reconciliation remains behind broker
read/write contracts.

Offline PR snapshot reconciliation is available for fixture and broker-read data. Given
broker-supplied PR snapshots, the Curator can parse markers, identify Curator PRs by marker or
`curator/` branch prefix, classify the PR state, and include bounded PR reconciliation summaries in
the run reconciliation model. Broker fixtures may include `pr_snapshots` to exercise this path in
dry runs. When `--enable-broker-reads` is set with `--broker-url`, the runner may also read live PR
and issue snapshots through the broker using `BROKER_AGENT_ID` and `BROKER_AGENT_SECRET`; it never
receives direct GitHub tokens.
PR snapshots include labels, review IDs, and review-thread IDs. Because GitHub Apps cannot be PR assignees, the label
`ym-curator: needs work` is the explicit human reassignment signal for open Curator PRs. If present,
reconciliation classifies the PR as actionable by the Curator. The label
`ym-curator: waiting-review` is the post-repair handoff signal; reconciliation classifies that PR as
`ready_for_owner` even when GitHub still reports an older `CHANGES_REQUESTED` review decision. If
expected validation checks are absent for a Curator PR, reconciliation classifies the PR as
`checks_missing` rather than treating an unknown status as healthy.
PR reconciliation summaries include per-state counts for operator triage. When a terminal PR
snapshot references an upload, reconciliation may also emit an upload transition preview such as
`pr_opened -> processed` for merged PRs or `pr_opened -> deferred` for closed-unmerged PRs. These
previews are validation-only; they do not move queue directories or write `curator.json`.
If a Curator PR is discovered by branch prefix but its body markers are missing or incomplete,
reconciliation may match it back to local upload metadata by `pr_number` or branch, and to feedback
decisions by `pr_number`, before emitting terminal previews.
When a terminal PR snapshot references feedback IDs, reconciliation may also emit feedback decision
previews. Merged Curator PRs preview `pr_opened`; closed-unmerged Curator PRs preview `deferred`.
Dry runs keep these previews validation-only. In `state_only`, accepted previews whose latest
decision does not already match may be appended to `curator-decisions.jsonl` as reconciliation
decisions. If a current feedback decision is an unrelated terminal disposition, the preview is
rejected rather than silently overwriting local history.
Broker fixtures may also include `issue_snapshots`. Closed issue snapshots can satisfy
`owner_input_resolved` re-entry triggers in validation-only reports: deferred feedback gets a
feedback re-entry preview, and deferred uploads with matching `blocking_issue_number` preview
`deferred -> claimed`. These previews do not append feedback decisions, write `curator.json`, or move
upload queue directories in dry runs. In `state_only`, accepted feedback re-entry previews may append
a reconciliation decision with `reentry_trigger: "next_run"` so the next run can plan the now-unblocked
feedback. Accepted upload transition previews may update an existing bundle `curator.json` in place,
but they still do not move queue directories. Queue movement remains disabled until explicitly
enabled. Failed metadata updates are reported as `upload-metadata-update` partial failures instead of
silently disappearing.

The implementation also exposes pure marker rendering and parsing helpers for future PR bodies and
comments. Marker blocks use stable `YKM-Curator-*` lines, including `YKM-Curator-Run`,
`YKM-Curator-Action`, `YKM-Curator-Action-Type`, `YKM-Curator-Action-ID`,
`YKM-Curator-Idempotency-Key`, and repeated evidence markers for upload, feedback, source, section,
and result IDs. `YKM-Curator-Action` is the broker-required scope (`upload`, `feedback`, or
`maintenance`); `YKM-Curator-Action-Type` is the Curator plan action type such as `issue` or
`corpus_pr`. Marker parsing ignores non-marker body text, deduplicates repeated evidence markers,
and remains compatible with legacy marker blocks that stored the plan action type in
`YKM-Curator-Action`.

Deterministic body-draft helpers may generate bounded issue/PR body scaffolds before model-backed
drafting is enabled. These drafts cite evidence identifiers and marker blocks only; they must not
copy private corpus, intake, upload, feedback, or log excerpts into GitHub bodies.

## Adapter And Policy Preflight

Curator adapter interfaces live under `src/curator` and are provider-neutral. Broker and model
adapters must hide direct GitHub/provider credentials from Curator code. Until adapters are enabled,
`manual_live` performs deterministic policy preflight only.

The HTTP broker adapter performs a bounded `/healthz` reachability probe and can generate read-only
broker preflight request descriptors for planned GitHub-object work. For `pull.create` intents it
describes the PR-list read needed to check proposed branch reuse. For all GitHub-object intents it
describes the issue/PR marker search needed to check idempotency keys. These descriptors are emitted
in `broker-preflight` probe details with status `skip`. The Markdown run report renders these
descriptors under `Broker Read Preflight` for manual review.

When reconciliation is enabled and an HTTP broker URL is configured, the runner may also emit
`broker-pr-read-preflight` descriptors for the broker reads needed by the future PR maintenance loop.
These include Curator PR discovery by branch prefix and durable markers, plus per-PR detail reads for
comments, reviews, review comments, review threads, commit status, and check runs when PR numbers are
already known from fixture snapshots. These descriptors are validation-only; live broker read
execution happens only when `--enable-broker-reads` is explicitly set. With broker reads enabled, the
runner reads Curator PRs by durable marker and `curator/` branch prefix, reads review/review-thread
and status/check data needed for classification, and emits `broker-pr-read` probe results. Broker
read failures fail closed and do not advance state in `state_only`.
Known issue numbers from feedback decisions and upload metadata may also produce
`broker-issue-read-preflight` descriptors for issue state and comments. These descriptors support the
future owner-input reconciliation loop without giving the Curator direct GitHub access. With broker
reads enabled, known issue numbers are read through the broker and converted into issue snapshots for
owner-input re-entry reconciliation.

When upload planning is enabled and upload review previews exist, the runner may emit
`broker-upload-read-preflight` descriptors for the read-only branch and idempotency checks needed
before a future broker-backed upload PR can be opened. These descriptors do not execute live reads or
create branches.

The HTTP model proxy adapter performs bounded `/healthz` reachability probes and typed model calls
through `/v1/model/call`. It must not receive provider API keys. `model_proxy_url` may be the proxy
service base URL, `/v1/model/call`, or for Codex proxy probing the OpenAI-compatible `/v1` base;
health probing maps endpoint paths back to service `/healthz`. Codex PR repair uses the same proxy
token boundary but invokes Codex directly with `OPENAI_API_KEY` set only inside the subprocess.

Offline fixture adapters are supported for tests and local dry verification. `--broker-fixture` and
`--model-proxy-fixture` point at JSON files with the same schema version as the Curator contracts.
They simulate reachability, allowed broker operations, existing branches, and model proxy budgets
without making network calls or mutations.
Broker fixtures may also list existing idempotency keys from PR/issue markers so proposed GitHub
object operations can be deduped offline before live broker execution exists. Upload review previews
are also checked against fixture `existing_branches` and `existing_idempotency_keys` before future
broker-backed upload PR work is enabled.
Model proxy fixtures may include typed responses keyed by task name. Fixture responses validate
against the Curator model-call response contract and are for offline tests only; the runner still
makes zero model calls until a model-backed planning step is explicitly enabled.
Fixture response map keys must match the embedded response `task_name`; mismatches are contract
errors so future planning code cannot accidentally consume a response under the wrong task contract.
The fixture model adapter exposes typed response validation for tests, so a fixture response must
pass both the generic model-call envelope and the task-specific output contract before it can be used
by future planning code.
The Curator also defines typed output contracts for future feedback planning, upload review, PR
comment classification, and PR body drafting tasks. Fixture or proxy responses must be validated
against those task-specific Pydantic models before any output can influence plans, PRs, issues, or
state. These validation helpers do not enable live model planning by themselves.
When a task requests a nonzero model-call or token budget, model proxy fixtures must advertise equal
or larger limits. Otherwise the run fails closed with a `model-budget` preflight failure before any
model call can be attempted, and `model_budget_exhausted` is set in the run report.

Guarded `manual_live` runs that contain proposed GitHub-object actions (`issue` or `corpus_pr`),
upload review previews, or `codex_proxy` PR repair must also prove the broker boundary is configured.
Non-mutating actions do not require a broker probe.
Guarded `manual_live` runs with a non-zero `model_call_budget.max_calls_per_run` must prove the
model proxy boundary is configured.

The initial offline execution policy validates:

- issue targets are in the explicit issue allowlist;
- corpus PR targets are in the explicit PR allowlist;
- new GitHub object proposals fit the per-run and feedback mutation budgets;
- non-mutating actions such as no-op and defer do not consume GitHub mutation budget.

Policy denials appear in the run report. They do not create issues, PRs, branches, queue moves, or
model calls.

Policy-allowed GitHub-object actions may also appear as execution intents in the run report. An
execution intent is a broker-ready operation descriptor such as `issue.create` or `pull.create` with
the action id, idempotency key, target repo, branch when applicable, durable evidence pointers, and
bounded deterministic title/body previews. Issue intents also include deterministic labels and
assignees where the Phase 4 routing policy defines them. Owner-action feedback issues carry
`ykm-curator`, `feedback`, and `needs-owner-input`, and are assigned to `grubbyhacker` by default.
Upload-review `pull.create` intents may be executed in `manual_live` after corpus validation passes;
other execution intents remain `not_executed` until their live adapters are enabled.

`--simulate-execution` is an offline test aid. It requires `--broker-fixture` and records simulated
execution results in the run report without changing GitHub, queues, corpus files, feedback
decisions, or Curator state.
