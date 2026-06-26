# YouKnowMe Corpus Change Processing

## Intake Contract

The agent-facing write tool for modifying existing corpus content is `corpus_change`.
Use `upload` for new file bundles.

A corpus change request requires:

- `intent`: `add_to_existing`, `update_existing`, or `remove_from_existing`
- `instruction`: the bounded change Roger wants made

Optional evidence fields are `source_id`, `section_id`, `result_ids`, and `upload_id`.
Target evidence is helpful but not required; Curator may search the corpus when the request is clear
but untargeted.

Corpus change intake is sensitive. It is not indexed or served directly.

## Curator Outcomes

Each actionable corpus change request becomes one durable `grubbyhacker/ykmcorpus` outcome:

- `corpus_pr` when Curator can safely make a bounded corpus edit.
- `corpus_issue` when the request is corpus work but Curator cannot safely produce a PR.

This workflow does not create YouKnowMe product issues and does not use product-feedback,
non-actionable, or owner-escalation categories.

## Live Processing

Manual live corpus change processing uses `feedback_executor: "codex_proxy"` in the existing Curator
task schema. Corpus PR outcomes run an agentic Codex loop against a temporary `ykmcorpus` checkout,
validate the checkout, push a Curator branch, and create the pull request through the broker.
The Codex worker may leave uncommitted edits or make local commits on the prepared branch. Curator
validates and applies guardrails to the full branch delta either way, then owns push, PR creation,
and feedback decision state.

If the agent cannot produce a valid corpus PR, Curator files a bounded `ykmcorpus` issue that
preserves the instruction, supplied evidence, and failure reason.

Checkpoint advancement requires all included corpus change records to have a durable GitHub outcome.
