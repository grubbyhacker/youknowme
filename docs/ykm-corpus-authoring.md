# YKM Corpus Authoring Guide

This guide captures the current conventions for writing source markdown in the private
`ykmcorpus` repo. The service must still ingest bare markdown, but curated frontmatter and clear
markdown structure materially improve stable retrieval, filtering, evals, and future Curator work.

## Frontmatter

Every curated corpus file should start with frontmatter:

```markdown
---
id: stable-human-readable-id
type: procedure
tags: [hot-tub, home, chlorine, maintenance]
related: [optional-related-source-id]
aliases: [optional-old-id]
---

# Clear Document Title
```

Supported fields today:

- `id` - Stable source identity. Required for curated files. Use lowercase kebab-case. Do not use
  raw paths as identity. Preserve this value across file moves and renames.
- `aliases` - Old IDs that should still resolve through `retrieve`. Use when renaming an ID.
- `type` - A broad content class used for filtering. Examples: `procedure`, `writing`,
  `writing-sample`, `work-history`, `interview-prep`, `resume`, `preference`, `project`, `skill`.
- `tags` - Specific lowercase kebab-case labels used for scoped queries. Tags are AND-filtered by
  default in `query`.
- `related` - Stable IDs of nearby documents. Phase 1 surfaces these links but does not traverse
  them automatically.
- `delivery_mode` - Reserved optional hook. Do not rely on behavior from it yet.

The current parser supports simple scalar values and bracket lists. Keep frontmatter boring:

```yaml
tags: [work-history, google, developer-productivity]
```

Avoid nested objects, multiline strings, dates that need YAML type handling, and clever formatting.

## IDs

Good IDs are durable names for the thing, not descriptions of the current file path:

- Good: `hottub-santa-cruz-bromine`
- Good: `substack-architectural-linting`
- Good: `resume-walk-google`
- Avoid: `homemaint-bromine-hot-tub-maintenance-notes-santa-cruz-md`

If a file is renamed, keep the same `id`. If an `id` must change, put the old value in `aliases`.

## Types

Use `type` for coarse routing. A query like "find the car review writing sample" should be able to
filter to `type: writing-sample`; a query about procedures should be able to filter to
`type: procedure`.

Current useful types:

- `procedure` - Stepwise or operational instructions.
- `writing` - Published or draft essays/posts.
- `writing-sample` - Samples meant to represent writing style or portfolio material.
- `work-history` - Career narratives, stories, recruiter prep, interview scripts.
- `interview-prep` - Company- or role-specific interview material.
- `resume` - Resume variants.
- `preference` - Communication, workflow, or personal preferences.
- `project` - Project plans and notes.
- `skill` - Instructions an agent should retrieve and execute.

Prefer extending this list deliberately instead of making one-off types.

## Tags

Tags do the safety and scoping work that semantic search alone cannot reliably do. Use tags to
separate same-kind subjects and to encode the filters an agent would naturally apply.

Tag guidance:

- Use lowercase kebab-case.
- Include subject tags: `hot-tub`, `google`, `crusoe`, `capital-one`.
- Include disambiguators: `home`, `santa-cruz`, `chlorine`, `bromine`.
- Include purpose tags: `maintenance`, `interview`, `career-summary`, `book-review`.
- Include domain tags where useful: `developer-productivity`, `agentic-development`,
  `architectural-linting`.
- Do not create tags for every noun in the document. Tags are for likely filters.

For ambiguous or safety-relevant subjects, tags are mandatory. The hot-tub pattern is the model:

```yaml
tags: [hot-tub, spa, santa-cruz, beach-house, bromine, maintenance, home-maintenance]
```

The agent can then query with `tags: [bromine]` or `tags: [santa-cruz]` instead of relying on
embedding similarity to keep chlorine and bromine procedures apart.

## Markdown Structure

Frontmatter helps identity and filtering, but semantic search currently embeds chunk text, not
frontmatter. That means the body still needs clear titles and headings.

For authored YKM notes, procedures, and work-history reference material, prefer:

- A single descriptive H1 near the top.
- H2/H3 sections for distinct topics or procedures.
- One procedure under one heading when possible.
- Descriptive headings that contain retrieval terms an agent might ask for.
- Short orienting prose before long lists when it clarifies what the section is about.

Avoid in authored YKM reference material:

- Headerless long documents.
- One large section containing many unrelated procedures.
- Titles only in filenames, with no matching H1 in the body.
- Generic headings like `Notes` when the section is actually about a specific task.

Imported writing samples, essays, resumes, and converted artifacts are different. Preserve their
natural shape unless evals show a real retrieval problem. Multiple H1s in an imported writing sample
are not automatically wrong; they are only worth changing when the file is meant to behave as one
YKM-authored reference document, or when retrieval/preview behavior suffers in practice.

Headerless files still ingest, but they may produce `headerless-or-single-section` warnings and can
be harder to retrieve reliably. Treat that warning as a signal, not a mandate. The practical contract
is: frontmatter carries identity and filters; body edits must preserve the document's purpose and
voice.

## Large Sections

Sections over the current parent preview budget produce `oversized-parent` warnings. This is not a
build failure, but it means `query` may return a bounded preview and a `retrieve` pointer rather than
the whole section.

When an authored reference document gets these warnings, prefer splitting it by natural H2 sections.
For example:

```markdown
# Interview Story: The Unofficial Tools Team

## Setup

## What We Built

## Why It Mattered

## Interview Delivery Notes
```

Do not split a single ordered procedure across unrelated headings just to satisfy the warning. Whole
procedure retrieval is more important than perfect warning counts. Likewise, do not rewrite imported
writing samples just to make warnings disappear if retrieval already works.

## Writing For Retrieval

A good corpus document should answer two questions:

1. What would the owner or an agent ask for?
2. Which exact file/section should that query retrieve?

Put likely query words in the H1/H2 or the first paragraph when they are natural. Do not keyword-stuff
or write for a search engine; write clear labels that a human would also appreciate.

Examples:

- Better H1: `# How to Build a Car Review`
- Weaker: no title, with the title only implied by the filename.
- Better H2: `## Startup After Refill`
- Weaker: `## Notes`

## Eval-Driven Maintenance

When adding or editing corpus material:

1. Add or update frontmatter.
2. Build with OpenRouter embeddings.
3. Run private evals from `.ykm/private-eval/`.
4. If retrieval fails, first improve headings/frontmatter/query filters.
5. Consider ranking/model changes only after good corpus structure still leaves the correct source
   outside top 3/top 5.

Current evidence supports `openai/text-embedding-3-small` through OpenRouter without a reranker.
