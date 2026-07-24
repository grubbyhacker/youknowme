# Curator Deployment Incident Recovery Ledger

## Closure

- **Exit:** A future YouKnowMe `main` deployment passes the vps-ops
  environment-input gate and deploys the pinned YouKnowMe image plus the Curator
  profile.
- **Out of scope:** Diagnosing live runtime behavior beyond this proven preflight,
  and setting the missing GitHub secret.
- **Plan:** One YouKnowMe PR. No staging lifecycle is needed because this is a
  workflow-contract-only repair. A triggered deployment is rerun only after the
  missing secret is configured and this PR is merged.

## Contract trace

| Boundary | Contract |
| --- | --- |
| Producer | GitHub Actions deploy job |
| Representation | `VPS_OPS_*` environment variables |
| Transport | `scripts/run-ansible-playbook.sh` environment mapping |
| Consumer / enforcement | vps-ops `scripts/run-ansible-playbook.sh map_env_secrets` |
| Trust | Agentd coordinator token is a GitHub secret; the GHCR token is the job `GITHUB_TOKEN` with `packages: read`; the username is `github.actor` |
| Failed behavior | Fail fast and name missing inputs |

The repair projects the three broker inputs required by the current vps-ops
mapping:

- `VPS_OPS_GH_BROKER_AGENTD_COORDINATOR_TOKEN`
- `VPS_OPS_GH_BROKER_GHCR_PACKAGES_READ_TOKEN`
- `VPS_OPS_GH_BROKER_GHCR_PULL_USERNAME`

The YouKnowMe repository currently lacks the agentd coordinator secret. Roger
must configure `VPS_OPS_GH_BROKER_AGENTD_COORDINATOR_TOKEN` in the YouKnowMe
repository, using the same value already used by `gh-agent-broker`, before merge
and deployment. No secret value is recorded or changed here.

## Evidence and proof

- Incident: YouKnowMe production deploy workflow run `29549453158` on
  `2026-07-17` failed before Ansible because these projections were absent.
- Locked diagnosis revisions: YouKnowMe `main` `07a3a9b`; vps-ops `main`
  `42a2248`.
- Independent broker evidence: `gh-agent-broker` self-deploy `a6f330a` is
  healthy.
- Required proof: static deploy-secret contract plus YouKnowMe CI.

