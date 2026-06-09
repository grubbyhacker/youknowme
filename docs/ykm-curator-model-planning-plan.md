# YouKnowMe Curator Model Planning Plan

Status: planned; next Phase 4 milestone after the live Curator launcher.

## Summary

Add manual-only model-backed feedback planning for Curator through `gh-agent-proxy`. This milestone
may read broker state and make one bounded model call, but it must not create issues, PRs, branches,
queue moves, checkpoints, or feedback decisions.

The existing hourly `ykm-curator-dry-run` timer remains the safe baseline and must stay unchanged.

## Key Changes

- Add a manual-only sandbox launch profile named `ykm-curator-dry-run-model`.
- Keep the task in `mode: dry_run`, with GitHub mutation budget set to zero.
- Enable broker reads for reconciliation and require the broker boundary.
- Enable model-backed feedback planning only when explicitly requested in the task/profile.
- Use `gh-agent-proxy` for model calls; Curator must never receive provider keys.
- Validate model output against the typed `FeedbackPlanningModelOutput` contract before it can affect
  `feedback-plan.json`.
- Fail closed and write a report on proxy errors, denied model, invalid output, or budget exhaustion.

## Model Access

Configure `gh-agent-proxy` and LiteLLM on `hermes-vps` with prompt logging disabled.

Preferred model order:

1. `deepseek/deepseek-v4-flash`
2. `openai/gpt-5-mini`
3. `google/gemini-3.1-flash-lite`
4. `nvidia/nemotron-3-super-120b-a12b`
5. `stepfun/step-3.7-flash`

The bench-only free alias `nvidia/nemotron-3-super-120b-a12b-free` may be configured for manual
testing, but production Curator should not depend on free-tier availability.

## Test Plan

- Local tests cover fixture-backed model planning success, invalid model output rejection, budget
  failure, required-proxy failure, and unchanged deterministic planning when model planning is off.
- VPS proxy smoke confirms health, denied-model rejection, and one successful call through the first
  available preferred model.
- Manual `ykm-curator-dry-run-model` launch confirms the report shows broker reads and one model
  call while still reporting zero GitHub mutations, zero checkpoint advancement, zero queue moves,
  and zero decision appends.

## Assumptions

- First production-facing model run is manual only.
- No GitHub issue or PR creation is enabled in this milestone.
- Upload review remains deterministic/off.
- Model proposes; Curator validates and reports.
