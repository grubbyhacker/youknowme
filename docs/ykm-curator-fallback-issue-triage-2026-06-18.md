# Curator Fallback Issue Triage - 2026-06-18

This note summarizes the open `YouKnowMe Curator fallback` GitHub issues that
were created in `grubbyhacker/youknowme` on 2026-06-15 and correlates them with
the live intake queue on `hermes-vps`.

## Summary

- Reviewed 19 still-relevant fallback issue records: #58-61, #64-78.
- Closed 14 stale fallback issues: #58-61, #64-72, #78.
- Left 5 issues open for owner decision: #73-77.
- Root cause pattern: the Curator decision log already had earlier decisions
  for many feedback records, but a later fallback issue reporter pass opened
  GitHub issues anyway.

## Closed As Stale Fallback Noise

These were closed with `not planned` because queue state showed they had already
been resolved before the fallback issue was opened, or they were operational
smoke/no-op records.

| Issue | Feedback | Queue evidence | What it was | Closure reason |
|---|---|---|---|---|
| [#58](https://github.com/grubbyhacker/youknowme/issues/58) | `fb_20260606_213452_9a6b8666` | `no_action_non_actionable`, 2026-06-10 | Prefer inline markdown review for uploads | Already decided non-actionable before issue creation |
| [#59](https://github.com/grubbyhacker/youknowme/issues/59) | `fb_20260606_213456_d3bca92e` | `no_action_non_actionable`, 2026-06-10 | Omit meta-communication from upload markdown | Already decided non-actionable before issue creation |
| [#60](https://github.com/grubbyhacker/youknowme/issues/60) | `fb_20260606_213500_db0a045a` | `no_action_non_actionable`, 2026-06-10 | Remove generic safety boilerplate from uploads | Already decided non-actionable before issue creation |
| [#61](https://github.com/grubbyhacker/youknowme/issues/61) | `fb_20260606_213503_19125419` | `no_action_non_actionable`, 2026-06-10 | Do not include unsupported `status: review-draft` frontmatter | Already decided non-actionable before issue creation |
| [#64](https://github.com/grubbyhacker/youknowme/issues/64) | `fb_20260606_213507_4746553b` | `no_action_non_actionable`, 2026-06-10 | Preserve related topics in upload frontmatter | Already decided non-actionable before issue creation |
| [#65](https://github.com/grubbyhacker/youknowme/issues/65) | `fb_20260606_213511_5015ac83` | `no_action_non_actionable`, 2026-06-10 | Preserve manual chemistry content as fallback information | Already decided non-actionable before issue creation |
| [#66](https://github.com/grubbyhacker/youknowme/issues/66) | `fb_20260606_213514_8e690ede` | `no_action_non_actionable`, 2026-06-10 | Avoid excessive tool churn while preparing uploads | Already decided non-actionable before issue creation |
| [#67](https://github.com/grubbyhacker/youknowme/issues/67) | `fb_20260606_213520_6ab1e4ec` | `no_action_non_actionable`, 2026-06-10 | Tool discovery note about targeted `list_resources` | Already decided non-actionable before issue creation |
| [#68](https://github.com/grubbyhacker/youknowme/issues/68) | `fb_20260606_213523_a3e1be27` | `no_action_non_actionable`, 2026-06-10 | Meta-note to bound feedback records | Already decided non-actionable before issue creation |
| [#69](https://github.com/grubbyhacker/youknowme/issues/69) | `fb_20260606_213527_a860b6d2` | `no_action_non_actionable`, 2026-06-10 | Consolidated correction for an over-granular feedback batch | Already decided non-actionable before issue creation |
| [#70](https://github.com/grubbyhacker/youknowme/issues/70) | `fb_20260606_213532_a94d87c8` | `no_action_non_actionable`, 2026-06-10 | Explicit no-further-action note | Already decided non-actionable before issue creation |
| [#71](https://github.com/grubbyhacker/youknowme/issues/71) | `fb_20260606_213536_fb409cab` | `no_action_non_actionable`, 2026-06-10 | Correction that an upload had not yet been staged | Already decided non-actionable before issue creation |
| [#72](https://github.com/grubbyhacker/youknowme/issues/72) | `fb_20260606_213540_5e51ccbe` | `no_action_non_actionable`, 2026-06-10 | Final process note to avoid cascading feedback entries | Already decided non-actionable before issue creation |
| [#78](https://github.com/grubbyhacker/youknowme/issues/78) | `fb_20260611_225232_47323102` | Fallback issue opened; feedback text is operational smoke | Intake migration smoke check | No product or corpus action |

## Open For Owner Decision

These remain open and were labeled with `ykm-curator` and `feedback`. Product
or process candidates also have `enhancement`; the review-policy item has
`question`.

| Issue | Feedback/upload evidence | What it is | Recommended decision |
|---|---|---|---|
| [#73](https://github.com/grubbyhacker/youknowme/issues/73) | `fb_20260606_213952_3df1fa9d` | Product feedback: the feedback tool should say it writes to a protected Curator review queue and should not be used as scratchpad/audit/iterative correction stream. | Keep if we want a product change to feedback tool guidance. Otherwise close as covered by policy/docs. |
| [#74](https://github.com/grubbyhacker/youknowme/issues/74) | `fb_20260606_213959_8ddfe7ba` | Product feedback: refine feedback schema/categories so `agent_note` is clearly durable product/process feedback, possibly adding/documenting `product_feedback`. | Keep or merge into #73 as the schema side of the same problem. |
| [#75](https://github.com/grubbyhacker/youknowme/issues/75) | `fb_20260606_214003_c0cf92ee` | Product feedback: add caller-side guardrails/rate limiting to prevent feedback spam in one interaction. | Keep as the most concrete product fix from the batch. |
| [#76](https://github.com/grubbyhacker/youknowme/issues/76) | `fb_20260606_215317_90520067`, upload `upl_20260606_214512_ba212e24`; upload is processed and PR [ykmcorpus#6](https://github.com/grubbyhacker/ykmcorpus/pull/6) is merged. | Product feedback: upload tool schema examples should show the exact `files` object shape with filename/content keys. | Keep only if this is still not fixed in tool descriptions. The linked upload itself is done. |
| [#77](https://github.com/grubbyhacker/youknowme/issues/77) | `fb_20260611_030225_ffcb8be7`, upload `upl_20260611_030213_d8066f73`; PR [ykmcorpus#8](https://github.com/grubbyhacker/ykmcorpus/pull/8) is merged. | Process feedback: an upload was staged before Roger reviewed the markdown. | Likely close as stale if the later PR review/merge was acceptable; keep only if we want a product rule requiring explicit pre-upload approval. |

## Suggested Cleanup Plan

1. Decide whether #73 and #74 should be merged into a single feedback-tool
   guidance/schema issue.
2. Keep #75 if we want a concrete product guardrail against feedback spam.
3. Check current upload tool descriptions before deciding #76.
4. Close #77 unless we want to formalize "Roger must review markdown before
   upload" as a tool-level requirement rather than a process lesson.
5. Fix the Curator fallback reporter so it does not open issues for feedback
   records that already have terminal decisions in `curator-decisions.jsonl`.

