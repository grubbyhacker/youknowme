# YouKnowMe Curator Model Eval Plan

Status: next milestone plan after manual model-backed dry-run launch.

## Goal

Harden model-backed feedback planning quality before enabling any state advancement or GitHub
mutations.

The current infrastructure milestone is complete: `ykm-curator-dry-run-model` can manually launch
through sandbox-broker, call `gh-agent-proxy`, use the dedicated `OPENROUTER_CURATOR_API_KEY`, and
produce a passing dry-run report with zero state writes and zero GitHub mutations.

## Scope

1. Inspect the latest model dry-run report and compare model actions with deterministic actions.
2. Identify bad or noisy model behavior:
   - wrong action type,
   - weak or missing evidence,
   - over-eager `corpus_pr`,
   - duplicated actions,
   - poor no-action classifications,
   - unhelpful grouping,
   - unnecessary token use.
3. Capture a small sanitized eval fixture set from real feedback windows.
4. Add tests that run model-planning validation against fixture responses or fixed expected actions.
5. Tune the feedback-planning prompt/schema until the dry-run output is stable and useful.
6. Run several manual `ykm-curator-dry-run-model` launches and record the results.

## Non-Goals

- Do not enable `state_only` yet.
- Do not append feedback decisions.
- Do not advance checkpoints.
- Do not create issues or PRs.
- Do not give the Curator sandbox provider keys.
- Do not add the model profile to the timer principal.

## Initial Evidence

Known passing live run:

- run ID: `20260609T223412Z-08b3f3abea519140`
- profile: `ykm-curator-dry-run-model`
- model: `deepseek/deepseek-v4-flash`
- report status: `pass`
- model calls: `1`
- model tokens: `7461`
- GitHub mutations: `0`
- checkpoint advanced: `false`
- feedback decisions appended: `0`
- partial failures: `[]`

## Done Criteria

- A repeatable eval fixture suite exists for model feedback planning.
- The prompt/schema has tests around expected action quality.
- Manual model dry-runs produce stable, reviewable proposed actions.
- The runbook records how to inspect model planning quality and token use.
