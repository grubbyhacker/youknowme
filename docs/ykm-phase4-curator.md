# YouKnowMe Phase 4 Curator Plan

Status: planning draft.

Phase 4 introduces The Curator: a separate, minimum-privilege agent that processes YouKnowMe intake,
maintains its own proposed corpus PRs, and files GitHub issues when human or cross-repo follow-up is
better than a corpus edit.

This document is a plan, not an implementation. It captures the intended shape after Phase 3 staged
intake and before any Curator runtime is built.

## Goals

The Curator's first useful job is intake triage, not broad autonomous corpus maintenance.

Phase 4 v1 should:

- Drain staged upload bundles from the YKM intake queue.
- Review actionable feedback records and turn them into corpus PRs or GitHub issues.
- Maintain Curator-authored open PRs, including responding to owner review feedback.
- Preserve the spine rule: YKM remains passive and never writes to its own corpus.
- Keep credentials and authority separated: the Curator proposes, GitHub records, the owner merges.
- Build the Curator as an SDK-first agent system so we learn the mechanics of typed agent
  development rather than delegating the whole workflow to an existing coding agent shell.

Phase 4 v1 should not:

- Automatically merge PRs.
- Rebuild or redeploy the live YKM index.
- Serve staged intake content directly.
- Make proactive cleanup PRs without an upload or feedback trigger.
- Run as an always-on daemon before the manual workflow is understood.
- Depend on a specific model provider or subscription entitlement as a core architectural fact.

## Current Inputs

YKM already exposes two staged write paths:

- `upload` writes bounded markdown bundles under `/opt/youknowme/intake/uploads/pending`.
- `feedback` appends bounded JSONL observations under
  `/opt/youknowme/intake/feedback/feedback.jsonl`.

The Phase 3 queue contract already reserves these upload directories:

```text
/data/intake/uploads/pending/
/data/intake/uploads/claimed/
/data/intake/uploads/processed/
/data/intake/uploads/rejected/
/data/intake/uploads/archive/
```

Phase 4 should extend the practical queue contract with:

```text
/data/intake/uploads/deferred/
/data/intake/feedback/curator-state.json
/data/intake/feedback/curator-decisions.jsonl
/data/intake/feedback/runs/<run_id>/feedback-plan.json
```

`deferred` is for upload bundles that are not unsafe, but require owner input before a useful PR can
be created.

The feedback schema should be extended additively with:

- `needs_owner_action`
- `positive_content`
- `non_actionable`

Existing feedback categories remain valid:

- `missing_content`
- `wrong_content`
- `stale_content`
- `unclear_content`
- `agent_note`

`needs_owner_action` is for durable facts, questions, or decisions that require Roger's future
attention before the Curator can finish or improve corpus work. It is an intake category, not a
Curator state. A record in this category will often lead to a GitHub issue and may also cause related
upload or feedback work to become `deferred` when the missing owner input is essential.

`agent_note` remains valid, but should not be interpreted as an unrestricted scratchpad. In Curator
triage, broad or repetitive agent notes should be clustered, superseded, or marked non-actionable
when they do not describe a durable content, product, or workflow concern.

## Runtime Architecture

The Curator should be a deterministic controller with bounded agent calls, not one large prompt.

Recommended shape:

```text
curator CLI
  -> deterministic scheduler/state machine
  -> intake/log readers
  -> broker client
  -> typed agent decision calls
  -> optional edit executor
```

The controller owns:

- Run ordering.
- Queue claims.
- PR discovery.
- State transitions.
- Idempotency.
- Broker calls.
- Persistence.
- Retry behavior.
- Policy enforcement.

The agent layer owns bounded reasoning tasks:

- Plan over feedback batches since the previous run.
- Cluster related feedback records and split complex records into multiple proposed actions.
- Evaluate an upload bundle.
- Choose corpus path/frontmatter/headings/tags.
- Draft PR text.
- Interpret owner PR feedback.
- Propose a patch strategy.
- Draft issue summaries.

The editor/executor layer may start simple and become pluggable:

- `deterministic`: simple file placement and metadata edits.
- `sdk_agent`: provider-neutral structured LLM calls inside the Curator.
- `hermes` or `codex`: optional subordinate executor for complex branch edits or review iteration.

Hermes or Codex may be useful as a worker, but they should not own the durable queue or PR lifecycle.
The durable truth should live in intake metadata, branch names, PR bodies, PR comments, and GitHub
state.

## Agent SDK Direction

Phase 4 v1 should be SDK-first and provider-neutral.

Implementation should live in this repository initially because the Curator is part of the
YouKnowMe lifecycle and should be tested against the YKM intake contracts.

The first implementation should use:

- Python.
- Pydantic models for state, task contracts, and structured LLM outputs.
- A small model adapter interface, rather than hard-coding one provider.
- Either Pydantic AI or the OpenAI Agents SDK for the first agent layer.

Pydantic AI is attractive because typed structured output and durable-execution integrations map well
to this workflow. OpenAI Agents SDK is attractive because tools, guardrails, handoffs, and tracing are
first-class. The plan should not require either one until a short implementation spike compares them
against the actual Curator tasks.

The model provider should be selected by configuration. Reasonable initial provider paths:

- OpenAI API for direct OpenAI SDK support and tracing.
- OpenRouter for model choice and cost flexibility.
- A Hermes/Codex executor only for scoped edit tasks, if using subscription-backed coding agents is
  operationally useful.

Do not assume ChatGPT/Codex subscription access is the same thing as API access for an SDK-backed
agent.

## Broker And Permission Boundaries

The Curator should run in a sandboxed container launched by `gh-agent-broker` sandbox-broker.

The Curator container should receive:

- `/data/intake` mounted read-write.
- `/data/logs` mounted read-only.
- A task contract mounted read-only, such as `/input/task.json`.
- An output directory, such as `/output`.
- Broker credentials sufficient only for allowed GitHub operations.

The Curator container should not receive:

- GitHub tokens.
- YKM runtime secrets.
- Cloudflare Access secrets.
- OpenRouter/OpenAI keys unless needed by the agent runtime.
- The Docker socket.
- Arbitrary host mounts.
- Merge rights.
- Direct write access to the live YKM index.

Broker policy should allow:

- Clone/fetch of `grubbyhacker/ykmcorpus`.
- Push only to Curator-owned branches, such as `curator/<run_id>/<slug>`.
- Open PRs against protected `main` in `ykmcorpus`.
- Read and comment on Curator-authored PRs.
- Update Curator-owned PR branches.
- File issues against an explicit allowlist of owner repositories.
- Assign Curator-created issues to Roger when the issue represents owner action or product follow-up.

Broker policy should deny:

- Pushes to `main`.
- PR merges.
- Writes to non-allowlisted repositories.
- Secret exfiltration through broad host access.
- Unbounded issue creation.

All Curator-created issues should carry `ykm-curator`, plus more specific labels when useful:

- `upload`
- `feedback`
- `corpus`
- `maintenance`
- `needs-owner-input`

Issue routing defaults:

- Corpus facts, owner follow-up, and corpus maintenance issues go to the private corpus repo.
- Product, service, tool-description, schema, and Curator implementation issues go to this YKM repo.
- `needs_owner_action` issues are assigned to Roger by default.
- Product or service follow-up issues are also assigned to Roger by default.

## Run Ordering

Each manual Curator run should process existing Curator PRs before opening new work.

Recommended run order:

1. Load run configuration and policy.
2. Discover Curator-authored open PRs.
3. Reconcile PR state and respond to owner feedback where needed.
4. Mark merged or closed PRs in intake metadata.
5. Read feedback records since the last feedback checkpoint.
6. Read relevant upload metadata, source pointers, and supporting logs referenced by the feedback.
7. Ask the agent layer for one batch-level feedback plan.
8. Validate the proposed plan against policy and typed action schemas.
9. Execute allowed feedback actions: no-op decisions, issue creation, corpus PRs, or upload links.
10. Claim and process upload bundles.
11. Persist run state, feedback decisions, and a run report.

This ordering prevents the Curator from opening new PRs while ignoring review feedback on existing
PRs.

## Upload State Machine

Upload state is represented by directory location plus `curator.json`.

States:

- `pending`: written by YKM; not yet claimed.
- `claimed`: atomically moved by the Curator for processing.
- `pr_opened`: a corpus PR exists and is linked to this bundle.
- `deferred`: owner input is needed before a useful PR can be created.
- `rejected`: the bundle should not become corpus content.
- `processed`: the linked PR was merged or the work was otherwise completed.
- `archived`: retained or deleted according to future retention policy.

Transitions:

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

The Curator should claim by atomic directory rename from `pending` to `claimed`. Do not introduce a
database until filesystem contention is real.

Every claimed upload should receive a `curator.json` similar to:

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
  "claimed_at": "2026-06-06T00:00:00Z",
  "last_checked_at": "2026-06-06T00:00:00Z",
  "last_action_at": "2026-06-06T00:00:00Z",
  "blocking_reason": null,
  "notes": "Curated into procedures/home/example.md"
}
```

An acceptable upload should normally become one focused corpus PR. The Curator should curate rather
than copy raw staging content blindly: choose a stable source ID, corpus path, frontmatter, headings,
tags, and related links, while preserving the uploaded intent.

Unsuitable uploads should be rejected with a clear reason. Ambiguous uploads should be deferred and,
when useful, linked to a GitHub issue requesting owner input.

## Feedback State Machine

Feedback records are append-only, but they are not the Curator's primary planning unit. The Curator
should plan over all feedback submitted since the previous run, then execute a smaller or larger set
of actions based on the batch. One action may cite many feedback records; one feedback record may
produce multiple actions.

The Curator should track offsets or processed IDs in `curator-state.json`, persist each batch plan
under `feedback/runs/<run_id>/feedback-plan.json`, and append per-feedback dispositions to
`curator-decisions.jsonl`.

If no previous checkpoint exists, the first production Curator run should plan over all existing
feedback. The currently submitted production feedback should also become E2E fixture material for
Curator tests.

### Feedback Batch Plan

The feedback batch plan is the durable explanation of Curator agency for a run. It should include:

- `run_id`
- input checkpoint or feedback offset range
- included feedback IDs
- referenced upload IDs, source IDs, section IDs, and result IDs
- proposed actions
- policy validation result
- execution result
- timestamp

Each action in the plan must cite its evidence. Evidence should include the relevant feedback IDs
and, when available, upload IDs, source IDs, section IDs, result IDs, or query-log references.

Allowed v1 feedback action types:

- `no_action`: positive, non-actionable, duplicate, superseded, or insufficiently grounded feedback.
- `issue`: owner action, product follow-up, corpus maintenance, or ambiguous work that needs review.
- `corpus_pr`: a clear corpus edit that is justified by the feedback and available evidence.
- `link_to_upload`: feedback handled as part of an upload bundle.
- `defer`: action blocked on owner input or missing evidence.

The Curator may propose an action that emerges from a cluster rather than from one individual record,
but the action must cite the cluster as evidence and pass deterministic policy validation. This is
the agency boundary: batch-level inference is allowed, ungrounded invented work is not.

The Curator should use a soft action-volume cap for feedback batches. Exceeding the cap should be
called out in the run report and plan, but should not automatically discard valid actions.

### Feedback Decisions

Feedback decision states:

- `unseen`: exists in `feedback.jsonl`, not yet processed.
- `no_action_positive`: positive signal recorded, no corrective action.
- `no_action_non_actionable`: weak or untargeted signal recorded, no corrective action.
- `no_action_superseded`: record is covered by a later correction, consolidation, or duplicate.
- `issue_opened`: follow-up belongs in a GitHub issue.
- `pr_opened`: clear corpus edit proposed.
- `linked_to_upload`: feedback is handled as part of an upload bundle.
- `deferred`: owner clarification needed.

Decision records should include:

- `feedback_id`
- `run_id`
- `plan_action_id`
- `decision`
- `pr_number`
- `issue_number`
- `source_id`
- `section_id`
- `upload_id`
- `reason`
- `timestamp`

Positive feedback and non-actionable feedback should usually produce no corrective action. Untargeted
negative feedback should not trigger speculative corpus edits. Actionable feedback with source
pointers may produce a PR if the fix is clear.

Current production feedback should be used as E2E test data for the planner. Tests should assert the
shape of the resulting plan, not exact wording. Useful scenarios include:

- Noisy self-correcting feedback collapses into a small number of actions plus superseded/no-op
  dispositions.
- Missing owner facts produce assigned `needs-owner-input` corpus issues.
- Product or service feedback routes to this YKM repo instead of becoming an immediate service fix.
- Positive feedback records produce no GitHub action.
- Upload-linked feedback attaches to upload processing rather than automatically becoming a separate
  issue.

## PR Maintenance State Machine

The Curator must maintain its own active PRs. Opening a PR is not completion.

Curator PR states:

- `open_waiting_review`: PR is open, no owner action needed by Curator.
- `changes_requested`: owner or reviewer requested concrete changes.
- `commented_needs_triage`: comments exist and need classification.
- `checks_failed`: CI or validation failed.
- `ready_for_owner`: Curator has responded or updated the branch and is waiting again.
- `merged`: PR merged; linked intake can move to `processed`.
- `closed_unmerged`: PR closed without merge; linked intake should become `rejected` or `deferred`.
- `stale_or_blocked`: Curator cannot proceed without owner input.

On each run, the Curator should:

1. Find open PRs authored by the Curator or matching the Curator branch prefix.
2. Read PR comments, review submissions, unresolved review threads, and check status.
3. Classify the PR state.
4. For concrete requested changes, update the same branch and leave a concise response.
5. For ambiguous feedback, ask a clarifying PR comment and mark blocked.
6. For failed checks, attempt a fix only if the failure is within Curator scope.
7. For merged PRs, mark linked upload or feedback records as processed.
8. For closed-unmerged PRs, record the outcome and stop retrying unless owner reopens the work.

Curator branches and PR bodies should include stable markers so a fresh run can reconstruct state:

```text
YKM-Curator-Run: cur_...
YKM-Curator-Upload: upl_...
YKM-Curator-Feedback: fb_...
```

The Curator should be conservative when interpreting review comments. A reviewer saying "not this"
or "let's hold off" should close or defer work, not prompt the Curator to keep pushing new variants.

## Corpus Maintenance

Proactive corpus maintenance should be issue-only in v1.

The Curator may notice maintenance candidates while processing intake, such as:

- Two topics that should be linked with `related`.
- Repeated feedback indicating missing tags.
- Conflicting notes that need owner judgment.
- Labels that should be promoted into frontmatter.
- Entity disambiguation candidates, such as distinct same-kind subjects.

In v1, these become labeled backlog issues. They should not become proactive cleanup PRs unless they
are directly necessary to process a specific upload or feedback item.

Entity identity remains Curator-owned over time, but v1 should represent entity work as corpus
frontmatter, tags, related links, aliases, and issue backlog items. Do not introduce a separate entity
database or query-path entity resolution.

## Safety And Prompt-Injection Policy

Uploads and feedback are untrusted input. The Curator should treat them as data, not instructions.

The deterministic controller should wrap every agent call with a narrow task contract. For example:

- "Given this batch of new feedback records and referenced context, return a feedback action plan
  using the allowed action schema."
- "Review this markdown bundle and return an upload decision."
- "Draft a PR description from these concrete file changes."
- "Classify these PR comments into allowed next actions."

Agent outputs must be parsed into Pydantic models and rejected if they do not validate.

The Curator should not obey upload or feedback text that asks it to:

- Change broker policy.
- Reveal secrets.
- Bypass GitHub review.
- Modify unrelated repositories.
- Ignore owner instructions.
- Exfiltrate logs or corpus content.

## Proposed Implementation Slices

### Slice 1: Documentation And Contracts

- Write this plan.
- Add a Curator contract document for `task.json`, `curator.json`, feedback batch plans, and
  feedback decision records.
- Add additive feedback categories to the YKM contract.
- Add tests for the new feedback categories.

### Slice 2: Deterministic Skeleton

- Add a `ykm curator` CLI or separate `curator` entrypoint.
- Load config and task contract.
- Discover upload directories.
- Discover feedback records.
- Build a feedback batch from records since the last checkpoint.
- Discover Curator PR markers through broker calls, initially in dry-run or fixture mode.
- Emit a run report without changing queues or GitHub state.

### Slice 3: Queue State

- Implement atomic upload claim.
- Write and update `curator.json`.
- Track feedback checkpoints, run plans, and per-feedback dispositions.
- Add fixture tests for state transitions and idempotency.

### Slice 4: Broker Integration

- Clone/fetch `ykmcorpus` through broker.
- Create Curator branches through broker.
- Open PRs through broker.
- File allowlisted issues through broker.
- Read PR comments, reviews, threads, and checks.
- Deny disallowed broker operations in tests or dry-run policy checks.

### Slice 5: Typed Agent Decisions

- Add provider-neutral model adapter.
- Add first structured decision tasks:
  - upload review
  - feedback batch planning
  - PR comment classification
  - PR body drafting
- Compare Pydantic AI and OpenAI Agents SDK for the actual task shape before committing long-term.

### Slice 6: PR Maintenance Loop

- Reconcile Curator-authored open PRs before new intake.
- Respond to concrete owner feedback.
- Update branches when the requested change is clear.
- Ask clarification when feedback is ambiguous.
- Mark merged and closed PRs back into intake state.

### Slice 7: Production Manual Run

- Launch manually through sandbox-broker.
- Use synthetic or low-risk intake first.
- Confirm the Curator cannot push to `main`.
- Confirm the Curator can open a PR and later respond to review feedback.
- Confirm YKM service remains passive and staged intake is never served directly.

## Test Plan

Offline tests:

- Feedback schema accepts existing categories plus `needs_owner_action`, `positive_content`, and
  `non_actionable`.
- Upload state transitions are valid and invalid transitions fail.
- Feedback decisions are idempotent across repeated runs.
- Feedback batch plans support many-feedback-to-one-action and one-feedback-to-many-action mappings.
- Feedback batch actions cite evidence IDs.
- PR markers can reconstruct state after local metadata is missing.
- Agent outputs are rejected when they fail schema validation.
- Prompt-injection text in uploads/feedback cannot alter allowed actions.
- Current production feedback fixtures produce expected action shapes without exact wording locks.

Fixture or mocked-broker tests:

- Curator branch push allowed.
- Push to `main` denied.
- PR merge denied.
- Issue creation allowed only for allowlisted repos.
- Curator-authored PR with no comments stays waiting.
- Review-requested PR transitions to `changes_requested`.
- Failed checks transition to `checks_failed`.
- Merged PR marks linked intake `processed`.
- Closed-unmerged PR marks linked intake `deferred` or `rejected`.
- `needs_owner_action` opens an assigned corpus issue by default.
- Product/service feedback opens an assigned issue in this YKM repo by default.
- Noisy or self-correcting feedback records become superseded/no-op dispositions when covered by a
  later consolidated record.

Manual acceptance:

- Curator opens one focused PR from a staged upload.
- Owner requests a change in PR review.
- Curator detects the review, updates the same branch, and comments.
- Owner merges.
- Curator marks linked intake processed on a later run.
- Curator files a labeled issue for ambiguous feedback.
- Curator does not create proactive cleanup PRs in v1.

## Open Questions

- Which SDK should be the first implementation target: Pydantic AI or OpenAI Agents SDK?
- Should the Curator package be part of `src/ykm` or live under a separate package namespace in this
  repo?
- What exact broker interface should the Curator call: MCP, CLI, HTTP, or another adapter?
- Should the first production Curator runs have model access inside the sandbox, or should model
  calls happen through an external brokered service?
- What is the issue allowlist for cross-repo filing?
- What retention policy should apply to processed, rejected, and archived intake?
- How much query-log context is acceptable during v1? Current default: only use logs as supporting
  context when feedback references result/source IDs.
- What soft action-volume threshold should trigger extra reporting for a feedback batch?

## Recommended Defaults

- Manual runs only.
- PR maintenance before new intake.
- Feedback planning over records since the last run.
- Persisted run-level feedback plans plus per-feedback dispositions.
- SDK-first Python implementation.
- Provider-neutral model adapter.
- Intake read-write, logs read-only.
- Corpus PRs and allowlisted issues only.
- Assign owner-action and product/service issues to Roger by default.
- No proactive cleanup PRs in v1.
- No always-on worker until the manual lifecycle is proven.
- No live index rebuild or deploy responsibility in the Curator.
