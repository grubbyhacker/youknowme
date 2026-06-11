# YouKnowMe Feedback Processing

## Intake Contract

Feedback intake is intentionally small. A feedback record requires only `comment`; optional evidence
fields are `source_id`, `section_id`, `result_ids`, and `upload_id`. The legacy `category` field may
still be present on old or compatibility callers, but Curator must not depend on the caller choosing
the right category.

Feedback is sensitive intake data. It is not indexed or served directly.

## Curator Outcomes

Each actionable feedback record should become one GitHub outcome:

- `corpus_pr` in `grubbyhacker/ykmcorpus` when the agent can safely make a bounded corpus change.
- `corpus_issue` in `grubbyhacker/ykmcorpus` when the feedback is about corpus data quality but the
  agent cannot safely produce the change as a PR.
- `product_issue` in `grubbyhacker/youknowme` for functionality feedback, praise, duplicates,
  unclear feedback, non-actionable feedback, or any case the agent cannot confidently classify.

Feedback processing no longer uses local no-op, link-to-upload, deferred, or capacity-deferred
feedback states. The decision log records only durable GitHub outcomes for new feedback processing:
`issue_opened` or `pr_opened`.

## Live Processing

Manual live feedback processing uses `feedback_executor: "codex_proxy"`. Issue outcomes are created
through the broker. Corpus PR outcomes run an agentic Codex loop against a temporary `ykmcorpus`
checkout, validate the checkout, push a Curator branch, and create the pull request through the
broker. If the agent cannot produce a valid corpus PR, it falls back to a `product_issue` so the
feedback is not left idle.

Checkpoint advancement requires all included feedback records to have a durable GitHub outcome.
