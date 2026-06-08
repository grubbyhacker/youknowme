# YouKnowMe Corpus CI Artifact Prerequisite Milestone

Status: planning draft.

This milestone makes the private `grubbyhacker/ykmcorpus` repository automatically validate corpus
shape and produce official LanceDB-backed YouKnowMe index artifacts after successful `main` branch
builds.

The goal is not yet to deploy those artifacts to the VPS. The goal is to make `ykmcorpus/main`
produce a trustworthy, immutable RAG database artifact that a later deployer can safely promote.

## Goal

Approved corpus PRs should become deployable index artifacts as soon as is reasonable after merge,
without a manual rebuild on Roger's machine.

The production boundary remains:

- `ykmcorpus` owns markdown source.
- YKM build code compiles markdown into the generated index artifact.
- YKM serving consumes only an official generated artifact mounted read-only.
- The serving container does not receive corpus repo credentials, GitHub write credentials, or corpus
  source checkouts.
- The Curator proposes corpus PRs; it does not build, deploy, or merge.

## Current Starting Point

The current YKM build pipeline is already offline-artifact shaped.

Command:

```bash
YKM_EMBEDDING_PROVIDER=openrouter uv run ykm build --corpus <corpus-checkout> --out <index-dir>
```

Build output directory:

```text
<index-dir>/
  manifest.json
  chunks.jsonl
  warnings.jsonl
  quarantine.jsonl
  lancedb/
```

The current `manifest.json` records:

- schema version;
- build id;
- source commit;
- embedding provider;
- embedding model;
- embedding dimensions;
- created timestamp;
- chunk count;
- quarantined count;
- warning count.

The serving process loads `manifest.json`, `chunks.jsonl`, and `lancedb/` from the mounted index
path. It still embeds user queries at runtime, so the query-side embedding provider/model/dimensions
must match the artifact that was built in CI.

Known current gap:

- `docs/ykm-container-packaging.md` describes manifest-derived embedding configuration, but current
  code still uses environment-based provider selection directly. This milestone should close that gap
  before CI-built production artifacts are promoted.

## Desired `ykmcorpus` CI Shape

Two CI tiers should exist in `ykmcorpus`.

### Pull Request Validation

Runs on PRs that change corpus-relevant files.

Purpose:

- catch Curator-produced bad markdown before Roger merges;
- catch human mistakes before they reach `main`;
- avoid spending embedding API budget for every PR unless needed.

Recommended checks:

- install YKM build/test tooling at a pinned version or from an explicit repo/ref;
- run markdown/frontmatter shape validation;
- run secret scanning using the same high-confidence scanner as the YKM build path;
- run a fake-embedding build to prove the corpus can compile structurally;
- run lightweight retrieval/eval fixtures if available and non-private enough for the repo;
- fail on policy errors, warn on non-blocking quality issues.

This tier should not upload production index artifacts.

### Main Branch Artifact Build

Runs only after changes land on `main`.

Trigger:

- `push` to `main`;
- restricted with `paths` to corpus files, corpus validation config, eval fixtures, and workflow/build
  configuration;
- no artifact build on feature branches, PR branches, tags, or manual Curator branches unless Roger
  explicitly requests a one-off diagnostic workflow.

Purpose:

- run the full structural validation suite;
- build the real OpenRouter-backed LanceDB index;
- run retrieval eval/smoke checks against the produced artifact;
- upload a versioned immutable artifact and its checksum/report.

Recommended workflow controls:

- `timeout-minutes` on build jobs.
- `concurrency` with `cancel-in-progress: true` for redundant main builds.
- `OPENROUTER_API_KEY` injected only from GitHub encrypted secrets.
- repository or account-level OpenRouter spending cap for the CI key.
- minimal `GITHUB_TOKEN` permissions, normally `contents: read` plus only what artifact upload
  requires.
- short artifact retention at first, then increase once artifact size and deploy needs are known.

## Artifact Contract

Initial artifact name:

```text
youknowme-index-<source_commit>-<build_id>.tar.gz
```

Initial sidecar files:

```text
youknowme-index-<source_commit>-<build_id>.sha256
youknowme-index-<source_commit>-<build_id>.build-report.json
```

The tarball should unpack to a single directory containing:

```text
manifest.json
chunks.jsonl
warnings.jsonl
quarantine.jsonl
lancedb/
```

Recommended manifest additions before this becomes deployable:

- `artifact_schema_version`;
- `corpus_repo`;
- `corpus_ref`;
- `corpus_commit`;
- `build_code_repo`;
- `build_code_ref`;
- `build_code_commit`;
- `artifact_created_by`;
- `artifact_sha256`;
- `eval_passed`;
- `shape_validation_passed`;
- `quarantine_policy`;
- optional `workflow_run_id`;
- optional `workflow_run_url`.

The manifest should remain enough for `health` to prove what is live:

- corpus commit;
- build id;
- embedding provider/model/dimensions;
- creation time;
- chunk and quarantine counts.

## Corpus Shape Validation

The validation suite should be stricter than ingest. Bare markdown can still be accepted by the core
build path, but the production corpus should have a higher quality bar.

Initial blocking checks:

- all files intended for corpus are UTF-8 `.md` files;
- no high-confidence secrets;
- no unsupported HTML/script or embedded data URL payloads;
- unique `id` values when present;
- no alias collides with another source id or alias;
- `type` is present or inferable and belongs to an allowed vocabulary;
- `tags` are present for durable corpus docs and belong to an allowed vocabulary or documented
  extension list;
- `related` entries resolve to existing source ids or documented external references;
- file size and total document size stay within configured limits;
- heading depth stays within configured limits;
- section size stays within configured limits, or fails if it would create poor retrieval behavior;
- generated chunk count per document stays within configured limits;
- no empty documents and no headings with no useful body;
- stable id behavior is tested across file rename when explicit `id` is present.

Initial non-blocking warnings:

- generated id fallback was used;
- headerless or single-section document;
- oversized parent section that will be returned as a preview;
- uncommon tag/type introduced without a vocabulary update;
- very short or very long title;
- missing aliases for renamed or migrated source material.

The exact policy should live in `ykmcorpus`, not in chat history. A future implementation can use a
small corpus policy file such as:

```text
.ykm/corpus-policy.yaml
```

That file can define allowed types, allowed tags, size limits, heading-depth limits, and warning vs.
error thresholds.

## YKM Repo Compatibility Work

This repository needs a small amount of work before CI-built artifacts can be treated as production
inputs.

Required:

- Add a manifest-derived embedding provider factory for serving/query.
- Fail startup if runtime embedding configuration conflicts with the mounted artifact manifest.
- Preserve explicit env overrides only for local development or diagnostics, and make mismatches
  visible.
- Add tests proving an index built with one embedding model is queried with the same provider/model
  metadata.
- Add `ykm validate-index` and `ykm package-index` commands for private corpus CI.
- Add artifact smoke tests that unpack a tarball, load `YkmIndex`, run one query, and run one
  retrieve.

Nice to have:

- a dedicated `ykm validate-corpus` CLI command;
- a machine-readable build report separate from `manifest.json`;
- checksums for large index files;
- artifact schema versioning tests.

## Deployment Boundary

This milestone stops at artifact production.

Out of scope:

- automatically downloading the artifact to `hermes-vps`;
- swapping `/opt/youknowme/index`;
- restarting the serving container;
- adding webhooks or a deploy broker;
- hot reload;
- rollback automation.

A later deployment milestone should consume only successful `main` artifacts and should promote them
with an atomic swap plus smoke checks.

## Security And Least-Privilege Notes

GitHub Actions is an acceptable build location for this design if Roger explicitly accepts GitHub as
a sensitive data processor for private corpus content and embedding requests.

Secrets:

- `OPENROUTER_API_KEY` should live only in GitHub encrypted secrets for `ykmcorpus`.
- The CI key should have a strict provider-side spending cap.
- The key should be used only through environment variables at runtime.
- Workflow logs must not print prompts, corpus content, raw embeddings, or secret values.

Permissions:

- PR validation should use read-only repository permissions.
- Main artifact builds should use the minimum permissions needed to read source and upload artifacts.
- No workflow should receive a GitHub token with corpus write rights for this milestone.
- No workflow should receive VPS SSH credentials for this milestone.

Cost controls:

- Build artifacts only on `main`.
- Use `paths` filters to skip non-corpus changes where safe.
- Use concurrency cancellation to avoid redundant builds.
- Use job timeouts to fail closed on provider hangs.
- Monitor artifact size and retention.

## Acceptance

This milestone is complete when:

- `ykmcorpus` PRs run structural markdown/corpus validation.
- Bad Curator-authored markdown fails before merge.
- `ykmcorpus/main` successful builds produce one official LanceDB index artifact.
- The artifact includes manifest metadata tying it to corpus commit, build code, and embedding model.
- The artifact can be downloaded, unpacked, loaded by YKM, and queried without rebuilding.
- Main artifact builds are branch/path constrained, timeout bounded, and concurrency controlled.
- OpenRouter credentials are injected only through GitHub encrypted secrets.
- YKM serving can derive or validate query-side embedding configuration from the mounted artifact.
- No deploy or VPS mutation is required to satisfy this milestone.

## Open Questions

- Should production artifacts be stored as short-retention Actions artifacts, GitHub Releases,
  packages, or a private object store?
- Should PR validation require explicit `id` on every production corpus document, or keep generated
  ids as warnings?
- What is the initial allowed tag/type vocabulary, and where should it live in `ykmcorpus`?
- Should warnings fail `main` artifact builds after an initial grace period?
- What private eval set is safe and useful to run in `ykmcorpus` CI?
- Should artifact signing be added now, or is GitHub artifact provenance plus checksum enough for the
  first deployment milestone?
