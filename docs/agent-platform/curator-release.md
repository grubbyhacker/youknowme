# Curator release publishing (Stage 3, youknowme side)

The cross-repo architecture in `agent-infra-docs/design/agent-platform-coupling.md`
is authoritative; this note records only what is true of `youknowme`. If
implementation shows the architecture is wrong, the correction lands in
`agent-infra-docs` first and this note follows.

Depends on the merged Curator image contract (`docs/agent-platform/curator-image.md`,
PR #128) and the merged broker release surface
(`gh-agent-broker` `cmd/gh-agent-broker/release.go`, PR #177).

## What this stage delivers

`.github/workflows/curator-release.yml` publishes the dedicated Curator image as
an **AgentRelease** to the broker and promotes the **broker-assigned generation**.

- Builds the dedicated image from `Dockerfile.curator` at the checked-out commit
  and exports the same **single-image Docker archive** the image contract emits
  (`outputs=type=docker`), plus non-secret provenance from
  `scripts/curator-image-provenance.py`.
- Calls the pinned `gh-agent-broker` CLI:
  - `release-publish -agent-type youknowme-curator -archive <tar>
    -source-revision <sha> -platform linux/amd64
    -dependency-manifest-sha256 <sha256> -github-output` → the broker assigns and
    returns a **generation** and a ready state (`state=verified`, `ready=true`);
  - `release-promote -generation <broker-assigned generation>` → promotes exactly
    that generation. **No image reference is ever passed to promote.**
- The generation flows publish → promote via the publish step's
  `generation` output; the workflow caller never supplies it.

## Safety posture (deliberate)

- **workflow_dispatch ONLY.** No `push` / `workflow_run` / `schedule` trigger, so
  merging to `main` cannot call the (production-inert) broker release API or
  require a publisher/promoter secret. Promotion is opt-in via a boolean input;
  publish alone is the default.
- **The caller never selects an image or generation.** The image is this repo's
  own build; the generation is broker-assigned. There is no image/digest/
  generation/ref dispatch input.
- **Publisher and promoter are separate action-scoped operator tokens**
  (`VPS_OPS_GH_BROKER_RELEASE_PUBLISHER_OPERATOR_TOKEN`,
  `VPS_OPS_GH_BROKER_RELEASE_PROMOTER_OPERATOR_TOKEN`). Neither can launch; neither
  step carries the other's token.
- **Tailnet-only broker.** The workflow joins Tailscale (`tag:github-actions-deploy`)
  **before** any broker call; the broker base URL is a non-secret repo variable
  (`CURATOR_RELEASE_BROKER_URL`, default the VPS tailnet address).
- **Cross-repo pin.** The `gh-agent-broker` checkout pins the exact reviewed commit
  `23709f88bf840bf5bdc35fb5c45a94d9512cb61b` (origin/main, PR #177), never a moving
  ref; the workflow re-verifies `git rev-parse HEAD` against it.
- **Coexists with the existing broker deploy path.** `deploy-production.yml` is
  unchanged; this stage adds the release path without removing the old one.

## Out of scope (not done here, by instruction and by design)

The workflow is **not triggered**. No production change, no secrets provisioned,
no vps-ops change. The broker release store stays inert in production until
vps-ops lands the registry backup contract; that, operator-token provisioning,
and retiring the broker deploy path are later steps.

## Machine-verifiable boundary

- **Gate:** `mise run lint` and `mise run test`.
- **New validator:** `scripts/validate-curator-release-workflow.py`
  (`mise run curator-release-workflow`), an **offline, dependency-free** check that
  reads the workflow text and never runs it, joins a network, or reaches the broker.
- **Invariants it encodes:** manual-only trigger, no caller image/generation input,
  pinned broker commit, publisher/promoter token separation, tailscale-before-call,
  archive/provenance binding, and generation-only promotion.
- **Tests:** `tests/test_curator_release_workflow.py` — the checked-in workflow
  passes and each invariant is proven by a mutated copy that must fail.
