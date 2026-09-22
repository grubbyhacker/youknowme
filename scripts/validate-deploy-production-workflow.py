#!/usr/bin/env python3
"""Offline validator for the YouKnowMe production deploy workflow.

Encodes the deploy-contract invariant that `.github/workflows/deploy-production.yml`
deploys YouKnowMe ONLY and no longer carries the obsolete gh-agent-broker
deployment coupling. The broker now ships its own Curator AgentRelease pipeline
(publish/verify/acquire/promote), so YouKnowMe's deploy must not re-deploy the
broker or export any broker-role secret. This check is offline and
dependency-free: it reads the workflow text and reasons about it structurally.
It never runs the workflow, joins a network, or reaches the broker.

Invariants asserted:

  1. No broker playbook: the deploy `run:` block never invokes
     `playbooks/deploy-gh-agent-broker.yml`.
  2. Single deploy playbook: exactly one `run-ansible-playbook.sh` invocation
     remains, and it is `playbooks/deploy-youknowme.yml`.
  3. Youknowme-only Doppler roles: `VPS_OPS_DOPPLER_ROLES` requests `youknowme`
     and never `gh-agent-broker`.
  4. No broker secret exports: no `env:` key matches a broker-role secret
     namespace (VPS_OPS_GH_BROKER_*, VPS_OPS_HERMES_BROKER_*,
     VPS_OPS_SIGNAL_PLANE_DISPATCHER_BROKER_*), and no GHCR pull username/token
     pair (`github.actor` / `github.token` wired into a broker GHCR export)
     remains.
  5. No broker GHCR permission: the deploy job does not request
     `packages: read`, which existed solely for the broker GHCR pull.
  6. YouKnowMe's own deploy is preserved: the deploy-youknowme playbook is still
     invoked and the youknowme runtime secret exports (VPS_OPS_YKM_*) remain.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "deploy-production.yml"

BROKER_PLAYBOOK = "playbooks/deploy-gh-agent-broker.yml"
YKM_PLAYBOOK = "playbooks/deploy-youknowme.yml"

# Broker-role secret namespaces that must never be exported by the YouKnowMe deploy.
BROKER_SECRET_PREFIXES = (
    "VPS_OPS_GH_BROKER_",
    "VPS_OPS_HERMES_BROKER_",
    "VPS_OPS_SIGNAL_PLANE_DISPATCHER_BROKER_",
)


def _deploy_step(text: str) -> str:
    """Return the text of the `Deploy YouKnowMe services` step block."""
    steps = re.split(r"(?m)^      - ", text)
    for step in steps:
        if step.lstrip().startswith("name:") and "run-ansible-playbook.sh" in step:
            return step
    return ""


def _job_permissions_block(text: str) -> str:
    """Return the job-level `permissions:` block (the indented one under the job)."""
    match = re.search(r"(?ms)^    permissions:\s*\n(.*?)(?=^    \S|\Z)", text)
    return match.group(1) if match else ""


def check_no_broker_playbook(text: str, errors: list[str]) -> None:
    if BROKER_PLAYBOOK in text:
        errors.append(
            f"deploy must NOT invoke the broker playbook {BROKER_PLAYBOOK}; "
            "broker deployment is decoupled from YouKnowMe."
        )


def check_single_youknowme_playbook(text: str, errors: list[str]) -> None:
    invocations = re.findall(r"run-ansible-playbook\.sh\s+-i\s+\S+\s+(playbooks/\S+)", text)
    if not invocations:
        errors.append("deploy must invoke run-ansible-playbook.sh at least once.")
        return
    if YKM_PLAYBOOK not in invocations:
        errors.append(f"deploy must invoke {YKM_PLAYBOOK}.")
    extra = [p for p in invocations if p != YKM_PLAYBOOK]
    if extra:
        errors.append(
            f"deploy must invoke only {YKM_PLAYBOOK}; unexpected playbook(s): "
            f"{', '.join(sorted(set(extra)))}."
        )


def check_youknowme_only_doppler_roles(text: str, errors: list[str]) -> None:
    match = re.search(r"VPS_OPS_DOPPLER_ROLES:\s*(.+)", text)
    if not match:
        errors.append("deploy must set VPS_OPS_DOPPLER_ROLES.")
        return
    roles = [r.strip() for r in match.group(1).strip().split(",") if r.strip()]
    if "gh-agent-broker" in roles:
        errors.append("VPS_OPS_DOPPLER_ROLES must NOT include gh-agent-broker.")
    if "youknowme" not in roles:
        errors.append("VPS_OPS_DOPPLER_ROLES must include youknowme.")


def check_no_broker_secret_exports(text: str, errors: list[str]) -> None:
    step = _deploy_step(text) or text
    # env: keys of the shape `NAME: ...`
    for match in re.finditer(r"(?m)^\s{10}([A-Z0-9_]+):\s", step):
        name = match.group(1)
        for prefix in BROKER_SECRET_PREFIXES:
            if name.startswith(prefix):
                errors.append(f"broker-role secret export must be removed: {name}.")
                break
    if "${{ github.actor }}" in step:
        errors.append(
            "the broker GHCR pull username (${{ github.actor }}) export must be removed."
        )
    # A github.token wired into a broker GHCR export must be gone; telemetry
    # `github_token:` inputs are a different, preserved key.
    if re.search(r"(?m)^\s{10}VPS_OPS_GH_BROKER_[A-Z0-9_]*:\s*\$\{\{\s*github\.token", step):
        errors.append("the broker GHCR pull token (github.token) export must be removed.")


def check_no_broker_packages_permission(text: str, errors: list[str]) -> None:
    perms = _job_permissions_block(text)
    if re.search(r"(?m)^\s+packages:\s*read", perms):
        errors.append(
            "the deploy job must NOT request `packages: read`; it existed solely "
            "for the broker GHCR pull."
        )


def check_youknowme_deploy_preserved(text: str, errors: list[str]) -> None:
    if YKM_PLAYBOOK not in text:
        errors.append(f"YouKnowMe's own deploy must be preserved: {YKM_PLAYBOOK} missing.")
    step = _deploy_step(text)
    if step and not re.search(r"(?m)^\s{10}VPS_OPS_YKM_[A-Z0-9_]+:\s", step):
        errors.append(
            "YouKnowMe runtime secret exports (VPS_OPS_YKM_*) must be preserved."
        )


def main() -> int:
    errors: list[str] = []
    if not WORKFLOW.is_file():
        print(f"missing workflow: {WORKFLOW}", file=sys.stderr)
        return 1
    text = WORKFLOW.read_text(encoding="utf-8")

    check_no_broker_playbook(text, errors)
    check_single_youknowme_playbook(text, errors)
    check_youknowme_only_doppler_roles(text, errors)
    check_no_broker_secret_exports(text, errors)
    check_no_broker_packages_permission(text, errors)
    check_youknowme_deploy_preserved(text, errors)

    if errors:
        print("Production deploy workflow check failed:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1
    print(
        "Production deploy workflow check passed (no broker playbook, single "
        "youknowme playbook, youknowme-only Doppler roles, no broker secret "
        "exports, no broker GHCR permission, youknowme deploy preserved)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
