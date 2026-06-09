# YouKnowMe Docs

Place the PRD and supporting documentation for YouKnowMe in this directory.

Current planning artifacts:

- `ykm-requirements.md` - PRD and phase requirements.
- `ykm-invariants.md` - durable project invariants.
- `ykm-planning-guidance.md` - planning method and implementation gates.
- `ykm-planning-answers.md` - resolved planning-agent questions and owner decisions.
- `ykm-corpus-authoring.md` - frontmatter and markdown authoring guidance for the private corpus.
- `ykm-cloudflare-cutover.md` - existing Cloudflare Tunnel / Access contract and cutover plan.
- `ykm-vps-runbook.md` - Phase 1E VPS deployment, smoke checks, and rollback runbook.
- `ykm-phased-plan.md` - current high-level implementation phases and status.
- `ykm-phase2-runbook.md` - retrieval quality and private eval loop for Phase 2.
- `ykm-phase3-intake.md` - staged upload/feedback intake contract and forward Curator design.
- `ykm-phase4-curator.md` - Curator agent plan, state machines, runtime direction, and settled
  initial-scope decisions.
- `ykm-curator-contracts.md` - Phase 4 Curator JSON/file contracts for tasks, state, feedback
  plans, decisions, locks, branches, and reports.
- `ykm-curator-implementation-status.md` - restart handoff for current Curator implementation
  capabilities, safety boundaries, verification, and next targets.
- `ykm-curator-launcher-plan.md` - VPS Curator trigger plan using sandbox-broker operator REST
  launch profiles and a systemd timer.
- `ykm-curator-launcher-runbook.md` - live Curator launcher maintenance, smoke checks, token
  rotation, image updates, and rollback.
- `ykm-curator-model-planning-plan.md` - manual-only model-backed feedback planning milestone through
  `gh-agent-proxy`.
- `ykm-curator-model-eval-plan.md` - next milestone for evaluating and tightening model feedback
  planning quality before state advancement or mutations.
- `ykm-curator-prerequisite-milestone.md` - broker, proxy, GitHub app, and deployment prerequisites
  before Curator implementation.
- `ykm-curator-dry-run-harness-milestone.md` - minimal Curator dry-run worker and sandbox report
  contract.
- `ykm-corpus-ci-artifact-prerequisite-milestone.md` - `ykmcorpus` CI validation and official
  LanceDB index artifact production prerequisite.
