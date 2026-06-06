# You Know Me (YKM) — Requirements

*Status: working draft. Informal by design. Decisions are locked unless marked OPEN.*

---

## 1. What this is

An MCP server that serves curated context about its **owner** — projects, writing, procedures,
manuals, preferences — so any AI agent (Claude, ChatGPT, web/mobile/remote) can retrieve scoped,
grounded context on demand instead of being fed context files or re-told everything in a
long-lived session.

**North star:** durable, externalized long-term memory that survives session boundaries.
The owner starts a *fresh, fast* chat and loses nothing, because the context lives in YKM, not in
a thread that gets slower as it grows. The hot-tub case is the canonical test, not the goal.

**Single-tenant by architecture — a real decision, not a convenience.** YKM is a single-tenant
personal tool — closer to NotebookLM (one person's notebook) than a multi-user platform. There is
**no notion of multiple users**: no per-user partitioning, no `user_id` threaded through the data
model, no corpus isolation. If multi-user is ever wanted, that is a deliberate **re-architecture**
(auth identity → data partitioning → result scoping → isolation testing) — explicitly triggered, not
a config flip. The owner is comfortable with that; it is not coming. *Do not design "seams" for
multi-tenancy* — that is the speculative generality the invariants forbid.

**Single-tenant ≠ content-overfit (different axes — keep them separate).** Single-tenant is about
*users*; overfitting is about *content*. The system must still **not special-case specific subjects,
documents, people, or content categories** (no "hot tub" or "résumé" code path) — it knows **typed,
tagged content**, and reads the owner's corpus generically at runtime. A clean, un-overfit,
single-tenant system is exactly the NotebookLM shape. When a thing could say "the owner" instead of
"Roger," it should. Overfitting to one person's *content* is a design smell.

**Deployment-mode details (genuinely revisable by config) are a separate, smaller set:** owned VPS,
log retention window, source-path exposure (§14). These are deployment decisions, not architecture —
distinct from single-tenancy, which *is* architecture.

**Secondary goals**
- A useful contribution to the owner's personal productivity.
- A worked example of building RAG, Python, a live service, and agentic work.

---

## 2. Architecture in one breath

A private markdown repo is the source of truth. A build pipeline compiles it into a vector
index. YKM serves **read-only** queries against that index over authenticated MCP. **All
writes — uploads, edits, enrichments — are GitHub PRs that a human merges.** A separate
Curator agent reads usage and feedback logs and proposes those PRs.

### Layering

1. **Content** — heterogeneous, typed, tagged, chunked, embedded. What `query` retrieves.
2. **Identity** *(later, Curator-owned)* — entities (e.g. `hottub-home`, `hottub-beachhouse`)
   derived from content, used to scope and disambiguate.
3. **Curator** *(later)* — external agent; reads logs, proposes PRs, no merge rights.

### The spine rule (load-bearing)

> **YKM is a passive content service. It never mutates its own corpus.**
> GitHub is the HITL gate, the audit trail, and the only door into the repo. YKM reads the
> result of merges; it does not author them.

This single rule collapses most of the complexity: upload becomes "sanitize → open PR,"
feedback becomes "an input the Curator reads," and the Curator becomes an external
collaborator with feature-branch push only.

**Enforced structurally, not just behaviorally.** The spine rule does not rely on the service (or
any agent) *choosing* not to write. The serving container holds **no repo-write credential** — there
is no code path to misuse. The serving container also consumes **only official builds**, never
ad-hoc local index state — *what makes a build "official" (CI/registry provenance vs. optional
cryptographic signing) depends on where the index is built; see §9.* This keeps the sensitive
capability out of the serving container, leaving the Curator's branch-push as the only write
credential anywhere, held by a separate actor in a different place.

---

## 3. The query contract (the part everything depends on)

- **YKM returns context; the agent synthesizes.** YKM hands back the right *scoped material*
  with source pointers. It does not generate advice or answers. Keeps YKM dumb and reliable,
  and puts reasoning where the reasoning model already is.
- **Retrieval is chunk-based.** Documents are split into passages at ingest, each embedded;
  a query returns the nearest passages *with a pointer back to the source doc* — not whole
  files. Chunking is invisible to authors.
- **Chunk on structure, return the parent.** Two deterministic rules, no build-time reasoning:
  1. **Structural chunking** — split on existing markdown structure (headers, sections, list
     items), *not* blind N-token windows. A procedure written under one heading stays under
     that heading because we split on the heading, not inside it.
  2. **Small-to-big / parent retrieval** — embed small chunks for match precision, but on a hit
     return the *enclosing section* (the parent), not the bare matched chunk. So even if the
     embedded unit clipped mid-procedure, what comes *back* is the whole section.
  Reinforced by a mild authoring convention (taught by the upload skill): "keep one procedure
  under one heading." Convention-*guided*, not convention-*dependent* — ignoring it yields a
  less tidy section, not a broken half-procedure.
- **No algorithmic procedure-detection.** We do *not* try to detect conditional/procedural
  ordering at build time (that would mean an LLM in the ingest path — cost + nondeterminism we
  don't want). Completeness comes from structural splitting + parent retrieval, above.
- **Completeness ≠ safety — keep them separate.** Returning a *whole* procedure is a
  completeness guarantee. Returning the *right entity's* procedure is a safety guarantee, and it
  lives entirely in labeled disambiguation below — *not* in the chunker. A perfectly intact chunk
  can still be the wrong tub.
- **Safe disambiguation floor: surface distinctly, never silently merge.** When a query is
  ambiguous across subjects (home vs. beach-house tub), YKM returns the candidates as **distinct
  results, each carrying its own identifying tags/source** — so the agent can label or ask, and the
  *system* never collapses two different subjects into one answer. In phase 1 this is **tag/source-
  based** (separate files, distinct tags like `chlorine` vs. `bromine`); the polished entity-level
  label (`[Home Hot Tub — CHLORINE]`) is what *matures* once the identity layer exists (§2, §8).
  The safety guarantee — don't merge — holds from phase 1; the labeling gets richer later. Mixing up
  a dangerous subject is a failure the *system* makes hard, not one left to careful phrasing.
- **Links are see-only (phase 1).** A retrieved chunk may carry `related:` links in its
  metadata. The agent *sees* them and can issue a new query; YKM does **not** traverse them.
  No graph-walk logic on the hot path.
- **`query` vs. `retrieve` — different jobs.** `query` is semantic search (ranked, embedding-backed).
  `retrieve` is **deterministic source retrieval by stable ID / path / section** — no embedding, no
  ranking, no synthesis. `retrieve` must not become an answer API or a second semantic path; it
  returns exactly the requested content.
- **Stable IDs are required.** Documents and sections carry **stable IDs that survive rebuilds**, so
  `retrieve`, source pointers, logs, and future Curator PRs can refer to content reliably. ID
  derivation is an implementation choice; *stability across rebuild* is the requirement.
- **Phase-1 query filters (plumbing, not orientation).** `query` accepts simple metadata filters —
  `type`, `tags`, `source`, `limit` — as basic scoping plumbing. This is **not** the deferred
  orient-then-retrieve mechanism below; it's just filterable search. Keep the *agent-orientation*
  question parked even while filters exist.

### Orient-then-retrieve (OPEN — let logs decide)

The agent should be able to make *better* queries by seeing corpus structure/metadata first,
then issuing a filtered query. Mechanism is undecided: a separate `describe`-style call, tags
riding along in responses, or guidance baked into the MCP tool description. **Hold open.** (Note:
the simple phase-1 filters above are *plumbing*; this is the richer *orientation* mechanism — don't
conflate them.)
Watching how real agents actually invoke `query` (the logs are visible) is half the project's
value; do not over-theorize this now.

---

## 4. Data model

### Content is heterogeneous but rides one mechanism

Many types side-by-side, all chunked/embedded/tagged/retrieved the same way. The *type* is
metadata, not a separate subsystem. This is the discipline that stops Swiss-army-knife sprawl.

Planned content types (non-exhaustive):
- Dev environment
- Work history / resume
- Writing: Substack, essays, articles
- Project ideas; project plans
- Research topics + notes
- Home procedures (e.g. hot-tub maintenance schedules)
- Markdown versions of manuals / owner's manuals
- Personal & professional goals
- Procedures (home + hobby)
- Interests
- Communication preferences
- **Skills** (see below)
- Obsidian notes *(future — see §6)*

### No ambient/standing context

YKM never injects "always-on" context into every query. Preferences are ordinary content you
*fetch* deliberately — e.g. *"Use YKM to retrieve my communication preferences and store them
for this and future sessions"* is just a query plus an agent-side persist step that lives in
the agent's UI, not in YKM.

### Skills as content

Skills are retrieved like any other content (`type: skill`); the difference is the agent
*executes* the result instead of *reading* it — and that difference lives in the agent, not
in YKM. Example prompts: *"Consult YKM on how to write a paper outline…"*, *"…on how to
structure code review comments, and use that to evaluate this PR."*
- **OPEN:** whether skills can/should be served as first-class MCP skills (executable MCP-side)
  vs. served as text the agent runs. Parked.

### Metadata: enrichment, never a barrier

- **Bare, un-annotated markdown is always accepted.** Never block content creation on tagging.
- YKM defines its **own** frontmatter conventions (`type`, `tags`, `related`/links, optional
  `delivery_mode`). Authored deliberately by you or the Curator. YKM owns the format; the
  author owns the intent.
- **The upload skill teaches the format.** A served skill ("how to write a persisted memory for
  YKM") carries the frontmatter/tagging guidance, so when an *agent* authors content for YKM it
  produces well-tagged frontmatter. Discipline becomes a *served convention*, not a gate. The
  system teaches its own ingestion format.

### Links (own convention, not Obsidian)

Explicit, first-class links between content are in scope — authored by you or the Curator via a
frontmatter field with stable IDs (`related: [hottub-home, leslies-dry-acid]`). This is the seed
of the deferred entity layer: same convention, two authors. Distinct from Obsidian-awareness,
which is out of scope (§6).

### Images / figures (informative only)

Interleaved images are in scope — product shots (e.g. Leslie's Dry Acid), headshots, the beach
house, figures/charts — the way images make markdown more readable. **Not** album support; **no**
decorative content (hero banners). The design splits into two independent dials, mirroring the
chunking split (findability vs. delivery = embed-size vs. return-size):

- **Findability approach: borrow from the text, don't index pixels.** (Applies whenever images
  land — image support as a whole is *later*, not phase 1; see §5, §11.) An image is an *asset
  attached to its parent chunk*. You find the chunk by its surrounding prose + alt text + caption;
  the image reference rides along. Zero new index infrastructure — chunks already carry references
  and return their parent section. Sufficient for our use cases, because the text already names
  the thing pictured.
- **Delivery: by reference, not bytes.** The query result returns a *pointer* to the image, not
  inline data. Keeps `query` lean (latency). Inlining bytes is a later `delivery_mode` option.
  *Note:* MCP can carry image blocks, but client forwarding into the model's context is variable;
  references work regardless of client behavior.
- **Serving path is OPEN and deferred** (not phase 1; gated by cost + available features). What the
  reference points to, and how the fetch is served, is undecided. Two shapes with different
  cost/security profiles:
  1. **Through the MCP `retrieve` path** — agent fetches via `retrieve`; YKM streams bytes over the
     *already-solved* authenticated MCP channel. No public asset surface, no new auth, no extra
     cost. Tradeoffs: payload weight, variable client handling of binary blocks. *Likely preferred
     precisely because it reuses solved auth* — but a later-phase call.
  2. **A separately-fronted URL** (e.g. Cloudflare or other) — new public surface, new auth, possible
     cost. **Do not assume this is available or worth paying for.** Carries the unknowns.
- **Storage mirrors the text/blob split.** Small figures/headshots may live *in the repo*
  (self-contained, simple). Heavy images join PDFs in blob storage (§5).
- **Alt text is load-bearing.** Since findability is borrowed from text, an uncaptioned image with
  empty alt text is nearly invisible to retrieval. The upload skill must teach "meaningful caption
  / alt text for every informative figure." The hero-banner ban becomes a content convention the
  Curator can flag. Informative-only isn't taste — it's what keeps borrowed-findability honest.
- **OPEN (parked, behind evidence):** pixel-level **multimodal embedding** (CLIP-style) so a text
  query matches an image directly. The "index the image itself" version. A real project (multimodal
  model, unified vector space). Earns its keep only when images *aren't* described by their context;
  ours are, so likely never needed.

### Delivery mode (OPEN — hooked, not built)

One delivery path in phase 1: ranked chunks + source pointers, procedures kept whole. Add
`delivery_mode` as an *optional* metadata field defaulting to that path. Fill it in later with
log evidence (e.g. "manuals want a whole section, essays want a ranked snippet") rather than
guessing now. Engineer gets simplicity; the seam is reserved.

---

## 5. Build / ingest pipeline

**Model it like Hugo.** The repo holds *source* (markdown + frontmatter). Chunks, embeddings,
and the vector DB are *build artifacts*, regenerable at any time, **never committed.** Committing
them buys merge conflicts, staleness, and a repo that lies. "Reindex" = `hugo build` for your brain.

```
source markdown (repo)  →  preprocess  →  structural chunk  →  embed  →  vector DB (compiled artifact)
                                          (split on headers/sections, not token windows)
```

- **Multi-stage pipeline with inspectable intermediates is encouraged.** The pipeline is
  naturally staged (preprocess → normalize → structural chunk → embed → load), and stages may emit
  intermediate artifacts (a canonical normalized doc, a chunk manifest, etc.). These aid debugging,
  incremental builds, and clarity — *build them where they help.* This is **not** a single-pass
  monolith; do not over-optimize toward avoiding stages.
- **Two narrow constraints on intermediates** (everything else is the implementer's call):
  1. **Not committed to the source repo.** Intermediates are build-workspace outputs, regenerable,
     never checked in. (Spine rule: source-repo writes happen *only* via PR.)
  2. **Not a maintained format with an external contract.** Intermediate schemas are internal to
     the build, free to change, with nothing outside the pipeline depending on them. What we resist
     is a *parallel compiled artifact* (markdown + chunks + entity cards bundled into a persisted,
     versioned format) standing alongside the vector DB — that's scope creep before evidence, not a
     debugging file. The vector DB is the one compiled artifact. If entity cards ever become
     queryable, the Curator generates them as **markdown in the repo (source)**, via PR.
- **Where build-time file transforms land.** A transform (normalize frontmatter, derive a
  chunk-ready copy) lands in the *build workspace*, ephemeral. A transform meant to become
  canonical source is a **PR** (spine rule), never a silent write-back to the repo.
- **Preprocess scope (phase 1): structural, not semantic.** Parse standard markdown structure
  (headers, sections, lists) to drive chunk boundaries; strip/ignore anything not recognized.
  Markdown-structure-aware, **not** Obsidian-aware (§6), and **no** procedure-detection (§3).
  Parent-section retrieval is a *query-time* rule, not a build step — the build just needs to
  preserve the chunk→parent-section mapping.
- **Parent-retrieval edge cases need defined behavior** (the graceful-degradation claim, made
  concrete): very large sections, deeply nested lists, one heading containing multiple procedures,
  **files with no headings**, and sections exceeding a context budget. Each needs a predictable
  fallback (e.g. cap a too-large parent and return the matched chunk + neighbors; treat a headerless
  file as a single section or fall back to sized windows). Behavior must be *defined*, not emergent.
- **Build emits structural warnings (don't fail the build).** When a parent section is oversized or
  structurally suspicious (no headings, a heading with many procedures), the build **reports** it so
  the owner can improve authoring — but bare/messy content still ingests (invariant: never block
  content). Warning, not error.
- **Reindex strategy** — OPEN (operational). When/how to trigger, full vs. incremental. Decide
  during build.

### Models (RAG components)

- **Embedding model required** (core of retrieval).
- **Re-ranking model: evidence-driven, not assumed.** A reranker improves ranking quality but may
  be unnecessary for a small, distinctly-tagged single-user corpus. Validate whether phase 1 needs
  one at all; it is a fair candidate to *defer to phase 2* if embedding-only retrieval is good
  enough. Do not build it into phase 1 by default.
- **Prefer OpenRouter** for embeddings (and reranking, if used) unless a local solution is
  economical/possible. Decide by cost. (This choice also drives build location — §9.)

### Blob / PDF / image-asset handling (parked, later phase)

**Both the blob *serving path* and the image *serving path* are scoped out of early phases** — they
introduce complicated retrieval/serving questions gated by cost and available features (see §4 Images
serving path). Phase 1 is pure text markdown.

- **Storage backend — OPEN (operational).** Leaning toward a **replicated Google Drive folder**
  over a bucket — little planned content, keep it cheap, reuses an ecosystem you already have. But
  this is an infrastructure choice with weak product impact; treat it as open, not decided. Local
  cache available to the container regardless.
- Heavy images go here too; small figures/headshots may live in-repo (§4 Images).
- **OPEN:** how blob content gets indexed (extract text → treat as markdown source?); and how blobs
  are *served* (through MCP `retrieve` vs. a fronted URL — same fork as §4 Images). Both deferred.

---

## 6. Explicitly out of scope (with reasoning, so we don't re-litigate)

- **Obsidian-awareness.** No vault parsing, Dataview, `![[embeds]]`, or link-graph resolution.
  The meticulous notes don't exist yet; building a parser for a corpus of zero files is premature.
  If diligent note-taking ever materializes, this becomes a future pipeline enhancement *with real
  files to test against.* (Note: YKM partly exists to make such notes worth taking — so this may
  return, but not now.)
- **Persisted, *maintained* parallel compiled format** (a versioned second artifact alongside the
  vector DB). See §5. **Not** a ban on pipeline stages or ephemeral build intermediates — those are
  encouraged.
- **Link traversal** by YKM. See-only in phase 1 (§3).
- **Ambient/standing context.** See §4.

---

## 7. Self-improvement (the theme)

Two distinct signals, converging into one input stream the Curator reads. **Feedback never
auto-touches the served corpus** — any resulting change is a normal PR.

- **Passive — usage logs.** The Curator reads access logs (you can already see them): what's hot,
  what's retrieved-then-useless, what's never touched. Drives quality/enrichment priorities.
  Observability you already have; Curator just gets read access.
- **Active — agent feedback (a write path).** An agent reports things like *"this note didn't
  distinguish startup vs. maintenance dosing and I gave bad advice"* or *"I needed the bromine SKU
  and it wasn't there."* Carries the same hazards as upload (untrusted input, injection, resource
  limits) but is *about* content, not content itself.
- **Decided shape: feedback is an inert observation log.** The Curator is the only consumer; it is
  *not* visible to future agents at query time (that fork — queryable annotations riding in
  responses — is the expensive one and is rejected for now). Adds a write endpoint and a log, not
  a subsystem.

---

## 8. Curator agent (later)

A **separate actor**, not part of YKM. Likely a different container; communicates via **private
MCP**; possibly built on **Hermes** (PR review is a multi-turn, get-nagged-and-iterate loop —
tenacity is the relevant trait) or a custom agent-SDK build.

Responsibilities (as understood; role needs more definition before full commitment):
- Read usage + feedback logs; identify hot content and quality/enrichment priorities.
- Evaluate uploads *as later-phase enrichment*. **Note the phase ordering:** `upload` is phase 3,
  the Curator is phase 4 — so **upload review must work *without* the Curator** (the HITL is the
  human reviewing a PR; that's the whole gate). Curator evaluation of uploads is an *enhancement
  layered on later*, never a prerequisite for upload to exist.
- Identify conflicts; over time, **derive and maintain entity identity** (`hottub-home` vs.
  `hottub-beachhouse`; merge/split when a third home or a relative's tub enters the picture).
- **Propose PRs only.** Feature-branch push; **no merge rights — you merge by hand.** Responds to
  your code-review feedback.
- **OPEN:** a channel to *hint* the Curator toward specific tasks — possibly in-MCP, possibly
  external. Parked.

**Sequencing note:** understand the basic experience — including a *non-agentic* upload/processing
flow — before building the Curator. Expect to learn lessons that reshape the role. The Curator may
first run as a series of prompts in a Codex session, with all type/metadata living in repo
frontmatter so no separate records are persisted beyond the repo.

---

## 9. Non-functional requirements

- **Containerized on owned VPS** — Hostinger KVM4. Everything runs in containers.
- **Hermes service-token access.** The Hermes Agent runs in a container on the *same* VPS but uses
  the public Cloudflare Access route with service-token headers. Cloudflare validates the service
  token; YouKnowMe then authorizes the verified Access JWT `common_name` against
  `YKM_ALLOWED_SERVICE_COMMON_NAMES`.
- **Compatible with web/mobile ChatGPT and Claude**, including remote-data-center execution.
- **Authentication — SOLVED (POC complete).** Cloudflare/managed OAuth + tunnels + fronting owns
  *authentication* (proving identity). Single-use OTP. Agent ↔ MCP flows for query/health/retrieve
  are done. The service does **not** manage credentials or identity — that delegation is required by
  the Cloudflare tunnel + MCP creation flows and stays.
- **Authorization — service-owned, defense-in-depth (new requirement).** The original "Cloudflare is
  the auth provider, never vet inside the service" decision over-delegated: it let *authentication*
  config (an email allowlist on token issuance) silently do all the *authorization* work — one
  misconfiguration away from exposing personal data to any authenticated principal. Correct this:
  the service **independently authorizes** the Cloudflare-authenticated identity against a configured
  **owner-email allowlist (size one)** or an explicit Cloudflare Access service-token identity
  allowlist, and returns a 4xx on mismatch. Two independent layers must both fail for exposure to
  occur. This is *not* multi-tenancy (no `user_id` in the data model, §1) — it's a single front-door
  gate authorizing against configured principals.
  - **Verify the *signed* assertion, never a bare header.** The check is only additive if the service
    validates Cloudflare's **signed identity assertion** (e.g. the Access JWT against Cloudflare's
    public keys + audience tag) and extracts the verified email or service-token `common_name` from
    *that* — not an unverified `Cf-Access-…-Email` header, which anything reaching the origin could
    spoof. An unverified-header check is security theater and worse than none.
  - **DEPENDENCY TO CONFIRM before building:** that the Cloudflare flow exposes a *verifiable* signed
    identity claim to the origin. The design is sound only if it does (see planning guidance).
  - **Fail closed.** Missing / unverifiable assertion, owner-email mismatch, or unallowed
    service-token `common_name` → reject. Never fall back to "couldn't verify, allow." Tested as a
    negative case (§13a).
  - **Residual risk (don't oversell):** this defends against the auth-layer misconfiguration named
    above (wrong email issued a token). It does **not** protect against a compromised Cloudflare
    account, leaked signing material, or a hijacked owner session. It raises the bar; it does not make
    serving personal data risk-free.
- **No public-path relaxation for Hermes.** Earlier planning considered a local Hermes bypass. The
  current Hermes path uses Cloudflare Access service tokens instead, so the public route still
  **always** requires a verified signed JWT and fails closed. The failure to prevent remains the same:
  never relax the public JWT requirement merely because Hermes needs access. Service-token JWTs are
  authorized only by verified `common_name` allowlist, not by `aud` plus missing `email`.
- **Health endpoint is split.** (a) A **private, unauthenticated liveness** endpoint (process up),
  bound to the private interface for the container/orchestrator. (b) An **authenticated MCP `health`
  tool** reporting serve-readiness + provenance (`source_commit`/`build_id`/`embedding_model`/
  `created_at`) behind normal authz. Provenance is **not** exposed unauthenticated (information
  disclosure).
- **Credential isolation (structural spine enforcement).** The **serve container holds no
  repo-write credential** — it cannot write to the repo because it has no credential to, not because
  it is told not to. The Curator (separate actor, different place) has feature-branch push, no merge.
  Whether a *signing key* exists at all depends on the build-location fork below.
- **Artifact trust — OPEN, downstream of build location.** "Trusted artifact" can't be defined until
  we know *where the index is built.* Two branches:
  1. **Build in CI (GH Actions) → GH artifact registry.** "Official build" has teeth nearly for
     free: registry provenance, known workflow, no signing key to manage. Serve container pulls the
     latest official artifact and trusts the registry. *Preferred if feasible.*
  2. **Build on the VPS.** No registry; "official build" = "what my own pipeline produced." Weaker
     provenance; cryptographic signing would be the way to add teeth back — but likely **not worth
     it** for a single-user system where build host and serve host are both yours.
  Minimum bar either way: serve container consumes only *official builds*, never ad-hoc local index
  state. (Full crypto-signing is now optional, not required.)
- **Trust rigor scales with exposure — don't gold-plate it in phase 1.** While you are the sole
  author hand-curating content (phase 1, no `upload`), serving a locally/VPS-built index is fine and
  provenance hygiene is light. The trust mechanism becomes load-bearing only once `upload` (phase 3)
  introduces *untrusted agent input* into the pipeline. A coding agent should not build heavy build
  provenance/signing in phase 1; it should ensure the seam exists for when phase 3 needs it.
- **Build location depends on the embedding-model choice (§5).** The real unknown is whether
  GH-hosted runners can do the index build. Bottleneck is usually GPU (local embedding model) or
  build *time*, not runner CPU/RAM. If embeddings are an **OpenRouter API call** (current lean), the
  runner only orchestrates — CI is likely fine. A **local GPU embedding model** drags the build onto
  the VPS. So: resolve the embedding-model question and build location mostly resolves with it.
- **Secure / minimal exposure.** Don't over-expose on the internet; strict authn/authz.
- **Single-tenant *by architecture* (not "scale is a non-goal for now").** One tenant, period (§1).
  Explicit non-requirements: no multi-tenant authz machinery, no per-user corpus routing, no
  `user_id` in the data model, no corpus isolation. This is **not** "single-user until we scale" — it
  is the architecture. *Service-side owner authorization (above) is a single front-door gate against
  a configured principal, which is the single-tenant expression of authz — not multi-tenancy.*
  Generalizing to multi-user is a deliberate re-architecture, never a config flip (§1).
- **`health` — define what it asserts.** Beyond process liveness: index loaded, index/embedding
  version, source commit SHA, vector DB reachable, auth path functioning. Exact set is an
  implementation detail; the requirement is that `health` distinguishes "process up" from
  "actually able to serve correct results."
- **Upload path is sanitized and HITL-gated.** Safety-scan uploads for attacks; resource limits
  (frequency, size); HITL approval = a GitHub PR you review. (Upload is an acknowledged stretch.)

---

## 10. User-facing functions

| Function   | Phase | Notes |
|------------|-------|-------|
| `query`    | 1     | Primary interface. Semantic, RAG-backed. Filters (`type`/`tags`/`source`/`limit`); returns scoped chunks + source pointers; tag/source disambiguation. Response shape: §12. |
| `health`   | 1     | Liveness **and** serve-readiness (index loaded, version, source commit). §9. |
| `retrieve` | 1     | Deterministic source retrieval by stable ID/path/section. **Not** semantic, **not** an answer API. §3. |
| `upload`   | 3     | Markdown in → sanitize → **PR**. Stretch goal. |
| `feedback` | 3     | Agent observations → inert log for the Curator. |

---

## 11. Phasing

### Phase 1 — *the line that wins* ⭐
**Smallest slice that makes the owner reach for YKM instead of the old long thread.**

`query` + `health` + `retrieve`, RAG-backed, over a **hand-curated repo the owner populates
manually.** No upload, no blob, no Curator. The moment a correctly-scoped answer comes back in a
fresh, fast session, YKM has already won; everything else is enrichment.

Includes: build/ingest pipeline (markdown → preprocess → structural chunk → embed → vector DB),
**embedding model** (OpenRouter unless local is cheap; reranker only if embedding-only retrieval
proves insufficient), frontmatter conventions (`type`/`tags`/`related`), **tag/source-based
disambiguation** (surface distinct subjects, never silently merge — entity-level labels come later),
see-only links, simple query filters, stable IDs, the artifact + build manifests (§12), logging
(§14), and the phase-1 acceptance tests (§13).

### Phase 2 — Enrichment & evidence *(three distinct tracks, not a grab bag)*
Driven by phase-1 logs and eval results. Kept separate so the implementer doesn't treat them as one:
- **2a — Retrieval quality.** Reranker (if eval proves need), ranking tuning, payload-breadth tuning
  (fatter payloads vs. round-trips — *separate dial from* link traversal).
- **2b — Authoring & conventions.** The served "how to write a persisted memory" upload skill;
  richer frontmatter conventions.
- **2c — Orientation & delivery shaping.** Orient-then-retrieve mechanism; `delivery_mode` population.

### Phase 3 — Write paths
`upload` (sanitize → PR) and `feedback` (inert log). HITL via GitHub throughout. Trust rigor in the
build pipeline becomes load-bearing here (§9).

### Phase 4 — Curator
External agent (Hermes or SDK). Reads logs, proposes PRs, derives/maintains entity identity. Begin
only after the manual flows have taught their lessons; possibly prototype as Codex prompts first.

### Later / unscheduled — Non-text serving
Blob (PDF) and image **serving + retrieval paths**, with their open tech questions (serve through
MCP `retrieve` vs. fronted URL; indexing of blob content). Deliberately not slotted to a numbered
phase — gated by cost, available features, and evidence of need (§4 Images, §5 Blob handling).

---

## 12. Contracts to define — **blocking prerequisite for serving**

These are the **contract layer**, to be specified as concrete schemas *before the MCP server is
coded*. **Blocking gate:** do not implement serving from prose and backfill schemas — behavior drifts
if you do. *Scoping nuance:* the **build/ingest pipeline and dev fixtures (§13) may proceed in
parallel** and will *inform* these schemas (you learn what fields matter by building the indexer);
only the **serving** layer is gated. Required *content* named below; exact field names/shapes are the
spec-pass decision.

- **MCP tool schemas** — `query`, `retrieve`, `health`, each with defined **failure behavior** (§13a).
- **`query` response shape.** Each result carries at least: `source_id`, `source_path`, `section_id`,
  `parent_section`, `tags`, `score`, a disambiguation/identity hint; optionally `related`. **Resolve
  the content-duplication question:** return **`matched_chunk`** (the passage that matched —
  explainability) *and* **`returned_content`** (what the agent should synthesize from — typically the
  parent section), rather than one ambiguous `content` field.
- **Query filter semantics (define before schema work):**
  - **`source`** is ambiguous as written — **define it** (file/path vs. collection vs. type). Default
    intent: source *file/path*. Pick one and name it.
  - **`tags`** needs semantics: **AND vs. OR, exact vs. prefix, case sensitivity, and behavior when
    tags are inferred or missing.** Unspecified = inconsistent agent results. Decide in the spec pass.
  - `type`, `limit`: define defaults (and see payload budget, §13a).
- **Stable IDs — not path-only.** Paths may change; **path-derived IDs alone are banned as the sole
  identity.** Either explicit frontmatter IDs, or a path-derived ID *with an alias/migration story* so
  IDs survive moves/renames across rebuilds. (Path may be an *input* to ID derivation, not the whole
  identity.)
- **Chunk→parent mapping is build-time deterministic and inspectable.** Parent *expansion* happens at
  query time, but the *mapping* is fixed at build and recorded in the manifest — so the same query
  deterministically yields the same parent.
- **Embedding/rerank provider is abstracted, not baked in.** Provider is **configurable**; OpenRouter
  is the **preferred initial** provider, not a hard dependency in the core pipeline. (Protects the
  build-location decision too — §9.)
- **Artifact contract** — vector DB path/package + manifest. **Phase-1 concrete minimum:** every
  served index exposes `source_commit`, `build_id`, `embedding_model`, `created_at` via `health`/
  diagnostics. Enough provenance now without gold-plating.
- **Build manifest** — per-chunk: source file, section heading path, chunk ID, parent section ID,
  tags, embedding model, source commit. Enough to answer "why did this query return this?" (§13b).
- **Source repo schema** — minimal frontmatter + inferred defaults (below), ID behavior, directory
  conventions. · **Runtime config + auth assumptions**, **logging schema** (§14).

**Frontmatter: bare accepted, defaults inferred.** Bare markdown always ingests (structural rule —
see ingest-vs-security split, §14). Absent frontmatter → build **infers defaults**: `type` (path/
heuristics), `tags` (may be empty), and a stable ID (path-derived *with* alias story, per above).
Minimal recommended schema + examples: spec pass + served upload skill.

## 13. Phase-1 acceptance tests ("done means done")

Phase 1 is complete when these pass (expanded into concrete cases in the spec pass). **Tests assert
what the *content service* does — never agent synthesis** (YKM returns context; what the agent does
with it is out of scope to test here).

- A query over two same-kind-but-distinct subjects (home vs. beach-house) returns **distinct result
  records with distinct source/tag identity** — the system never silently merges them. *(This is the
  testable form; "never returns a blended answer" was a synthesis test and does not belong to YKM.)*
- Every `query` result includes working **source pointers** (resolvable `source_id`/`section_id`).
- `retrieve` returns exact content **by stable ID/path/section**, deterministically, no ranking/
  synthesis — and IDs resolve **across a rebuild** (including after a file move/rename, per the alias
  story, §12).
- The **serve container has no GitHub credential with repo-write scope** (assert as a test).
- `health` reports index-loaded + `source_commit` + `build_id` + `embedding_model` + `created_at`,
  not just liveness.
- **Negative tests (not just expected-source).** The eval set must assert what *must not* happen:
  does NOT return the wrong subject; does NOT pull unrelated preference docs into an unrelated query;
  does NOT traverse `related`; does NOT surface content from unsupported blobs/images in phase 1.
- **Canonical query set (5–10, de-overfitted).** Spanning ambiguous (two subjects), specific (one
  named subject), preference, writing, project, and procedure queries — each with expected source
  section(s) and expected disambiguation behavior. **At least half must be content-*shape* tests not
  tied to the owner's memorable examples** (the hot tub may appear, but can't dominate — overfitting
  applies to tests too, §1). This set *is* the eval harness.
- **Eval harness exists before the reranker decision.** "Reranker is evidence-driven" (§5) has teeth
  only against a golden set. The eval gate decides reranker in/out.

### 13a. Failure behavior (the largest remaining gap — define for every tool)

Each of `query` / `retrieve` / `health` needs **defined, tested** behavior — not emergent — for:
missing index, stale index, unknown ID, bad/malformed filter, auth failure, embedding/rerank provider
unavailable *during build*, and **no results** (an empty result set is a valid answer, not an error).
Also define the **default query limit and payload budget** so responses are bounded and consistent
across clients.

**Authorization negative test (fail-closed, §9).** Forge/strip the Cloudflare identity assertion, or
present a verified identity whose email ≠ the configured owner → expect a **4xx, fail-closed**, never
a fallback-allow. This asserts the service-side authz layer actually works and that a coding agent
hasn't quietly introduced a "couldn't verify, allow" path to protect access.

### 13b. Dev fixtures & retrieval explainability (implementation requirements)

- **Deterministic local/dev fixtures.** A tiny sample corpus, a **fake/offline embedding mode or
  checked-in test vectors**, and golden outputs — so basic tests never depend on live embedding calls.
- **"Why did this query return this?" introspection.** An admin/debug command or CLI (not necessarily
  a user-facing MCP tool) that inspects the build manifest and explains a result's provenance (which
  chunk matched, which parent expanded, what score). Without it, retrieval bugs are opaque.

## 14. Security boundaries, logging & privacy

**Actors / trust boundaries (name them, then map read/write).** Serving container · build pipeline ·
source repo · Curator · remote MCP client · Hermes service-token client · Cloudflare/OAuth layer.
The spec pass maps which actor can read/write what; the load-bearing facts already decided: serve =
read-only, no repo write (§9); Curator = branch-push, no merge (§8); build = the one place with
repo-read + (optional) signing (§9).

**Logs are inside the private/sensitive boundary.** Logs carry `source_id`s that point at
(potentially sensitive) docs — so **logs inherit the sensitivity of what they reference.** Two
consequences that must be decided, not left implicit:
- Logs are a **protected asset**, stored and access-controlled like corpus content — not a casual
  side-channel.
- **The Curator reads logs (§7) → the Curator transitively gains a sensitive-content signal.** That
  is an accepted, *named* access grant, not an accident: the Curator's read scope includes "which
  source IDs were queried." If that's too much, the alternative is logging coarser (e.g. types/tags,
  not specific `source_id`s) — a spec-pass decision. Either way, **name the grant.**

**Logging — design the seams now even though upload/feedback are deferred.** Phase 1 must decide:
- **Log:** query latency, returned `source_id`s, errors, client/auth identity, timestamp. (Feeds the
  Curator's passive signal later, §7 — subject to the boundary note above.)
- **Do NOT log by default:** raw query text and returned content — they may contain sensitive data.
- **Debug mode is an explicit opt-in, not just "off."** Full-text logging (query text/content) is a
  named debug mode with **its own retention window and redaction**, so future retrieval tuning is
  possible without making sensitive text durably logged by default. ("Off by default" alone would
  quietly kill tuning usefulness; "opt-in with retention+redaction" preserves both.)

**Ingest gating: structural never blocks, security *can* quarantine.** Resolve the standing tension
between "bare markdown always ingests" and "the build handles secrets":
- **Content *structure* never blocks ingest** — messy/untagged/headerless content still goes in
  (invariant). This is the rule "never block content" actually means.
- **Security scanning *may quarantine*.** If the build detects obvious secrets (keys, tokens) in
  source, it **quarantines + reports** rather than indexing them — security gating is separate from
  structural gating, and it's allowed to stop content.
- **No silent source rewrite (spine rule).** Scrubbing must **never** rewrite the source repo
  silently. The scrubbed/redacted version is **only ever a build artifact**; if the *source* should
  change, it goes through a **PR** like every other source mutation. Quarantine = "don't index it +
  tell me," not "edit my repo behind my back."

**Privacy (this is personal data — treat it as such).**
- Retention windows defined (not "log forever"); single-user retention is a *deployment-mode* default
  (§1), not an architectural assumption.
- Retrieved content may include sensitive personal data by design; acceptable for the owner's own
  agent over the authenticated channel — which is *why* minimal-exposure and the no-second-front-door
  rules (§6, §9) are load-bearing, not optional.

**Compatibility tests (define, don't assume).** "Compatible with web/mobile ChatGPT and Claude" needs
concrete path tests: remote OAuth MCP `query`, Hermes service-token `query`, `health`, `retrieve`,
and defined **failure behavior** on each path (§13a).

**Single-tenant access model (state it, don't leave it accidental).** The corpus is **single-tenant
by design** (§1). Any *authorized* principal sees the whole corpus — there is no in-corpus access
control, and that's correct for a one-person notebook. Therefore **access control lives at the front
door, not in the query path**: authentication is Cloudflare's (who gets a token), and **authorization
is the service's** — verifying the signed identity against the configured owner-email allowlist of one
and failing closed (§9). The OTP/email allowlist is **load-bearing access control, not an arbitrary
setup choice** — treat it as such. The "one misconfiguration from exposure" risk is mitigated (not
eliminated) by the service owning authz as an independent second layer.

**Source-path exposure — a deployment-mode decision, not a universal one.** Source pointers expose
repo paths. For the **current single-user deployment** where the owner authors the repo, the path is
the natural stable reference and there's no one to leak it to — **accepted for phase 1.** But this is
a *deployment-mode* call (§1), not a product law: if second-user support ever exists, path exposure
becomes a policy/config question. Don't hard-code path exposure as if it were always safe.

## 15. Open-question triage: design-around-now vs. ignorable-for-phase-1

Not all OPEN items are equal. Two classes (this distinction is itself a requirement, so the
implementer knows which gaps constrain phase-1 shape):

- **OPEN but must be designed around NOW** (affects phase-1 artifact/interface shape):
  - **Build location** (CI vs. VPS) — shapes the artifact contract and manifest *today* (§9, §12).
  - **`query` response shape & stable-ID scheme** — everything downstream depends on it (§12).
  - **Logging seams** — cheap now, expensive to retrofit (§14).
  - **Service-side owner authorization** — verify Cloudflare's signed assertion, fail closed (§9,
    §14). A phase-1 requirement, not optional; gated on confirming Cloudflare exposes a verifiable
    signed claim to the origin.
- **OPEN and safely ignorable for phase 1** (decide later, no phase-1 footprint):
  - Multimodal image embedding (§4) · blob/image serving path (§4, §5) · orient-then-retrieve
    mechanism (§3) · delivery-mode population (§4) · Curator task-hinting (§8) · skills-as-executable
    (§4). Phase 1 only needs to **not foreclose** these (leave seams), not build them.

---

## Open items index
- Orient-then-retrieve mechanism (§3)
- Skills: executable MCP-side vs. served-as-text (§4)
- Delivery mode population (§4)
- Reindex strategy: trigger, full vs. incremental (§5)
- Reranker: needed in phase 1 at all? — evidence-driven, candidate to defer to phase 2 (§5)
- Local vs. OpenRouter embeddings — cost decision (§5); **drives build location** (next item)
- Build location: CI (GH Actions/registry) vs. VPS — gated by whether runners can do the index build (§9)
- Artifact trust mechanism — registry provenance vs. optional signing — downstream of build location (§9)
- Blob storage backend — Google Drive vs. bucket; operational, weak product impact (§5)
- Blob/PDF/image *indexing* approach (§5)
- Blob/image *serving* path — MCP `retrieve` vs. fronted URL; cost + feature gated (§4, §5) — deferred
- Multimodal (pixel-level) image embedding — likely never needed (§4 Images)
- Curator task-hinting channel (§8)
