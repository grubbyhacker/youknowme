# YKM — Answers to Planning-Agent Questions

*Disposition tags: **[REQ]** requirement-level answer (derived from PRD/invariants) · **[DECIDED §X]**
already specified, confirming · **[OWNER]** owner must decide — recommendation given · **[IMPL]**
implementation choice — steer given · **[SPEC-PASS]** to be fixed in the contract spec, recommended
default given. PRD = `ykm-requirements.md`; card = `ykm-invariants.md`.*

---

1. **Greenfield vs POC — [REQ, no conflict].** No conflict. "Authentication SOLVED (POC complete)"
   means the *approach* is proven, not that code must be reused. Greenfield is fine; POC is reference.
   **Implication:** the Cloudflare/tunnel/MCP wiring must be **re-implemented**, not inherited — and
   the POC is the **authoritative reference for what Cloudflare actually sends to the origin** (use it
   to confirm Q16). Planning guidance §2.1 (which assumed "extend the POC") is corrected accordingly.

2. **Corpus location — [OWNER; strong rec: separate private repo].** PRD §2 ("a private markdown repo
   is the source of truth") + greenfield service repo ⇒ the **corpus is a separate private repo** from
   the service-code repo. The spine rule requires the service hold no write credential to it. **Access
   pattern (derived):** the build takes a *configured corpus source* — **clone-at-commit-SHA** for
   official/CI builds (manifest records `source_commit`), **local checkout path** for dev. Support
   both. → *Confirm the repo split.*

3. **Content layout — [REQ].** Accept **arbitrary markdown paths + infer `type`** (path heuristics /
   optional frontmatter). A *required* layout would violate "bare markdown always ingests" (invariant
   6). A conventional `content/{type}/…` layout is **recommended** (aids inference, taught by the
   upload skill) but **not enforced**.

4. **Stable IDs — [REQ + IMPL].** Explicit frontmatter `id` is the **canonical identity when present**
   (makes rename a non-event). Bare content → **generated stable fallback**; generation scheme is IMPL,
   but must be stable across rebuild (content-based or registered) — raw mutable path as the *sole*
   identity is **banned** (§12). Rename handling: see #5.

5. **Rename aliases — [REQ].** Aliases must be **source-controlled** — they must survive a clean
   rebuild, and the build manifest is ephemeral/regenerable, so **"build-manifest-only" is ruled out.**
   Home: frontmatter `id`/`aliases`, **or** a repo-managed source aliases file. Either satisfies the
   requirement (pick one in spec pass); both keep identity in source, consistent with the spine rule
   and no-parallel-compiled-format.

6. **Response shape — [DECIDED §12].** Yes — return **both** `matched_chunk` (explainability) and
   `returned_content` (synthesis). The §12 minimum set (`source_id`, `source_path`, `section_id`,
   `parent_section`, `tags`, `score`, disambiguation hint, optional `related`) stands. Already
   specified; confirming.

7. **Payload budget — [IMPL].** Implementation chooses **conservative defaults, documents them, makes
   them configurable** (§13a requires they be *defined*, not specific numbers). Steer: small default
   `limit`; cap per-result to the parent section with the #8 overflow rule; bound total response size.

8. **Parent overflow — [REQ steer].** Never silently drop content. **Oversized parent →** return the
   matched chunk + bounded neighbors **+ a pointer** (`source_id`/`section_id`) so the agent can
   `retrieve` the full section deterministically. **Headerless file →** treat as one section; if it
   exceeds budget, fall back to sized windows + pointer. Leans on the query/`retrieve` split (query =
   bounded preview, `retrieve` = full deterministic fetch). Exact caps = IMPL. **Must be defined +
   tested** (§3, §13).

9. **Filter semantics — [SPEC-PASS; recommended defaults]:**
   - `source`: **file/path** (per §12 lean).
   - `tags`: **AND** by default (narrows to the right subject — serves disambiguation); allow OR
     expression.
   - **exact** match, not prefix, by default.
   - **normalized lowercase** (predictable).
   - **missing tags** → doc simply doesn't match a tag filter (filtering is opt-in scoping). Inferred
     tags: mark inferred-vs-authored in metadata; for *filtering*, treat the same.
   - `type`: **exact** match.
   → Owner may override; these are recommended spec-pass defaults.

10. **No-results — [DECIDED §13a].** `query` returns **success with empty `results`**, not an error.
    Optional warnings/suggestions = nice-to-have IMPL. Empty is a valid answer, never a tool-level
    "not found."

11. **Embedding provider — [REQ + OWNER].** Provider **abstraction is required** (§12); OpenRouter is
    the **preferred initial real provider**. **Fake/local test embeddings are required regardless**
    (§13b) — start there so tests never hit live calls. **Specific model / dimension / cost ceiling =
    OWNER decision (cost).** Build abstracted → ship with fake embeddings → wire OpenRouter as first
    real provider once the owner picks model/dim/ceiling.

12. **Vector DB — [IMPL + constraints].** Implementation choice; **do not bake a product into the
    core** (mirror the provider abstraction — keep it swappable behind an interface). Constraints:
    lightweight, **embeddable/in-process**, containerized, easy artifact packaging, fits a shared
    Hostinger KVM4. Examples that fit (not mandates): sqlite-vec, LanceDB, Chroma (persistent), FAISS-
    on-disk.

13. **Build location / minimum "official" — [REQ].** Phase 1: **official builds only, but "official" =
    "produced by the defined build pipeline and labeled with the manifest"** (`source_commit`,
    `build_id`, `embedding_model`, `created_at`) — **not** "from a registry." A **local/VPS build
    qualifies** in phase 1 (trust rigor scales with exposure — sole author, no upload, §9). CI/GH-
    registry is the *maturation*, not a phase-1 gate. **Forbidden:** serving hand-built / unlabeled /
    ad-hoc index state (invariant 2).

14. **Artifact packaging — [IMPL].** Implementation choice. Steer: **self-contained directory or
    tarball** carrying a **named manifest file**; avoid heavier Docker-layer/registry coupling in
    phase 1. Pick a stable conventional manifest filename. Don't over-engineer.

15. **Reindex trigger — [REQ + OWNER].** **Full rebuild is acceptable in phase 1** (small corpus,
    single tenant); incremental is a later optimization. Trigger = operational/owner choice; **manual
    command is the phase-1 minimum**, automation (Actions-on-change / cron-pull) is owner's later call
    (§5 OPEN).

16. **Cloudflare signed assertion — [CONFIRMED, current Cloudflare docs].**
    - **Yes**, Cloudflare Access provides a verifiable **signed JWT**. Header **`Cf-Access-Jwt-
      Assertion`** (also the `CF_Authorization` cookie).
    - **Validate:** signature against the team **JWKS** at
      `https://<team>.cloudflareaccess.com/cdn-cgi/access/certs`; check **`iss`** (team domain) and
      **`aud`** (your application AUD tag), plus `exp`. (RS256.)
    - **Authoritative owner email = the `email` claim inside the *validated* JWT payload** — **not** the
      unverified `Cf-Access-Authenticated-User-Email` header.
    - **Nuance (Cloudflare's own docs):** because YKM sits behind a **Cloudflare Tunnel**, Cloudflare
      treats re-validation as *optional* — the tunnel is *why* "trust Cloudflare" was defensible.
      Since the owner wants **defense-in-depth, validate anyway**: the tunnel makes the claim
      trustworthy; validation makes it **independent and provable**. This is the clean split — authn =
      Cloudflare, authz = service verifies the signed claim.
    - **Still confirm against the POC's actually-observed headers + current config** (the AUD tag is
      deployment-specific; the POC already authenticates, so it is ground truth).

17. **Hermes service-token authz — [UPDATED, see PRD §9 update].** Earlier planning considered a
    local Hermes bypass with a separate private-only trust mechanism. The current Hermes path uses
    the public Cloudflare Access route with service-token headers. Requirement: the public path
    ALWAYS requires a verified CF JWT and fails closed; Hermes is allowed only when the verified JWT
    `common_name` matches `YKM_ALLOWED_SERVICE_COMMON_NAMES`.

18. **Health auth — [REQ — see PRD §9 update].** **Split it:** (a) a **private, unauthenticated
    liveness** endpoint (process up) bound to the private interface for the container/orchestrator;
    (b) an **authenticated MCP `health` tool** that reports serve-readiness + provenance
    (`source_commit`/`build_id`/`embedding_model`/`created_at`) behind normal authz. **Don't expose
    provenance unauthenticated** (information disclosure).

19. **Logging granularity grant — [OWNER; recommend].** Recommend **logging specific `source_id`s by
    default** — the self-improvement/Curator signal depends on knowing what's hot (§7). **Named grant:**
    "logs record returned source IDs; the Curator's later read of logs inherits a which-docs-were-
    queried signal — accepted." Treat logs as a **protected asset** (§14). **Do not** log query
    text/content by default. → *Owner ratifies.*

20. **Log storage/retention — [IMPL + OWNER].** Storage: recommend **structured JSONL to a persistent
    volume** (queryable later by the Curator; stdout-only is too ephemeral for the passive signal).
    **Retention window = OWNER decision** — suggest a sane default (e.g. 30–90 days), configurable.

21. **Debug logging — [REQ policy / IMPL mechanism].** Policy required now: **off by default, explicit
    opt-in flag, its own retention window, redaction applied** (§14). Exact redaction rules/patterns =
    IMPL, deferred; **leave the config seam.**

22. **Security scanning — [REQ, scaled to exposure].** Phase 1: implement the **interface + basic
    high-confidence secret detection** (quarantine + report, **never** source rewrite — §14, invariant
    6). Aggressive/comprehensive scanning **matures with `upload` (phase 3)** when untrusted input
    arrives. Minimum patterns = IMPL; high-signal classes (private-key headers, obvious cloud keys,
    high-entropy tokens). Quarantine = "don't index + tell me."

23. **Dev fixtures — [REQ].** **Synthetic only** — no real personal data in the (greenfield) service
    repo. **Content-shape oriented**, mimicking structure (two distinct same-kind subjects with
    conflicting settings; a procedure with conditional branches) without being real owner content.
    Categories: the §13 set (ambiguous, specific, preference, writing, project, procedure) + negative
    tests; **≥ half must be shape tests, not the owner's memorable examples** (de-overfit, §1/§13).

24. **Reranker — [DECIDED §5/§13].** **Exclude from phase 1 by default; the eval harness decides.**
    The eval harness *is* required in phase 1. If embedding-only passes the golden set → reranker is a
    phase-2 candidate (or never). If it fails → add it in phase 1. So: skip by default, **record as a
    phase-2 candidate gated by the eval**, not an unconditional skip.

25. **MCP compatibility — [REQ].** Acceptance must cover **ChatGPT remote MCP (via Cloudflare), Claude
    remote MCP (via Cloudflare), and Hermes service-token MCP (via Cloudflare)** — plus per-path
    failure behavior (§13a, §14). **MCP Inspector = dev harness throughout, not an acceptance gate.**
    Priority: one remote path end-to-end first (proves the Cloudflare/MCP path), then the second
    remote, then Hermes service-token MCP.

26. **Naming — [OWNER/IMPL].** No requirement. Recommend **consistency**; `ykm` is the established
    shorthand in these docs, so e.g. `ykm` code namespace + `you-know-me`/`youknowme` for human-facing
    names is natural. Owner picks.

---

## What still needs the owner's input (the genuine blanks)

After the above, the only items a human must still fill before/early in implementation:
- **#2 corpus repo split** — confirm separate private repo.
- **#11 embedding model / dimension / cost ceiling** — the cost decision that also drives build
  location (#13) per PRD §9.
- **#19 logging grant** — ratify "log source_ids" (recommended) vs. coarser.
- **#20 log retention window** — pick a default.
- **#26 naming** — pick a convention.

Everything else is either a requirement, already decided in the PRD, or an implementation choice with
a steer above. The biggest *technical* confirm (#16, Cloudflare signed assertion) is resolved — verify
the AUD tag against the live deployment, but the mechanism is confirmed.
