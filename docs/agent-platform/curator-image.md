# Curator agent image (Stage 2)

The cross-repo architecture in `agent-infra-docs/design/agent-platform-coupling.md`
is authoritative. This note records only what is true of `youknowme`. If
implementation shows the architecture is wrong, the correction lands in
`agent-infra-docs` first and this note follows.

## What this stage delivers

Curator ships as a **dedicated image derived from the versioned shared runtime
base**, not as the YouKnowMe service image — so ordinary service changes no
longer manufacture agent releases (design, *The artifact contract*).

- `Dockerfile.curator` — the dedicated image. Its runtime stage is
  `FROM ${AGENT_RUNTIME_BASE}` (`ghcr.io/<owner>/agent-runtime-base`, introduced
  in `agent-workflows`, Stage 2). CI pins an immutable base coordinate via the
  `AGENT_RUNTIME_BASE` build arg; the default `:main` tag is a local convenience.
- It installs **only** the Curator application code and its Python dependency
  closure. Curator imports `ykm.build.parse_frontmatter` in two modules
  (`upload_draft.py`, `upload_model_eval.py`), so `src/ykm` is copied for that
  dependency; the YouKnowMe server, its CLI, and the `ykm serve` entrypoint are
  not part of this image.
- `scripts/curator-agent-entrypoint.sh` — the **fixed container invocation
  contract**: the platform mounts `/input` read-only and supplies
  `/input/work-item.json`; results are written under `/output`; **mode travels in
  the typed work item**, never on the command line. The entrypoint passes exactly
  those fixed paths to `curator run`.

## Provenance

Non-secret, recorded as image labels (`io.grubbyhacker.agent-image.*`) and an
on-disk manifest under `/usr/local/share/agent-image/`:

- **source revision** — the git commit the image was built from;
- **dependency-manifest SHA256** — a deterministic hash over `pyproject.toml` and
  `uv.lock` (the files that define the baked dependency closure), computed by
  `scripts/curator-image-provenance.py`;
- **platform** — `linux/amd64`;
- **archive/image identity** — the Docker archive SHA256, the local image ID, and
  the single repo tag, derived by CI from the exported archive.

The shared base is asserted (in `agent-workflows`) to carry **no** dependency
manifest; this image writing one is what marks it as a derived agent image.

## CI

`.github/workflows/curator-image.yml` validates the contract offline, builds the
`linux/amd64` image, and exports a **single-image Docker archive**
(`curator-image.tar`, `outputs=type=docker`) plus the finalized provenance JSON,
both uploaded as build artifacts.

It does **not** publish to a registry, promote, deploy, or touch any secret
beyond the built-in `GITHUB_TOKEN` used read-only to pull the runtime base.
Publish/verify/acquire/promote are broker-owned operations in the promotion
contract and are out of scope for this stage.

## Machine-verifiable boundary

- **Gate:** `mise run lint` and `mise run test`.
- **New validator:** `scripts/validate-curator-image-contract.py`
  (`mise run curator-image-contract`), an **offline** check that reads
  `Dockerfile.curator`, the entrypoint, and the service `Dockerfile` and never
  builds an image or reaches a registry.
- **Invariant it encodes:** Curator is a dedicated image derived from the shared
  runtime base, with a fixed entrypoint, the `/input/work-item.json` → `/output`
  contract, mode in the typed input, recorded non-secret provenance, and it is
  not the YouKnowMe service image.
- **Docker smoke (single real exercise, Docker-gated):**
  `tests/test_curator_image_smoke.py` builds the image and runs the entrypoint
  against `fixtures/curator-agent/work-item.json`, asserting the report is written
  under `/output` and the provenance is recorded. It skips cleanly when Docker or
  the base image is unavailable, so it never blocks the offline gate.

## Out of scope (per the design's sequencing and this task)

No broker API calls, no promotion, no deploy, no production workflow changes, no
secrets. Those belong to Stage 3 (AgentRelease promotion and resolution).
