# YouKnowMe Curator Prerequisite Milestone

Status: complete as of 2026-06-08.

This milestone removes known external blockers before implementing the Curator itself. It covers
GitHub app setup, broker capabilities, issue read/write flows, model proxying, sandbox runtime shape,
and production deployment wiring.

The goal is not to build the Curator yet. The goal is to make sure a future Curator can run with the
right permissions, brokered GitHub access, model access without provider keys, and a clear deployment
path.

## Current Starting Point

Already done before this milestone:

- `YKM Curator` GitHub App exists and is installed on private `grubbyhacker/ykmcorpus`.
- `GrubbyHacker Issue Reporter` can file issues in all needed repositories.
- `gh-agent-broker` is cloned next to this repo at `../gh-agent-broker`.
- `gh-agent-broker` already supports:
  - broker-owned GitHub App tokens;
  - brokered Git clone/fetch/push;
  - brokered PR creation;
  - brokered issue creation;
  - brokered issue/PR comments;
  - reporter MCP issue creation;
  - sandbox-broker worker launches;
  - deny-by-default repo, operation, branch, base-branch, permission, and metadata policy.

Important auth note:

- For app-installation access, the broker needs the GitHub App ID, installation ID, and private key.
  A GitHub App client secret is for user OAuth flows and is not enough by itself for installation
  access.

## Live Broker Status

As of 2026-06-08, the broker side has live YKM Curator support on `hermes-vps`.

GitHub app and broker identity:

- GitHub app context: `ykm-curator`.
- App ID: `3991340`.
- Installation ID: `138708452`.
- Repository: `grubbyhacker/ykmcorpus`.
- Worker principal: `BROKER_AGENT_ID=ykm-curator`.
- Worker secret: injected by sandbox-broker from the VPS environment.
- Workers receive broker credentials only. They do not receive GitHub tokens or the GitHub App PEM.

Broker-mediated Git:

- Workers should use only `http://broker:8080/git/grubbyhacker/ykmcorpus.git`.
- Workers should not use direct GitHub SSH or HTTPS remotes.
- Allowed branch pattern: `curator/<run-id>/<slug>`.
- Allowed base branch: `main`.
- Known caveat: broker issue `#27` tracks protection against reusing branches from merged PRs.

Required PR metadata:

```json
{
  "YKM-Curator-Run": "<run-id>",
  "YKM-Curator-Action": "upload|feedback|maintenance"
}
```

Live broker mutation E2E result:

- PR: `https://github.com/grubbyhacker/ykmcorpus/pull/1`.
- Branch: `curator/hermes-live-e2e-20260608/test-change`.
- Run ID: `20260608T022143Z-98cf2065a920f79a`.

Live read/reconciliation smoke result:

- Curator broker identity authenticated as `ykm-curator`.
- `grubbyhacker/ykmcorpus` PR list/read/files/comments/reviews endpoints returned successfully.
- `grubbyhacker/ykmcorpus` issue list/read/comments endpoints returned successfully.
- Reporter MCP `broker_search_issues`, `broker_get_issue`, and `broker_list_issue_comments`
  returned successfully for the reporter's allowlisted `grubbyhacker/gh-agent-broker` repository.

Broker image:

- Deployed locally on `hermes-vps` as `gh-agent-broker:ykm-prereqs-20260608`.
- The live broker and issue reporter are running this image.

New read APIs are available for broker-authorized principals:

- issue list/read/comments;
- PR list/read/files/comments/reviews/review-comments/review-threads;
- commit status;
- check runs.

Reporter MCP read tools are available:

- `broker_get_issue`
- `broker_search_issues`
- `broker_list_issue_comments`

Mutation budgets:

- Broker code supports durable mutation budgets.
- Current YKM concept:
  - `run_metadata_field: YKM-Curator-Run`
  - `action_metadata_field: YKM-Curator-Action`
  - `max_new_objects_per_run: 4`
  - `upload: 2`
  - `feedback: 2`
- Enforced for `pull.create` and `issue.create`.
- Over-budget denials return structured `capacity_deferred` responses.

Model proxy:

- Services: `litellm` and `gh-agent-proxy`.
- Docker/Hermes network endpoint: `http://gh-agent-proxy:8092/v1/model/call`.
- Host-local health endpoint: `http://127.0.0.1:8092/healthz`.
- Workers call with `Authorization: Bearer <GH_AGENT_PROXY_TOKEN>`.
- The token is injected by sandbox/broker environment and must not be hard-coded.
- Allowed models:
  - `google/gemma-4-26b-a4b-it`
  - `google/gemma-4-26b-a4b-it:free`
- The free model is policy-allowed but returned upstream 429 during testing; Curator should not
  depend on free-model availability.
- Proxy enforces bearer auth, allowed model list, per-run call/token budgets, request/response size
  limits, and timeout.
- Audit logs include run ID, model, decision, token counts, and errors. They do not include prompt
  bodies by default.

Sandbox mounts:

- Broker code supports operator-configured sandbox template `extra_mounts`.
- Callers cannot request arbitrary mounts.
- Mounts must be configured by the operator in the sandbox template.
- Unsafe paths and reserved mount targets are rejected.

## Workstream 1: GitHub App And Broker Config

Add a Curator-specific GitHub app context to broker production config.

Status: complete on `hermes-vps`.

Requirements:

- Add `YKM Curator` as a broker GitHub app context.
- Scope it to `grubbyhacker/ykmcorpus`.
- Store the app private key outside git.
- Configure the installation ID for `grubbyhacker/ykmcorpus`.
- Add a Curator broker agent identity, for example `ykm-curator-01`.
- Allow only the operations needed for corpus PR work.
- Allow branches matching `curator/<run_id>/<slug>` and deny `main` pushes.
- Allow base branch `main`.
- Require stable Curator metadata in PR bodies and broker requests:
  - `YKM-Curator-Run`
  - `YKM-Curator-Action`
  - `YKM-Curator-Feedback` when feedback evidence exists
  - `YKM-Curator-Upload` when upload evidence exists
  - broker operation/install markers already required by broker policy

Acceptance:

- Broker can mint installation access for `YKM Curator` without exposing tokens to agents.
- Curator broker identity can probe `grubbyhacker/ykmcorpus`.
- Curator broker identity can fetch the repo through brokered Git.
- Curator broker identity can push only `curator/*` branches.
- Push to `main` is denied.
- PR merge is not exposed or is denied.

## Workstream 2: Broker Read Surfaces

The Curator needs read access for reconciliation.

Status: complete in deployed prereq broker image.

Original requirement:

Current broker/reporter write paths were not enough.
Add either brokered CLI commands, broker REST endpoints, MCP tools, or a combination. The security
posture is acceptable as long as the broker keeps auth and policy enforcement central and does not
put raw GitHub tokens in the Curator container.

Required PR reads:

- List Curator PRs in `ykmcorpus` by branch prefix or stable body marker.
- Get one PR by number.
- Read PR body, head branch, base branch, state, merge status, author, labels, and current head SHA.
- List PR files.
- List issue comments on the PR conversation.
- List review submissions.
- List review comments or review threads, including unresolved state if available.
- Read commit statuses and/or check runs for the PR head SHA.

Required issue reads:

- Get one issue by number.
- Search/list issues by repo, state, labels, assignee, and Curator marker or dedupe key.
- List issue comments.

Acceptance:

- Curator can reconstruct existing Curator PR state after local metadata is missing.
- Curator can detect owner merge or close from any PR state.
- Curator can detect whether a linked owner-action issue is open or closed.
- Curator can dedupe proposed issues/PRs by marker before creating new GitHub objects.
- Read APIs return structured JSON stable enough for tests and state machines.

## Workstream 3: GitHub Mutation Limits And Fairness

Add broker-side limits that protect Roger's review queue.

Status: implemented for `pull.create` and `issue.create`.

Requirements:

- Hard per-run limit for new GitHub objects.
- Separate sub-limits or fairness rules so feedback issue/PR creation cannot starve upload PRs.
- Existing PR maintenance should not consume the same budget as new object creation.
- Capacity-deferred work should be clearly reported as retry-next-run, not owner-blocked.
- Broker audit logs should record allowed and denied mutation attempts.

Acceptance:

- A run that exceeds the new-object limit defers later actions without creating them.
- A noisy feedback batch cannot indefinitely prevent pending upload PR creation.
- PR maintenance comments/branch updates can still run when new-object budget is exhausted.

## Workstream 4: Issue Reporter Read Path

The existing reporter MCP write path is useful and should remain. Add read/query support so the
Curator can use the same issue channel for owner-action and product/service issue lifecycle.

Status: complete in deployed reporter MCP service.

Required MCP additions:

- `broker_get_issue`
- `broker_list_issues` or `broker_search_issues`
- `broker_list_issue_comments`
- reporter capabilities should advertise read support, repo allowlist, label allowlist, and body
  limits.

Policy:

- `ykmcorpus` issues may contain corpus/owner-fact work and must remain private.
- Public repos such as this YKM repo may receive product/service/implementation issues only when
  bodies contain no private corpus, intake, log, or personal-memory content.

Acceptance:

- Curator can read owner-action issue state without direct GitHub credentials.
- Curator can search for existing issues by dedupe key or marker before filing a duplicate.
- Reporter read responses are bounded and do not expose credentials or unrelated repo data.

## Workstream 5: Model Proxy In `gh-agent-proxy`

Build or adapt a self-hosted model proxy so the Curator never receives provider keys.

Status: live on `hermes-vps`.

Requirements:

- Likely home: `gh-agent-proxy`.
- Provider keys live only in the proxy service.
- Curator calls a narrow model endpoint through broker/proxy networking.
- Curator sandbox has no broad outbound internet access.
- Proxy supports at least one provider path suitable for structured outputs.
- Proxy enforces per-run call and token budgets.
- Proxy enforces request and response size limits.
- Proxy logs enough usage for Curator run reports without logging private prompt bodies by default.
- Hosted third-party proxy use requires an explicit later decision.

Acceptance:

- A synthetic Curator worker can make a typed model call through the proxy.
- Provider keys are not present in the Curator container.
- Budget exhaustion fails closed and is visible in the run report.
- Proxy can be deployed and reached on the private Docker network.

## Workstream 6: Curator Sandbox Template

Add a sandbox-broker template for manual Curator runs.

Status: complete on `hermes-vps`.

Live dry-run smoke:

- Sandbox template: `ykm-curator-dry-run`.
- Run ID: `20260608T230054Z-2256c2e7576399ca`.
- Report path:
  `/srv/hermes-sandbox-broker/runs/20260608T230054Z-2256c2e7576399ca/output/run-report.json`.
- Result: `status=pass`.
- Verified probes: task contract, intake mount, logs mount, output write, upload queue snapshot,
  feedback JSONL, query log JSONL, forbidden-secret absence, broker health, and model-proxy health.

Requirements:

- Curator image or placeholder worker image.
- Read-only intake evidence mount.
- Narrow writable mount for upload queue moves.
- Writable Curator state/output directory.
- Read-only logs mount.
- Read-only task contract at `/input/task.json`.
- Required run report under `/output`.
- Broker agent identity and secret injected as broker credentials only.
- Model-proxy endpoint credential injected only if model calls are enabled.
- No GitHub token, GitHub App private key, provider key, Docker socket, or arbitrary host mount.
- Network access only to the broker/proxy endpoints needed for the run.

Acceptance:

- Sandbox can launch a Curator dry-run worker.
- Worker can read intake/logs according to the mount contract.
- Worker can write run report/state output.
- Worker can reach broker/proxy and cannot reach arbitrary external network.
- Missing required output fails the sandbox run.

## Workstream 7: Deployment And Operator Runbook

Document how these services run together on `hermes-vps`.

Status: complete for Curator prerequisites.

Expected production shape:

```text
hermes-vps
  youknowme-phase1e
    - serves query/retrieve/upload/feedback
    - writes logs and intake
    - no GitHub or model-provider credentials

  gh-agent-broker
    - owns GitHub App private keys
    - brokers Git, PR, issue, and read/reconcile operations
    - writes audit logs

  broker-issue-reporter
    - MCP service for issue create/read/query
    - uses reporter broker identity

  sandbox-broker
    - launches manual Curator worker containers
    - controls mounts, network, task contract, and output contract

  gh-agent-proxy
    - self-hosted model proxy
    - owns provider keys
    - enforces model call/token budgets

  curator worker container
    - launched manually through sandbox-broker
    - receives broker/proxy credentials only
    - reads intake/log evidence
    - writes Curator state and reports
```

Acceptance:

- Private configs and secrets live outside git.
- Services can be restarted independently.
- Audit and run reports are retained in known protected paths.
- A manual dry run can verify broker auth, repo probe, issue read, PR read, and model-proxy reachability.

Live operator paths:

- Broker compose root: `/docker/gh-agent-broker`.
- Broker config: `/docker/gh-agent-broker/configs/production.yaml`.
- Sandbox config: `/docker/gh-agent-broker/configs/sandbox-beta.yaml`.
- Reporter config: `/docker/gh-agent-broker/configs/reporter.yaml`.
- Proxy config: `/docker/gh-agent-broker/configs/proxy.yaml`.
- Private env: `/docker/gh-agent-broker/.env`.
- Sandbox runs: `/srv/hermes-sandbox-broker/runs`.
- Sandbox audit log: `/srv/hermes-sandbox-broker/audit/sandbox-audit.jsonl`.
- YKM intake mount source: `/opt/youknowme/intake`.
- YKM logs mount source: `/opt/youknowme/logs`.

Live service names:

- `gh-agent-broker-broker-1`
- `gh-agent-broker-sandbox-broker-1`
- `gh-agent-broker-issue-reporter-1`
- `gh-agent-broker-gh-agent-proxy-1`
- `gh-agent-broker-litellm-1`
- `youknowme-phase1e`

Manual smoke commands should launch through sandbox-broker MCP, not direct Docker. Direct Docker is
acceptable only as a local image/entrypoint sanity check.

## Branch Reuse Policy Until Broker Issue `#27`

Until broker-side branch-reuse protection is fixed, the Curator must not reuse branch names.

- Every new Curator attempt uses a fresh run ID in the branch name generated by sandbox-broker.
- An open Curator PR may be updated on its existing branch while responding to review feedback.
- A merged or closed Curator PR branch is terminal. A later attempt must use a new branch.
- Reconciliation must dedupe by stable PR body markers such as `YKM-Curator-Run`,
  `YKM-Curator-Action`, `YKM-Curator-Upload`, and `YKM-Curator-Feedback`, not by branch reuse.
- If a stale local Curator state conflicts with GitHub state, GitHub state wins.

## Suggested Execution Approach

Use two agents in parallel after this milestone is accepted:

- One agent in this repo (`aboutmemcp`) should keep the Curator contracts, runbook, and future
  Curator skeleton aligned with the design.
- A separate agent in `../gh-agent-broker` should implement broker, reporter, sandbox, and proxy
  prerequisites on a feature branch.

This split is better than one agent jumping between repos because the broker repo has its own
development rules, tests, and release artifacts. The interface between the two agents should be this
milestone plus `docs/ykm-phase4-curator.md`.

## Done Criteria

This prerequisite milestone is done when:

- Broker config supports the `YKM Curator` app and Curator broker identity.
- Broker exposes enough PR and issue reads for Curator reconciliation.
- Issue reporter supports issue read/query as well as create.
- GitHub mutation limits and upload/feedback fairness are enforced.
- `gh-agent-proxy` or equivalent self-hosted proxy can make budgeted model calls for a sandboxed
  worker.
- Sandbox-broker has a Curator dry-run template with the right mounts and network limits.
- A manual dry-run worker can produce a run report proving all external dependencies are reachable
  without raw GitHub or provider credentials in the worker.

No known prerequisite blockers remain before the dense Curator milestone.
