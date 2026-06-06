# YouKnowMe Phase 2 Runbook

Status: complete. Keep this document as the maintenance playbook for future retrieval or triggering
issues.

Phase 2 improves retrieval from evidence. It should not add write paths, Curator behavior, blob/image
serving, or new public surfaces.

## Starting State

Phase 1E is live on `hermes-vps` at `https://mcp.fleiglabs.cc/mcp` through the existing Cloudflare
Tunnel / Access route. Production serves the Phase 1 index from a read-only mount and writes protected
query logs under `/opt/youknowme/logs`.

The completion evidence says:

- ChatGPT, Claude, Hermes Agent, and Claude.ai can retrieve production data through the live MCP path.
- Red-team probes fail closed.
- Query logs are source-pointer-only and do not include raw query text or returned content.
- The private baseline has 19 cases and passes 19/19 against build
  `f1fa6a81d97e4650b5775f717ad8c5dd`; 18 pass at top 1, all pass at top 3/top 5.
- Existing eval evidence does not justify a reranker, payload/ranking changes, or corpus changes.

## Phase 2 Tracks

Keep these separate:

- **2a Retrieval quality:** private eval cases, ranking and payload tuning, reranker only if evals
  show correct sources often appear in top 5 but not top 1/top 3.
- **2b Authoring and conventions:** corpus frontmatter, headings, IDs, aliases, and served guidance
  for writing durable memory.
- **2c Orientation and delivery shaping:** orient-then-retrieve and `delivery_mode`; defer until
  eval and usage evidence show the need.
- **2d Triggering/tool adoption:** improve MCP tool descriptions and client setup instructions when
  agents answer owner-specific questions from training data or web search instead of consulting
  YouKnowMe.

## Evidence Loop

1. Review protected usage evidence.
   - Use VPS query logs only as sensitive input.
   - Do not copy raw logs, real corpus text, or secrets into this repository.
   - Logs contain source IDs and auth/client signals, so treat them like corpus-adjacent private data.

2. Create private eval cases.
   - Store usage-derived cases under `.ykm/private-eval/`; this directory is git-ignored.
   - Prefer source IDs, section IDs, paths, and absent-source checks over copying private content.
   - Include both positive expectations and negative expectations.

3. Run the private eval.

   ```bash
   YKM_EMBEDDING_PROVIDER=openrouter uv run ykm eval \
     --index .ykm/real-index \
     --cases .ykm/private-eval/ykmcorpus.json \
     --out .ykm/private-eval/ykmcorpus-results.json
   ```

4. Diagnose misses in this order.
   - Inspect result provenance:

     ```bash
     YKM_EMBEDDING_PROVIDER=openrouter uv run ykm inspect-result \
       --index .ykm/real-index <result-id>
     ```

   - Check whether filters would have made the intended source obvious.
   - Check whether body headings contain natural retrieval terms.
   - Check whether the source has stable frontmatter `id`, `type`, `tags`, `related`, and aliases.

5. Fix the lowest-level cause first.
   - Corpus structure fixes beat ranking changes.
   - Filter/tag fixes beat payload changes.
   - Payload breadth tuning is separate from link traversal.
   - If usage repeatedly returns complementary artifacts for one task, record it as a future Curator
     consolidation candidate rather than silently merging sources in retrieval.
   - Reranking is justified only after healthy corpus structure still leaves expected sources outside
     top 1/top 3 while present in top 5.

6. Rebuild and re-run.

   ```bash
   YKM_EMBEDDING_PROVIDER=openrouter mise run real-smoke
   YKM_EMBEDDING_PROVIDER=openrouter uv run ykm eval \
     --index .ykm/real-index \
     --cases .ykm/private-eval/ykmcorpus.json \
     --out .ykm/private-eval/ykmcorpus-results.json
   ```

## Eval Case Shape

Private eval files use the same schema as `fixtures/eval/synthetic.json`:

```json
{
  "cases": [
    {
      "name": "short-stable-name",
      "query": "natural query text",
      "type": "procedure",
      "tags": ["optional-and-filter"],
      "tags_any": ["optional-or-filter"],
      "source": "optional/source/path.md",
      "expected_sources": ["stable-source-id"],
      "expected_sections": ["optional-section-id"],
      "absent_sources": ["wrong-nearby-source-id"],
      "pass_at": 3,
      "limit": 5
    }
  ]
}
```

Do not commit private eval files unless they contain only synthetic or intentionally public data.

## Triggering Failures

A triggering failure is different from a retrieval failure:

- Retrieval failure: the agent called YouKnowMe, but ranking or payload was wrong.
- Triggering failure: the agent did not call YouKnowMe for an owner-specific question.

Examples of triggering-sensitive queries:

- "How much bromine should I put into my home hot tub?"
- "How do I set my thermostat for heat?"
- "What should I say about my IC resume?"

For these, general training data or web search can be actively misleading because the answer depends
on Roger's private corpus. Improve the MCP tool descriptions and client registration copy before
changing ranking. Track triggering failures from conversation transcripts separately from eval cases,
because source-pointer logs only exist after a tool call.

## Maintenance Slices

1. Add private usage-derived eval cases from observed ChatGPT, Claude, Hermes Agent, and owner-driven
   queries when there is real evidence.
2. Classify each miss as corpus structure, filter semantics, payload breadth, ranking, or triggering.
3. Make corpus-only changes for structure/filter misses in the private corpus repo.
4. Rebuild `.ykm/real-index`, run private evals, then redeploy the index if quality improves.

## Out Of Scope

- Do not create production content in `POC/`.
- Do not add `upload` or `feedback`; those are Phase 3.
- Do not add Curator behavior; that is Phase 4.
- Do not add blob/PDF/image serving.
- Do not add link traversal under the name of payload tuning.
- Do not make Cloudflare MCP Portal an escape hatch for direct Access compatibility issues.
