# YouKnowMe Phase 3 Intake Runbook

Phase 3 adds guarded write intake without integrating content into the corpus. YKM stages uploads
and feedback on the VPS filesystem; a later Curator drains that queue, opens PRs/issues, and cleans
up processed intake.

## Runtime Shape

Production keeps the existing read path unchanged:

- `/data/index` - read-only generated index artifact.
- `/data/logs` - protected query logs.
- `/data/intake` - protected staged uploads and feedback.

The serving container still has no GitHub credential, no corpus checkout, and no merge capability.
It can only write runtime logs and intake files.

## Tools

`upload` stages bounded agent-curated markdown for future review. It does not publish, index, merge,
or choose final corpus paths.

Limits:

- 1 to 10 markdown files.
- 20 KB per file.
- 80 KB total payload.
- UTF-8 markdown only.
- Simple `.md` filenames only; filenames are normalized to lowercase kebab-case.
- Reject path traversal, binary/control bytes, HTML/script payloads, data URLs, and high-confidence
  secret patterns.

`feedback` records bounded observations about content quality. It is inert: not indexed, not returned
by `query`, and not visible to future agents except through future Curator/human workflows.

Limits:

- Comment max 2,000 characters.
- Up to 10 result pointers.
- No attachments in Phase 3 feedback; content that should be preserved belongs in `upload`.

## Filesystem Contract

Uploads are written only to pending bundles:

```text
/data/intake/uploads/pending/<upload_id>/
  manifest.json
  files/
    <normalized>.md
```

The manifest records schema version, upload id, timestamp, auth path, current build id, purpose,
suggested metadata, file hashes, byte counts, sanitizer warnings, and status `pending`.

Feedback appends to:

```text
/data/intake/feedback/feedback.jsonl
```

Records include timestamp, feedback id, category, bounded comment, optional source/result pointers,
optional upload id, auth path, and build id.

## Future Curator Queue Contract

Phase 3 only writes `pending`. The Phase 4 Curator may use these directories:

```text
/data/intake/uploads/pending/
/data/intake/uploads/claimed/
/data/intake/uploads/processed/
/data/intake/uploads/rejected/
/data/intake/uploads/archive/
```

The Curator should atomically move a bundle from `pending` to `claimed`, create a PR or issue from
the claimed bundle, and write a `curator.json` with its decision, PR/issue number, branch, and
timestamps. After a PR is merged, it may move the bundle to `processed` and then archive or delete it
according to the retention policy. Rejected bundles move to `rejected` with a reason.

Directory rename is the intended lock primitive. Do not introduce a database until filesystem
contention is real.

## Rebuild And Redeploy Forward Design

Phase 3 does not automate corpus integration. The intended later flow is:

1. Curator opens a PR against the private corpus repo from staged upload/feedback evidence.
2. Human reviews and merges.
3. Merge to corpus `main` triggers a full index rebuild.
4. The rebuild runs eval/smoke checks and produces a manifest-backed artifact.
5. Deployment atomically swaps `/docker/youknowme/data/index-current` to the new artifact and restarts YKM.
6. Smoke checks verify `health`, `query`, `retrieve`, and fail-closed auth behavior.

Preferred maturation is a GitHub Actions build on corpus merge if OpenRouter-backed embeddings remain
CI-feasible. A simpler interim path is a VPS cron job that pulls corpus `main`, rebuilds, runs evals,
and atomically swaps the index only on success. In both cases, YKM must never serve staged upload
content directly.
