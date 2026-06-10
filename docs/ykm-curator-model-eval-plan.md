# YouKnowMe Curator Model Eval Plan

Status: implementation milestone for offline feedback-planning evals and manual real-run review.

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

Raw reports and production feedback excerpts must stay under ignored local paths such as
`.ykm/curator-model-eval/`. Commit only sanitized fixture shapes.

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

Latest hardening runs:

- `20260610T005442Z-f2f445d0687153b5`: deterministic dry-run on
  `youknowme:curator-model-feedback-evals-20260610-7fc17c7` passed with `capacity=0`, `19`
  no-action records, `3` upload-linked records, `2` corpus candidates, no checkpoint advancement,
  no decisions appended, and no GitHub mutations.
- `20260610T005317Z-563dd947d9bf42ef`: model dry-run on the same image failed closed on invalid
  model output after one model call. The deterministic fallback plan was preserved and had the same
  `capacity=0` shape as above.
- Earlier model runs copied the deterministic soft-cap pattern and spent thousands of tokens without
  improving action quality. Removing deterministic prompt seeding and simplifying the model schema
  reduced prompt coupling, but did not produce a useful model plan with the current model.

Conclusion: model-backed feedback planning is not ready for state advancement. The next practical
path is deterministic `state_only` for safe dispositions, while keeping model planning manual and
fail-closed.

The branch-head image `youknowme:curator-model-feedback-evals-20260610-fb0e09e` also guards
`state_only` checkpoint advancement: safe no-action/upload-linked decisions may be appended, but the
checkpoint is not advanced while included feedback still lacks a state-only decision.

Model candidate sweep on the same profile:

- `deepseek/deepseek-v4-flash`: failed closed on invalid model output; deterministic fallback was
  better and preserved.
- `openai/gpt-5-mini`: proxy/upstream returned HTTP 400 via 502 before a usable model response.
- `google/gemini-3.1-flash-lite`: returned valid JSON with low token use, but the plan was lower
  quality than deterministic; it grouped upload-linked records as no-action and changed targeted
  corpus candidates into a generic issue.
- `nvidia/nemotron-3-super-120b-a12b`: failed schema validation by returning the wrong top-level
  field.
- `stepfun/step-3.7-flash`: timed out through the proxy before a usable model response.

Current recommendation: do not spend more implementation time on model-backed batch planning until a
new candidate is selected specifically for strict schema adherence and conservative classification.

## Done Criteria

- A repeatable eval fixture suite exists for model feedback planning.
- The prompt/schema has tests around expected action quality.
- Manual model dry-runs produce stable, reviewable proposed actions.
- The runbook records how to inspect model planning quality and token use.

## Offline Eval Harness

Committed fixture cases live under `fixtures/curator/model-feedback-planning/` and are exercised by:

```bash
mise run curator-model-eval
```

The evals validate action shape and safety properties, not exact model prose. They cover:

- positive and non-actionable feedback producing `no_action`;
- targeted stale/wrong/missing content with source evidence producing `corpus_pr`;
- untargeted missing content routing to an owner-action issue instead of a speculative corpus edit;
- upload-linked feedback producing `link_to_upload`;
- repeated feedback grouping into one action with multiple feedback IDs;
- bad model outputs rejected for unknown evidence, missing feedback coverage, duplicate actions,
  invalid idempotency keys, unsupported corpus PRs, missing upload evidence, and wrong target repos.

The runner fails closed when model output violates these checks. A failed model plan leaves the
deterministic base plan in the report and records a `model-feedback-planning` failure with model name,
  validation error, proposed action count when available, and token usage when a model call happened.

## Scenario Eval Harness

The reusable quality suite lives in:

```bash
fixtures/curator/model-feedback-planning/scenarios.json
```

It uses sanitized feedback records with realistic comments and expected dispositions per feedback ID.
The scorer allows models to group records differently, but still checks that each record gets the
expected action type, classification, target repo, and cited evidence.

Run the committed offline checks with:

```bash
mise run curator-model-eval
```

Run live local model checks through any compatible Curator model proxy with:

```bash
CURATOR_MODEL_PROXY_URL=... CURATOR_MODEL_PROXY_TOKEN=... \
  mise run curator-feedback-model-live-eval
```

The live runner defaults to:

- `deepseek/deepseek-v4-flash`
- `google/gemini-3.1-flash-lite`
- `anthropic/claude-haiku-4.5`
- `nvidia/nemotron-3-super-120b-a12b`
- `anthropic/claude-sonnet-4.6`

Use repeated `--model` flags to override the list and repeated `--case` flags to narrow the suite.
The JSON report separates `schema_or_call_fail` from `quality_fail`, so strict JSON problems remain
visible independently from planning-quality problems.

Latest local live scenario run through the VPS model proxy:

- report: `.ykm/curator-model-eval/live-full-gated-46-runids.json` (ignored local artifact)
- prompt/schema changes: feedback comments included, strict OpenAI-compatible JSON schema, bounded
  classification enum, explicit category-to-action routing rules
- `deepseek/deepseek-v4-flash`: passed 26 of 28 expected feedback outcomes; failed only the two
  untargeted `missing_content` examples by returning `no_action`/`insufficient_evidence`
- `google/gemini-3.1-flash-lite`: passed 28 of 28 expected feedback outcomes
- `anthropic/claude-haiku-4.5`: passed 24 of 28 expected feedback outcomes; failed targeted
  `unclear_content` cleanup by routing those records to owner-action issues instead of
  `corpus_candidate`
- `anthropic/claude-sonnet-4.6`: passed 28 of 28 expected feedback outcomes
- `nvidia/nemotron-3-super-120b-a12b`: still failed the initial safe no-action gate by returning
  the wrong top-level response shape

Early signal: strict schema and explicit routing rules removed the earlier broad quality failures.
The remaining open question is whether Gemini/Claude continue to hold up on larger, mixed windows
and real production feedback, not whether the current prompt can elicit valid JSON for simple cases.

Do not assume all Curator model tasks should use the same model. Feedback planning, upload review,
PR comment classification, and PR body drafting have different cost and quality profiles. Choose
per task from eval evidence: use the cheapest model that passes that task's suite, keep a stronger
model as an escalation path, and avoid adding fallback complexity until a concrete failure mode needs
it. Haiku 4.5 is a plausible cheaper candidate for narrower classification tasks, but this feedback
planning suite does not yet support using it as the general last-resort model.

## Upload-Review Eval Harness

Upload review is a separate model task from feedback planning. The committed upload-review suite
lives in:

```bash
fixtures/curator/model-upload-review/scenarios.json
```

Run offline schema/scoring checks with:

```bash
mise run curator-model-eval
```

Run live upload-review checks through a configured Curator model proxy with:

```bash
CURATOR_MODEL_PROXY_URL=... CURATOR_MODEL_PROXY_TOKEN=... \
  mise run curator-upload-model-live-eval
```

The initial upload-review schema is intentionally small:

- normalized corpus markdown files;
- optional `.ykm/corpus-policy.yaml` additions represented as `allowed_types_add` and
  `allowed_tags_add`;
- short rationale and reason text.

The prompt advises the model to prefer existing vocabulary and propose small policy additions only
when current policy does not fit. The first implementation does not enforce fine-grained policy
limits in code; code review plus corpus validation are the hard gate. The initial cases cover:

- a dev-environment preference document that should not be forced into `skill` or `work-history`,
  and may propose `preference` plus missing tool/environment tags;
- a Santa Cruz hot tub manual summary that should remain a home-maintenance corpus document and may
  propose missing product/manual tags.

Latest local live upload-review run through the VPS model proxy:

- report: `.ykm/curator-model-eval/live-upload-review-v4.json` (ignored local artifact)
- `anthropic/claude-sonnet-4.6`: passed both initial upload-review cases
- `anthropic/claude-haiku-4.5`: passed the hot-tub/manual case, but routed the dev-environment
  preference/policy-expansion case to `needs_owner_action`
- `google/gemini-3.1-flash-lite`: failed both cases by returning markdown that did not parse as
  valid corpus frontmatter

Early upload-review signal: this task appears harder than feedback classification. Use Sonnet for
the first upload-review implementation unless a cheaper model later passes a larger suite.

The next upload implementation must add an observe step that validates model-produced drafts with
the corpus repository's own tests before any PR is considered ready. The prompt and eval scorer are
quality controls, not the authoritative gate for frontmatter or policy correctness.

## Manual Inspection Workflow

For a real manual launch, inspect `/output/run-report.json` and `/output/run-report.md` from the
`ykm-curator-dry-run-model` run. Confirm:

- `mode` is `dry_run`;
- `checkpoint_advanced` is `false`;
- `feedback_decisions_appended` is `0`;
- `github_mutation_count` is `0`;
- `model_call_count` is `1` when feedback was included;
- `model_token_count` is reasonable for the feedback window;
- proposed actions cite only included feedback and referenced evidence;
- positive/non-actionable records are not turned into GitHub-object actions;
- `corpus_pr` actions cite source, section, or upload evidence.
