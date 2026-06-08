# YouKnowMe Phase 4 Curator Plan

Status: planning draft.

Phase 4 introduces The Curator: a separate, minimum-privilege agent that processes YouKnowMe intake,
maintains its own proposed corpus PRs, and files GitHub issues when human or cross-repo follow-up is
better than a corpus edit.

This document is a plan, not an implementation. It captures the intended shape after Phase 3 staged
intake and before any Curator runtime is built.

## Goals

The Curator's first useful job is intake triage, not broad autonomous corpus maintenance.

In this document, "initial scope" means the first production-safe manual Curator workflow. It is not
a full versioned release plan. It means enough Curator behavior to process staged uploads and
feedback, open reviewed PRs/issues, maintain its own PRs, and persist run state without giving the
Curator merge, deploy, or always-on authority.

Initial scope should:

- Drain staged upload bundles from the YKM intake queue.
- Review actionable feedback records and turn them into corpus PRs or GitHub issues.
- Maintain Curator-authored open PRs, including responding to owner review feedback.
- Preserve the spine rule: YKM remains passive and never writes to its own corpus.
- Keep credentials and authority separated: the Curator proposes, GitHub records, the owner merges.
- Build the Curator as an SDK-first agent system so we learn the mechanics of typed agent
  development rather than delegating the whole workflow to an existing coding agent shell.

Initial scope should not:

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
/data/intake/curator-run.lock
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
  -> model broker/proxy client
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
- Run locking.

This document uses "feedback planning" for one orchestration step inside a Curator run. It is not a
separate daemon or independent product. In that step, the controller gathers the feedback batch and
context, asks the agent layer for a proposed action plan, validates that plan, and then executes only
the allowed actions.

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

The first implementation should be SDK-first and provider-neutral.

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

- OpenAI API through a broker/proxy for direct OpenAI SDK support and tracing.
- OpenRouter through a broker/proxy for model choice and cost flexibility.
- A Hermes/Codex executor only for scoped edit tasks, if using subscription-backed coding agents is
  operationally useful.

Do not assume ChatGPT/Codex subscription access is the same thing as API access for an SDK-backed
agent.

## Model Egress And Key Boundaries

The Curator will send untrusted uploads and feedback to LLM calls. Provider-key placement and network
egress are therefore load-bearing security decisions, not implementation details.

Default decision: provider keys should not be mounted into the Curator sandbox. Model calls should go
through a broker-controlled LLM proxy or model broker that hides provider credentials from the
Curator and exposes only the allowed model-call surface. The likely home for this capability is
`gh-agent-proxy`, which is already the owner's general-purpose agent/broker integration project and
may need changes to support Curator model calls. The Curator sandbox should have outbound network
access only to that broker/proxy and other explicitly required broker endpoints.

This shape preserves provider neutrality without giving a prompt-injected Curator a direct exfiltration
path through arbitrary model-provider HTTPS calls. A third-party OSS LLM proxy package may be useful
inside `gh-agent-proxy`, but the architectural requirement is the boundary: the Curator gets a narrow
model endpoint, not provider secrets.

The proxy is content-bearing: it will see the corpus, upload, feedback, and log excerpts sent to the
model. The default trust footprint should therefore be self-hosted owner infrastructure, preferably
beside the existing broker/proxy stack. A hosted third-party proxy would be a new sensitive data
processor and should require an explicit design decision, not an accidental implementation detail.

The model path needs limits analogous to GitHub mutation limits. The proxy should enforce per-run
model-call and token budgets, and the Curator run report should include model-call counts, token
usage, and budget exhaustion if the proxy exposes them.

The first real Curator run over production intake should not happen until this boundary is designed
and smoke-tested. If an early local spike uses direct model keys, it must use synthetic or low-risk
fixtures only and should not be treated as production-ready.

## Broker And Permission Boundaries

The Curator should run in a sandboxed container launched by `gh-agent-broker` sandbox-broker.

Live broker contract as of 2026-06-08:

- Broker Git remote for workers: `http://broker:8080/git/grubbyhacker/ykmcorpus.git`.
- Workers should not use direct GitHub SSH or HTTPS remotes.
- Broker principal: `BROKER_AGENT_ID=ykm-curator`.
- Broker secret is injected by sandbox-broker from the VPS environment.
- GitHub app context: `ykm-curator`.
- GitHub app ID: `3991340`.
- Installation ID for `grubbyhacker/ykmcorpus`: `138708452`.
- Allowed branch pattern: `curator/<run-id>/<slug>`.
- Allowed base branch: `main`.
- Required PR metadata:
  - `YKM-Curator-Run`
  - `YKM-Curator-Action` with one of `upload`, `feedback`, or `maintenance`
- Known caveat: broker issue `#27` tracks protection against reusing branches from merged PRs.

The Curator container should receive:

- Staged feedback and upload bundle contents mounted read-only as evidence.
- Upload queue directories mounted only with the write scope required for atomic state moves.
- Curator-owned state directories mounted read-write for run plans, decisions, reports, and locks.
- `/data/logs` mounted read-only.
- A task contract mounted read-only, such as `/input/task.json`.
- An output directory, such as `/output`.
- Broker credentials sufficient only for allowed GitHub operations.
- A model-broker/proxy endpoint credential, if model calls are required.

The Curator container should not receive:

- GitHub tokens.
- YKM runtime secrets.
- Cloudflare Access secrets.
- OpenRouter/OpenAI/provider keys.
- The Docker socket.
- Arbitrary host mounts.
- Merge rights.
- Direct write access to the live YKM index.
- Broad outbound internet access.

Intake bundles and feedback logs are source evidence. The Curator should never edit them in place.
Curated output belongs on a corpus branch, and Curator metadata belongs in Curator-owned state.

Broker policy should allow:

- Clone/fetch of `grubbyhacker/ykmcorpus`.
- Push only to Curator-owned branches, such as `curator/<run_id>/<slug>`.
- Open PRs against protected `main` in `ykmcorpus`.
- Read and comment on Curator-authored PRs.
- Update Curator-owned PR branches.
- File issues against an explicit allowlist of owner repositories.
- Assign Curator-created issues to Roger when the issue represents owner action or product follow-up.
- Delete or clean up Curator-owned branches after terminal PR disposition, if branch cleanup is
  enabled by policy.

Broker policy should deny:

- Pushes to `main`.
- PR merges.
- Writes to non-allowlisted repositories.
- Secret exfiltration through broad host access.
- Unbounded issue creation.
- Issue creation in public repositories except explicitly allowlisted public product repositories.
- Unbounded PR or issue body content copied from private corpus or intake sources.

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
- Corpus and owner-fact issue targets must be private unless Roger explicitly decides otherwise.
- Public product repositories, including this YKM repo, may receive product/service/implementation
  issues when the body contains no private corpus, intake, log, or personal-memory content.
- Issue and PR bodies should summarize and cite source IDs or feedback IDs rather than dumping large
  private source excerpts.

Broker-side mutation limits should be explicit. The feedback planning step may have a soft cap on
proposed actions, but GitHub mutations need hard per-run ceilings for opened PRs plus filed issues.
New issue and PR creation count against these ceilings; updates to existing Curator PR branches or
comments on existing Curator PRs do not, because PR maintenance runs before new work and should not be
starved.

Live YKM mutation budget concept:

- `run_metadata_field: YKM-Curator-Run`
- `action_metadata_field: YKM-Curator-Action`
- `max_new_objects_per_run: 4`
- `upload: 2`
- `feedback: 2`
- Enforced for `pull.create` and `issue.create`.
- Over-budget denials return structured `capacity_deferred` responses.

Upload processing should not be permanently starved by feedback. Initial scope should use separate mutation
sub-budgets or a fairness rule so a noisy feedback batch cannot consume every new GitHub object every
run while pending uploads wait. Valid over-cap actions should be capacity-deferred with an immediate
retry trigger for the next run, not mixed with owner-blocked `defer` decisions.

## Run Ordering

Each manual Curator run should process existing Curator PRs before opening new work.

Recommended run order:

1. Load run configuration and policy.
2. Acquire a single-flight run lock.
3. Snapshot the feedback log start and end offsets for this run.
4. Discover Curator-authored open PRs and issues.
5. Reconcile GitHub state and respond to owner feedback where needed.
6. Mark merged, closed, or completed GitHub work in intake metadata.
7. Read feedback records in the frozen feedback window plus deferred feedback ready for re-entry.
8. Read relevant upload metadata, source pointers, and supporting logs referenced by the feedback.
9. Ask the agent layer for one batch-level feedback plan.
10. Validate the proposed plan against policy and typed action schemas.
11. Dedupe proposed GitHub mutations against existing PR/issue markers before execution.
12. Execute allowed feedback actions: no-op decisions, issue creation, corpus PRs, or upload links.
13. Claim and process upload bundles.
14. Persist run state, feedback decisions, run report, and the next feedback checkpoint.
15. Release the run lock.

This ordering prevents the Curator from opening new PRs while ignoring review feedback on existing
PRs.

Manual runs still need a lock. If a lock already exists and is live, a second Curator run should exit
without planning. If a stale lock is detected, the run should require an explicit stale-lock recovery
mode. Stale-lock recovery should itself use the same single-flight discipline so two recovery attempts
cannot proceed concurrently.

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
  "blocking_issue_number": null,
  "claimed_at": "2026-06-06T00:00:00Z",
  "last_checked_at": "2026-06-06T00:00:00Z",
  "last_action_at": "2026-06-06T00:00:00Z",
  "blocking_reason": null,
  "notes": "Curated into procedures/home/example.md"
}
```

`issue_number` is the primary issue created from or associated with the upload decision.
`blocking_issue_number` is the issue whose resolution can unblock a deferred upload. They may be the
same issue in the common owner-input case, or different issues when a bundle produces both general
follow-up and a specific blocker.

An acceptable upload should normally become one focused corpus PR. The Curator should curate rather
than copy raw staging content blindly: choose a stable source ID, corpus path, frontmatter, headings,
tags, and related links, while preserving the uploaded intent.

Unsuitable uploads should be rejected with a clear reason. Ambiguous uploads should be deferred and,
when useful, linked to a GitHub issue requesting owner input.

Deferred uploads should record a re-entry trigger in `curator.json`. The normal trigger is the linked
owner-input issue closing, but a deferred upload may also use an explicit retry-after timestamp when
the blocker is transient. On each run, the Curator should reconcile linked issue state before deciding
whether `deferred -> claimed` is allowed.

## Feedback State Machine

Feedback records are append-only, but they are not the Curator's primary planning unit. The Curator
should plan over all feedback submitted since the previous run, then execute a smaller or larger set
of actions based on the batch. One action may cite many feedback records; one feedback record may
produce multiple actions.

The Curator should track offsets or processed IDs in `curator-state.json`, persist each batch plan
under `feedback/runs/<run_id>/feedback-plan.json`, and append per-feedback dispositions to
`curator-decisions.jsonl`.

`curator-decisions.jsonl` should keep every decision the Curator has made. When the Curator needs the
current status for one feedback record, it should use the newest decision for that `feedback_id`. If
two decisions have the same timestamp, the later line in the file wins. This rule should be tested.

If no previous checkpoint exists, the first production Curator run should plan over all existing
feedback. The currently submitted production feedback should also become E2E fixture material for
Curator tests.

Feedback appended while a run is executing belongs to the next run. The run's feedback window should
be frozen from the recorded start checkpoint to the recorded end offset before planning begins.

### Feedback Batch Plan

The feedback batch plan is the durable explanation of Curator agency for a run. It should include:

- `run_id`
- input checkpoint or feedback offset range
- included feedback IDs
- deferred feedback IDs re-entered into this run
- referenced upload IDs, source IDs, section IDs, and result IDs
- proposed actions
- policy validation result
- execution result
- idempotency key for each action
- timestamp

Each action in the plan must cite its evidence. Evidence should include the relevant feedback IDs
and, when available, upload IDs, source IDs, section IDs, result IDs, or query-log references.

Allowed initial-scope feedback action types:

- `no_action`: positive, non-actionable, duplicate, superseded, or insufficiently grounded feedback.
- `issue`: owner action, product follow-up, corpus maintenance, or ambiguous work that needs review.
- `corpus_pr`: a clear corpus edit that is justified by the feedback and available evidence.
- `link_to_upload`: feedback handled as part of an upload bundle.
- `defer`: action blocked on owner input or missing evidence.

The Curator may propose an action that emerges from a cluster rather than from one individual record,
but the action must cite the cluster as evidence and pass deterministic policy validation. This is
the agency boundary: batch-level inference is allowed, ungrounded invented work is not.

The Curator should use a soft action-volume cap for proposed feedback actions. Exceeding the cap
should be called out in the run report and plan, but should not automatically discard valid actions.
GitHub mutations have a separate hard per-run ceiling enforced by policy.

Every planned action needs an idempotency key. The key should be stable across retry after a crash,
and should be based on action type plus durable evidence identifiers rather than model wording. Before
opening a PR or issue, the executor must search existing Curator PR/issue markers for that key and
reuse or update the existing object instead of creating a duplicate.

`corpus_pr` is structurally gated. A feedback-driven corpus PR must cite at least one resolvable
`source_id` or `section_id`, or be backed by a staged upload bundle. Otherwise the controller should
reject or downgrade the action to `issue` or `defer`. Missing-content feedback with no target should
usually become an owner issue, a product issue, or a link to an upload rather than a speculative
corpus PR.

Routing is an agent judgment validated by policy. Feedback category alone does not determine the
repository: the plan should classify whether an action is corpus, owner-action, product/service, or
Curator-maintenance work, and the controller should validate the chosen repo against the allowlist.

### Feedback Decisions

Feedback decision states:

- `unseen`: exists in `feedback.jsonl`, not yet processed.
- `no_action_positive`: positive signal recorded, no corrective action.
- `no_action_non_actionable`: weak or untargeted signal recorded, no corrective action.
- `no_action_duplicate`: record is a duplicate of another feedback record or action.
- `no_action_superseded`: record is replaced by a later correction or consolidation.
- `no_action_insufficient_evidence`: record lacks enough grounding for action.
- `issue_opened`: follow-up belongs in a GitHub issue.
- `pr_opened`: clear corpus edit proposed.
- `linked_to_upload`: feedback is handled as part of an upload bundle.
- `deferred`: owner clarification needed.
- `capacity_deferred`: valid action deferred because the run exhausted a GitHub mutation budget.

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

Current production feedback should be used as E2E test data for feedback planning. Tests should
assert the shape of the resulting plan, not exact wording. Useful scenarios include:

- Noisy self-correcting feedback collapses into a small number of actions plus superseded/no-op
  dispositions.
- Missing owner facts produce assigned `needs-owner-input` corpus issues.
- Product or service feedback routes to this YKM repo instead of becoming an immediate service fix.
- Positive feedback records produce no GitHub action.
- Upload-linked feedback attaches to upload processing rather than automatically becoming a separate
  issue.

Deferred feedback needs a re-entry path. A deferred decision should record what can unblock it, such
as a linked issue closing, a linked upload changing state, or a configured retry-after time. Deferred
feedback should not be invisible merely because its original feedback offset is behind the checkpoint.
Capacity-deferred feedback should use the immediate retry-after path for the next run; it is not
blocked on owner input.

## PR Maintenance State Machine

The Curator must maintain its own active PRs. Opening a PR is not completion.

The deployed prereq broker image exposes read APIs for PR list/read/files/comments/reviews,
review-comments, review-threads, commit status, and check runs. The Curator should use brokered reads
for reconciliation rather than direct GitHub access.

GitHub is authoritative for PR and issue state. Local `curator.json`, run plans, and decision logs
are durable Curator state, but they are reconciled against GitHub at run start. If local metadata and
GitHub disagree, the Curator should prefer GitHub's current PR/issue state and append a reconciliation
decision rather than rewriting history.

Curator PR states:

- `open_waiting_review`: PR is open, no owner action needed by Curator.
- `changes_requested`: owner or reviewer requested concrete changes.
- `commented_needs_triage`: comments exist and need classification.
- `checks_failed`: CI or validation failed.
- `ready_for_owner`: Curator has responded or updated the branch and is waiting again.
- `merged`: PR merged; linked intake can move to `processed`.
- `closed_unmerged`: PR closed without merge; linked intake should become `rejected` or `deferred`.
- `stale_or_blocked`: Curator cannot proceed without owner input.

Allowed PR state transitions:

```text
open_waiting_review -> commented_needs_triage
open_waiting_review -> changes_requested
open_waiting_review -> checks_failed
open_waiting_review -> merged
open_waiting_review -> closed_unmerged
commented_needs_triage -> changes_requested
commented_needs_triage -> ready_for_owner
commented_needs_triage -> stale_or_blocked
commented_needs_triage -> merged
commented_needs_triage -> closed_unmerged
changes_requested -> ready_for_owner
changes_requested -> merged
changes_requested -> closed_unmerged
checks_failed -> ready_for_owner
checks_failed -> merged
checks_failed -> closed_unmerged
ready_for_owner -> open_waiting_review
ready_for_owner -> merged
ready_for_owner -> closed_unmerged
stale_or_blocked -> ready_for_owner
stale_or_blocked -> merged
stale_or_blocked -> closed_unmerged
```

Roger remains sovereign over Curator PRs. He may merge or close a Curator PR from any non-terminal
state, and GitHub reconciliation should accept that terminal state even if local Curator metadata was
stale.

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

Proactive corpus maintenance should be issue-only in initial scope.

The Curator may notice maintenance candidates while processing intake, such as:

- Two topics that should be linked with `related`.
- Repeated feedback indicating missing tags.
- Conflicting notes that need owner judgment.
- Labels that should be promoted into frontmatter.
- Entity disambiguation candidates, such as distinct same-kind subjects.

In initial scope, these become labeled backlog issues. They should not become proactive cleanup PRs
unless they are directly necessary to process a specific upload or feedback item.

Entity identity remains Curator-owned over time, but initial scope should represent entity work as
corpus frontmatter, tags, related links, aliases, and issue backlog items. Do not introduce a separate
entity database or query-path entity resolution.

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

Malformed input records should not crash the run or disappear silently. Schema-invalid feedback
records, unreadable bundles, and malformed manifests should be quarantined or reported with a bounded
error record, mirroring the build path's quarantine posture for unsafe corpus inputs.

Run reports are part of the operator UX. Each run should produce a bounded report that records the
feedback window, plan summary, executed mutations, deferred actions, validation failures, partial
failure status, and the next checkpoint if it advanced.

## Proposed Implementation Slices

### Slice 1: Documentation And Contracts

- Write this plan.
- Add a Curator contract document for `task.json`, `curator.json`, feedback batch plans, and
  feedback decision records.
- Add additive feedback categories to the YKM contract.
- Add tests for the new feedback categories.
- Add contracts for run locking, feedback offset snapshots, action idempotency keys, and the rule for
  finding the current feedback decision.

### Slice 2: Deterministic Skeleton

- Add a `ykm curator` CLI or separate `curator` entrypoint.
- Load config and task contract.
- Discover upload directories.
- Discover feedback records.
- Build a feedback batch from records since the last checkpoint.
- Freeze feedback start/end offsets at run start.
- Discover Curator PR markers through broker calls, initially in dry-run or fixture mode.
- Use the live broker remote `http://broker:8080/git/grubbyhacker/ykmcorpus.git` in sandboxed dry
  runs.
- Emit a run report without changing queues or GitHub state.

### Slice 3: Queue State

- Implement atomic upload claim.
- Write and update `curator.json`.
- Track feedback checkpoints, run plans, and per-feedback dispositions.
- Implement the rule for finding the current feedback decision from the append-only decision log.
- Implement deferred feedback and deferred upload re-entry triggers.
- Add fixture tests for state transitions and idempotency.

### Slice 4: Broker Integration

- Clone/fetch `ykmcorpus` through the broker Git remote.
- Create Curator branches through broker.
- Open PRs through broker.
- File allowlisted issues through broker.
- Read PR comments, reviews, threads, and checks.
- Enforce hard per-run GitHub mutation limits with upload/feedback fairness.
- Deny disallowed broker operations in tests or dry-run policy checks.

### Slice 4A: Model Broker Boundary

- Use the live `gh-agent-proxy` model endpoint from inside the Docker/Hermes network:
  `http://gh-agent-proxy:8092/v1/model/call`.
- Keep provider keys outside the Curator sandbox.
- Allow Curator egress only to the model broker/proxy and required broker endpoints.
- Keep the proxy self-hosted by default; hosted third-party proxy use requires an explicit design
  decision.
- Enforce per-run model-call and token budgets.
- Add synthetic smoke tests proving the Curator can make a typed model call through the proxy.
- Treat direct provider-key use as local-spike-only, not production-ready.

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
- Current feedback status is read consistently from the append-only decision log.
- Feedback batch plans support many-feedback-to-one-action and one-feedback-to-many-action mappings.
- Feedback batch actions cite evidence IDs.
- Feedback batch start/end offsets freeze records appended during a run for the next run.
- Overlapping runs are rejected by the single-flight lock.
- Cluster-spanning action idempotency keys prevent duplicate PRs/issues after retry.
- `corpus_pr` without a resolvable target or staged upload is rejected or downgraded.
- PR markers can reconstruct state after local metadata is missing.
- Agent outputs are rejected when they fail schema validation.
- Malformed input records are quarantined or reported without crashing the run.
- Prompt-injection text in uploads/feedback cannot alter allowed actions.
- Current production feedback fixtures produce expected action shapes without exact wording locks.

Fixture or mocked-broker tests:

- Curator branch push allowed.
- Push to `main` denied.
- PR merge denied.
- Issue creation allowed only for allowlisted repos.
- Public issue repositories are denied unless explicitly allowed by policy for non-sensitive
  product/service work.
- PR/issue bodies are bounded and do not dump large corpus or intake excerpts.
- Hard per-run GitHub mutation limits defer over-cap actions.
- Upload PR creation is not indefinitely starved by feedback issue/PR creation.
- Curator-authored PR with no comments stays waiting.
- Review-requested PR transitions to `changes_requested`.
- Failed checks transition to `checks_failed`.
- Merged PR marks linked intake `processed`.
- Closed-unmerged PR marks linked intake `deferred` or `rejected`.
- `needs_owner_action` opens an assigned corpus issue by default.
- Product/service feedback opens an assigned issue in this YKM repo by default.
- Noisy or self-correcting feedback records become superseded/no-op dispositions when covered by a
  later consolidated record.
- Capacity-deferred feedback re-enters on the next run, distinct from owner-blocked deferral.
- Deferred uploads and feedback re-enter only when their trigger is satisfied.
- GitHub state wins over stale local Curator state during reconciliation.
- Owner merge or close from any non-terminal PR state is accepted during reconciliation.

Model tests:

- Offline tests use recorded or fixture model responses for deterministic plan-shape assertions.
- Live-model evaluation is separate from required offline tests and may tolerate clustering variance.
- Model broker/proxy tests verify provider keys are not available in the Curator sandbox.
- Model broker/proxy tests verify per-run call/token budgets fail closed.
- Real production feedback fixtures must remain inside the appropriate private boundary or be
  redacted/synthesized before being checked into this repo.
- At least half of feedback-planning fixtures should be synthetic, shape-based cases to avoid
  overfitting to the current production feedback batch.

Manual acceptance:

- Curator opens one focused PR from a staged upload.
- Owner requests a change in PR review.
- Curator detects the review, updates the same branch, and comments.
- Owner merges.
- Curator marks linked intake processed on a later run.
- Curator files a labeled issue for ambiguous feedback.
- Curator does not create proactive cleanup PRs in initial scope.
- Curator makes model calls through the broker/proxy without provider keys in the sandbox.
- Curator run reports clearly show partial failures and whether the feedback checkpoint advanced.

## Open Questions

- Which SDK should be the first implementation target: Pydantic AI or OpenAI Agents SDK?
- Should the Curator package be part of `src/ykm` or live under a separate package namespace in this
  repo?
- What exact broker interface should the Curator call: MCP, CLI, HTTP, or another adapter?
- What is the issue allowlist for cross-repo filing?
- What retention policy should apply to processed, rejected, and archived intake?
- How much query-log context is acceptable in initial scope? Current default: only use logs as
  supporting context when feedback references result/source IDs.
- What soft action-volume threshold should trigger extra reporting for a feedback batch?
- What Curator worker image/entrypoint and sandbox template should use the live broker/proxy contract?
- How should Curator avoid or recover from branch reuse until broker issue `#27` is fixed?
- What production model-call and token budgets should the proxy enforce beyond the current prereq
  smoke settings?
- What stale-lock timeout and recovery command should manual runs use?

## Recommended Defaults

- Manual runs only.
- PR maintenance before new intake.
- Feedback planning over records since the last run.
- Persisted run-level feedback plans plus per-feedback dispositions.
- Single-flight run lock and frozen feedback window.
- SDK-first Python implementation.
- Provider-neutral model adapter.
- Model calls through live `gh-agent-proxy`; no provider keys in the Curator sandbox.
- Intake evidence read-only, queue moves narrowly writable, Curator state read-write, logs read-only.
- Corpus PRs and allowlisted issues only.
- Assign owner-action and product/service issues to Roger by default.
- Hard cap on GitHub mutations per run with upload/feedback fairness; soft cap on planned actions.
- Capacity-deferred work retries on the next run.
- No proactive cleanup PRs in initial scope.
- No always-on worker until the manual lifecycle is proven.
- No live index rebuild or deploy responsibility in the Curator.
