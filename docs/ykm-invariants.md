# YKM — Invariants & Decision Card

*Paste at the top of any coding-agent session for this project. This is the durable contract.
When it conflicts with something the agent infers, **this wins.** When a rule seems to forbid
something useful, you are probably over-reading it — each rule names the mechanism it guards and
what it still permits. Guardrails, not bans.*

---

## A. Hard invariants — never violate, any phase

0. **Single-tenant by architecture; not content-overfit.** YKM is one person's notebook
   (NotebookLM-class), **not** a multi-user platform — no `user_id` in the data model, no per-user
   partitioning, no corpus isolation. Multi-user = a deliberate **re-architecture**, never a config
   flip; **don't design seams for it.** Separately, never special-case specific subjects/documents/
   people/content categories (no "hot tub"/"résumé" code paths) — the system knows typed, tagged
   content. Single-tenant is about *users*; overfitting is about *content* — different axes.
   (Genuinely config-revisable deployment details — owned VPS, retention window, source-path
   exposure — are not architecture.)
1. **YKM never writes to its own source — enforced structurally, not behaviorally.** The serving
   container holds **no repo-write credential**, so there is no code path to misuse regardless of
   what any agent decides. Every change to the repo is a GitHub **PR a human merges.** GitHub is the
   only door and the audit trail.
2. **YKM serves only official builds, never ad-hoc local index state.** The running service loads
   index/artifacts only from the project's official build output. *What makes a build "official" —
   CI/GH-registry provenance vs. optional cryptographic signing — depends on where the index is
   built (CI vs. VPS), which is OPEN.* Either way: never serve a hand-built or unverified local index.
3. **Credential scopes stay disjoint; serve holds none.** **Serve container:** read-only, *no*
   repo-write credential, *no* signing key. **Build stage** (CI or privileged sibling): repo-read,
   plus a signing key *only if* the VPS-build branch is chosen — secure its reachability. **Curator**
   (separate actor, different place): feature-branch push, no merge. No component holds more than one.
4. **YKM returns context; it never answers.** It hands back scoped *material* + source pointers.
   Synthesis, advice, and reasoning live in the calling agent — never inside `query`.
5. **One source of truth, one compiled artifact.** Source = repo markdown. Vector DB = build
   output, regenerable, **never committed.** No second *maintained* compiled format alongside it.
6. **Bare markdown always ingests — *structurally*.** Content *structure* (messy, untagged,
   headerless) never blocks ingest. But **security scanning may quarantine** (secrets/keys → don't
   index + report). Quarantine never rewrites source silently — a scrubbed version is a build artifact
   only; changing source is a **PR**. Structural gating ≠ security gating.
7. **No ambient/standing context.** Nothing is injected into every query. Preferences are content
   you *fetch* deliberately, then the agent persists them in its own UI.
8. **Minimal exposure: one front door, and the service guards it.** All bytes leave through the one
   already-solved authenticated MCP channel; don't open a second public/fronted surface without
   explicit validation. **Authentication is Cloudflare's; authorization is the service's** (defense-
   in-depth): verify Cloudflare's *signed* identity assertion (not a spoofable bare header) against a
   configured owner-email allowlist of one, and **fail closed** on mismatch/absence. Never a
   "couldn't verify, allow" fallback. The OTP/email allowlist is load-bearing access control, not an
   arbitrary setup detail.
9. **Single-tenant *by architecture* (not "scale later").** Optimize for clarity/debuggability. No
   multi-tenant authz machinery, no per-user routing, no `user_id` in the data model, no corpus
   isolation. Service-side owner authorization (invariant 8) is a single front-door gate against a
   configured principal — the single-tenant form of authz, not multi-tenancy. Generalizing = a
   deliberate re-architecture (invariant 0).
10. **Curator proposes, never merges.** Separate actor; feature-branch push only; a human merges. It
    reads logs and opens PRs; it does not mutate the corpus or live service. **Logs carry `source_id`s,
    so reading logs grants a sensitive-content signal — a named, accepted grant, not an accident.**

## B. Decision heuristics — how to choose at a fork

11. **Separate "how it's found" from "how it's delivered."** Every retrieval feature splits into a
    findability dial and a delivery dial, independently. (Embed-size vs. return-size; see-link vs.
    traverse; borrowed-text findability vs. reference delivery.) Solve them separately; don't let one
    drag in the other's complexity.
12. **Completeness ≠ safety.** Returning a *whole* procedure = completeness (structural chunk +
    parent-section retrieval). Returning the *right entity's* procedure = safety (tag/source
    disambiguation). Never try to solve one with the other.
13. **No model calls in the build or query path.** Ingest is deterministic (structural chunking, no
    procedure-detection). Reasoning over content is allowed **only in the Curator** — a separate,
    offline actor. If a step wants an LLM, it belongs in the Curator or nowhere yet.
14. **Staged pipelines & inspectable intermediates are encouraged.** The only constraints on a
    build intermediate: (a) not committed to the repo, (b) not a maintained format anything outside
    the build depends on. Otherwise emit whatever stages aid debugging. This is **not** a one-pass
    monolith.
15. **Build-time transforms land in the workspace; permanent changes are PRs.** Normalizing,
    deriving a chunk-ready copy → ephemeral build artifact. A change meant to be canonical source →
    PR. Never a write-back to the repo.
16. **Defer behind evidence; reserve a hook, don't guess.** For anything underspecified
    (orient-then-retrieve, delivery modes, link traversal, payload breadth, multimodal embedding),
    leave a labeled seam and wait for the logs. Speculative generality is drift.
17. **Identity is a later, derived, Curator-owned layer.** Phase work uses tags + *tag/source-based*
    disambiguation (surface distinct subjects, never silently merge). Do **not** build entity
    resolution into the query path or require hand-authored entities, however much the examples
    mention them. Entity-level labels are a later enhancement, not a phase-1 requirement.
18. **Don't assume a specific paid/external product is available.** Name tech choices as OPEN until
    validated against cost and access. Prefer reusing the solved MCP channel over new integrations.
19. **A prohibition is over-read if it blocks something clearly useful.** Re-check the named
    mechanism before complying. The rule guards a specific failure, not a whole category.

## C. Phase-1 boundary — what must NOT creep in early

20. **Phase 1 is exactly:** `query` + `health` + `retrieve`, RAG over a **hand-curated text-markdown
    repo the owner populates by hand.** Deterministic structural-chunk → embed → vector DB pipeline.
    Embedding model (reranker only if eval proves need — don't add by default). Tag/source-based
    disambiguation (don't silently merge). See-only links. Simple query filters. Stable IDs.
    Frontmatter conventions (optional, defaults inferred). Build provenance kept light — sole author
    (trust rigor scales with exposure; it matters at `upload`, phase 3).
21. **`query` ≠ `retrieve`.** `query` is semantic/ranked. `retrieve` is deterministic by stable
    ID/path/section — never semantic, never an answer API. Stable IDs must survive rebuilds.
22. **Design around these OPEN items NOW** (they shape phase-1 interfaces): build location, `query`
    response shape + stable-ID scheme, logging seams. *Ignore* the rest (multimodal, blob/image
    serving, orientation, delivery modes) — just don't foreclose them.
23. **Explicitly out of Phase 1:** upload, feedback, blob/PDF serving, image serving, multimodal
    embedding, link traversal, the Curator, entity resolution, orient-then-retrieve mechanisms,
    reranker (unless eval proves need). None of these. The phase wins by being small enough to use.
24. **The phase-1 success test:** a correctly-scoped answer (right subject, whole procedure) in a
    *fresh, fast* session, with working source pointers and no silent merging. If a change doesn't
    serve that, it isn't Phase 1.

---

*If a decision isn't covered here, the bias is: keep YKM dumb and read-only, defer the hard part,
reserve a hook, and write down the open question rather than resolving it by guessing.*
