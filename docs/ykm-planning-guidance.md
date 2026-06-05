# YKM — Planning Guidance (for the planning agent)

*Read alongside `ykm-requirements.md` (the PRD) and `ykm-invariants.md` (the card). This file is
**how to plan**, not **what to build** — the PRD is what to build. Your job is to turn the PRD into a
phased, dependency-ordered implementation plan, not to re-decide the product.*

---

## 0. How to read the three documents

- **Invariants card = constitution.** Short, durable, always wins on conflict. If your plan would
  violate a card item, the plan is wrong — stop and flag it, don't route around it.
- **PRD = detail + reasoning.** Section numbers (§3, §12, …) are the citation anchors. The card is a
  *projection* of the PRD; the PRD governs where they ever disagree.
- **This guidance = planning method.** Sequencing, human-decision gates, drift warnings.

**Cite section numbers in the plan.** Every work item should reference the PRD section / card
invariant it satisfies. This is how the human (and the next agent) audits the plan for drift.

---

## 1. The one hard gate, and the one thing that may parallelize it

- **Contracts (§12) block *serving*.** Do not plan to implement the MCP server from prose and
  backfill schemas — behavior drifts. The `query`/`retrieve`/`health` schemas, response shape,
  stable-ID scheme, filter semantics, failure behavior, and the artifact/build manifests must be
  *specified* before serving code is written.
- **But the build/ingest pipeline + dev fixtures (§13b) may run in parallel** and will *inform* those
  schemas (you learn which fields matter by building the indexer). Plan them as a parallel track that
  *feeds* the contract spec, not as blocked behind it.

So the critical path is: **decisions → (pipeline ‖ contract spec) → serving → eval/acceptance.**

**Lean into single-tenancy — it's an architectural decision, not a limitation (§1, invariants 0/9).**
Do **not** thread `user_id` through the data model, add per-user partitioning, or "leave seams" for
multi-tenancy — that's forbidden speculative generality. One corpus, one principal. *But* this does
**not** mean skip authorization (see §2 item 8): single-tenant authz = a single front-door gate, and
it is a phase-1 requirement.

---

## 2. Decisions the plan must escalate to the human — do NOT resolve by guessing

These have outsized downstream effect or are genuinely the owner's call. Surface them as explicit
plan inputs/gates, with a recommendation if you have one, but let the human decide:

1. **Greenfield — DECIDED.** Production YKM is **greenfield**, outside `POC/`, using the POC only as
   *reference*. No conflict with any requirement. Note the implication: "Authentication SOLVED"
   (§9) means the *approach* is proven, **not** that code is inherited — the Cloudflare/tunnel/MCP
   wiring is **re-implemented**, with the POC as the authoritative reference for what Cloudflare
   actually sends to the origin. Confirm the POC's language/framework/MCP SDK only as a *starting
   reference*, not a substrate to extend.
2. **Embedding provider → build location** (§5, §9). OpenRouter (API call, CI build feasible) vs.
   local model (GPU, forces VPS build). One decision largely settles the other and the artifact
   shape. Provider must be **abstracted** regardless (§12) — OpenRouter is the preferred *initial*
   provider, not a hard dependency.
3. **Vector DB choice.** Unspecified by design, but steer toward **lightweight / embeddable, sized
   for a shared Hostinger KVM4** (which also runs Hermes) — not a heavyweight clustered server. Single
   user, scale is a non-goal (invariant 9). Flag the choice for human confirmation.
4. **`query` response schema + stable-ID scheme** (§12). Everything downstream depends on it.
   Includes the `matched_chunk` + `returned_content` split and the no-path-only-ID / alias rule.
5. **Filter semantics** (§12): define `source` (file/path vs. collection vs. type) and `tags`
   (AND/OR, exact/prefix, case, missing) before schema work.
6. **Logging granularity** (§14): log specific `source_id`s vs. coarser types/tags — this sets how
   much sensitive signal the Curator inherits later. Name the grant.
7. **Latency / responsiveness target.** The north star is *fast* (vs. the slow long thread), but no
   requirement encodes it. Decide whether phase 1 has any responsiveness target or is correctness-
   first with latency deferred. (Likely correctness-first given single-user, but make it explicit.)
8. **Service-side owner authorization — confirm the Cloudflare dependency** (§9, §14, invariant 8).
   Phase 1 **must** add a service-owned authz gate: verify Cloudflare's *signed* identity assertion
   against a configured owner-email allowlist of one, fail closed. This is defense-in-depth, **not**
   redundant with Cloudflare — do not judge it skippable. **Blocking confirm:** that the Cloudflare
   flow exposes a *verifiable signed* identity claim (JWT/signature) to the origin. If it does not,
   escalate — an unverified-header check is worse than none. Add the forge/strip-identity fail-closed
   negative test (§13a).

Items 2, 4, 6, 8 are "design-around-now" opens/requirements (§15). The rest (multimodal, blob/image
serving, orientation, delivery modes, Curator hinting) are **safe to ignore for phase 1** — plan only
to *not foreclose* them. **Multi-tenancy is not even in this list** — it is out of architecture
entirely (§1); do not plan for it at all.

---

## 3. Suggested phase-1 plan skeleton (adapt; don't treat as gospel)

**Pre-work — human decisions (§2 above).** Gate the rest on items 1–6.

**Track A — pipeline & fixtures (parallel, feeds the contract spec):**
- Build/ingest: markdown → preprocess → structural chunk → embed → vector DB (invariant 13:
  deterministic, no model reasoning in-path).
- Build manifest emission (per-chunk provenance, §12) + structural warnings that don't fail the
  build (§5).
- Dev fixtures: tiny sample corpus + fake/offline embedding mode or checked-in vectors + golden
  outputs (§13b) — so tests never need live embedding calls.

**Gate G1 — contract spec finalized (§12).** Blocks serving. Output: concrete tool schemas, response
shape, ID + alias scheme, filter semantics, failure behavior (§13a), artifact + logging schemas.

**Track B — serving (after G1):**
- `query` (semantic + filters + tag/source disambiguation), `retrieve` (deterministic, not semantic),
  `health` (readiness + provenance fields).
- Per-tool failure behavior (§13a); payload budget / default limit.
- Logging seams at the decided granularity (§14).
- Wire to the POC's existing auth/tunnel; serve container provably has **no repo-write credential**
  (assert as a test — §13).

**Track C — eval & acceptance (with B):**
- Eval harness over the canonical query set incl. **negative tests** (§13); de-overfitted (≥ half not
  the owner's memorable examples).
- "Why did this return this?" introspection CLI (§13b).
- Reranker decision *via the eval gate*, not by default (§5).
- All §13 acceptance tests green.

**Deploy:** compatibility tests across client paths — remote OAuth MCP, local Hermes (Cloudflare
bypass), `health`, `retrieve` (§14).

---

## 4. Planning-agent drift warnings (the failure modes specific to *this* project)

- **Don't pull deferred phases forward.** Upload, feedback, Curator, blob/image serving, reranker,
  entity resolution, link traversal, orient-then-retrieve are all *out of phase 1* (invariant 23).
  Plan seams, not implementations.
- **Don't gold-plate provenance/security in phase 1.** Trust rigor scales with exposure; it's
  load-bearing at `upload` (phase 3), light while the owner is the sole author (§9). Plan the seam,
  not signing infrastructure.
- **Don't hard-code the owner's content.** No content-type or subject special-casing (invariant 0).
  The eval set's de-overfitting rule is a guard here too.
- **Don't flatten the pipeline to avoid stages.** Staged + inspectable is *encouraged*; the only bans
  are committed-to-repo and maintained-parallel-format (invariant 14).
- **Don't let `retrieve` become a second semantic path / answer API** (invariant 21).
- **Don't put an LLM in the build or query path** (invariant 13) — reasoning is Curator-only, later.
- **Don't write acceptance tests against agent synthesis** — test what the *content service* returns,
  not what the calling model does with it (§13).
- **A card prohibition that blocks something clearly useful is probably being over-read** (invariant
  19) — re-check the named mechanism and flag the tension rather than silently working around it.

---

## 5. Definition of "ready to code" and "phase 1 done"

**Ready to code (serving):** §2 human decisions made; §12 contract spec written; dev fixtures exist.

**Phase 1 done:** every §13 acceptance test passes — including negative tests, the no-write-credential
assertion, `health` provenance fields, stable IDs surviving a rebuild + rename, and the eval harness
existing *before* any reranker decision. Restated north star (invariant 24): a correctly-scoped
answer, with working source pointers and no silent merging, in a fresh fast session.

---

## 6. Output format the plan should take

- Milestones mapped to **§13 acceptance tests** (a milestone is "done" when its tests pass).
- Each work item tagged with the **PRD section / card invariant** it serves.
- **Deferred items explicitly marked deferred**, with the phase they belong to — so scope creep is
  visible in the plan itself.
- Human-decision gates (§2) called out as blocking inputs, not silently chosen.
- Keep the plan honest about the dependency chain: embedding → build location → artifact shape;
  contracts → serving. Don't serialize what can parallelize (Track A), don't parallelize what's gated
  (serving behind G1).
