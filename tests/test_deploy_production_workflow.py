"""Offline tests for the production deploy workflow validator.

Exercise the validator against the checked-in workflow (must pass) and against
mutated copies that reintroduce broker deployment coupling (must fail). No
workflow is run, no network is joined, no broker is reached.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]
VALIDATOR_PATH = ROOT / "scripts" / "validate-deploy-production-workflow.py"
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "deploy-production.yml"


def _load_validator() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_deploy_production_validator", VALIDATOR_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def validator() -> ModuleType:
    return _load_validator()


@pytest.fixture(scope="module")
def workflow_text() -> str:
    return WORKFLOW_PATH.read_text(encoding="utf-8")


def test_checked_in_workflow_passes(validator: ModuleType) -> None:
    assert validator.main() == 0


def test_no_broker_playbook(validator: ModuleType, workflow_text: str) -> None:
    errors: list[str] = []
    validator.check_no_broker_playbook(workflow_text, errors)
    assert errors == []


def test_broker_playbook_is_rejected(validator: ModuleType, workflow_text: str) -> None:
    mutated = workflow_text.replace(
        '            -e "ansible_port=${DEPLOY_PORT}"\n',
        '            -e "ansible_port=${DEPLOY_PORT}"\n\n'
        "          scripts/run-ansible-playbook.sh -i inventory/production.yml "
        'playbooks/deploy-gh-agent-broker.yml \\\n'
        '            -e "ansible_port=${DEPLOY_PORT}"\n',
        1,
    )
    errors: list[str] = []
    validator.check_no_broker_playbook(mutated, errors)
    assert any("deploy-gh-agent-broker.yml" in e for e in errors)


def test_single_youknowme_playbook(validator: ModuleType, workflow_text: str) -> None:
    errors: list[str] = []
    validator.check_single_youknowme_playbook(workflow_text, errors)
    assert errors == []


def test_extra_playbook_is_rejected(validator: ModuleType, workflow_text: str) -> None:
    mutated = workflow_text.replace(
        "run-ansible-playbook.sh -i inventory/production.yml playbooks/deploy-youknowme.yml \\",
        "run-ansible-playbook.sh -i inventory/production.yml playbooks/deploy-youknowme.yml \\\n"
        '            -e "x=1"\n\n'
        "          scripts/run-ansible-playbook.sh -i inventory/production.yml "
        "playbooks/deploy-gh-agent-broker.yml \\",
        1,
    )
    errors: list[str] = []
    validator.check_single_youknowme_playbook(mutated, errors)
    assert any("unexpected playbook" in e for e in errors)


def test_youknowme_only_doppler_roles(validator: ModuleType, workflow_text: str) -> None:
    errors: list[str] = []
    validator.check_youknowme_only_doppler_roles(workflow_text, errors)
    assert errors == []


def test_broker_doppler_role_is_rejected(validator: ModuleType, workflow_text: str) -> None:
    mutated = workflow_text.replace(
        "VPS_OPS_DOPPLER_ROLES: youknowme",
        "VPS_OPS_DOPPLER_ROLES: youknowme,gh-agent-broker",
    )
    errors: list[str] = []
    validator.check_youknowme_only_doppler_roles(mutated, errors)
    assert any("gh-agent-broker" in e for e in errors)


def test_no_broker_secret_exports(validator: ModuleType, workflow_text: str) -> None:
    errors: list[str] = []
    validator.check_no_broker_secret_exports(workflow_text, errors)
    assert errors == []


def test_broker_secret_export_is_rejected(validator: ModuleType, workflow_text: str) -> None:
    mutated = workflow_text.replace(
        "          VPS_OPS_YKM_CLOUDFLARED_TUNNEL_TOKEN: ${{ secrets.VPS_OPS_YKM_CLOUDFLARED_TUNNEL_TOKEN }}",
        "          VPS_OPS_GH_BROKER_ADMIN_SECRET: ${{ secrets.VPS_OPS_GH_BROKER_ADMIN_SECRET }}\n"
        "          VPS_OPS_YKM_CLOUDFLARED_TUNNEL_TOKEN: ${{ secrets.VPS_OPS_YKM_CLOUDFLARED_TUNNEL_TOKEN }}",
    )
    errors: list[str] = []
    validator.check_no_broker_secret_exports(mutated, errors)
    assert any("VPS_OPS_GH_BROKER_ADMIN_SECRET" in e for e in errors)


def test_broker_ghcr_username_export_is_rejected(validator: ModuleType, workflow_text: str) -> None:
    mutated = workflow_text.replace(
        "          VPS_OPS_YKM_CLOUDFLARED_TUNNEL_TOKEN: ${{ secrets.VPS_OPS_YKM_CLOUDFLARED_TUNNEL_TOKEN }}",
        "          VPS_OPS_GH_BROKER_GHCR_PULL_USERNAME: ${{ github.actor }}\n"
        "          VPS_OPS_YKM_CLOUDFLARED_TUNNEL_TOKEN: ${{ secrets.VPS_OPS_YKM_CLOUDFLARED_TUNNEL_TOKEN }}",
    )
    errors: list[str] = []
    validator.check_no_broker_secret_exports(mutated, errors)
    assert any("github.actor" in e for e in errors)


def test_no_broker_packages_permission(validator: ModuleType, workflow_text: str) -> None:
    errors: list[str] = []
    validator.check_no_broker_packages_permission(workflow_text, errors)
    assert errors == []


def test_packages_read_permission_is_rejected(validator: ModuleType, workflow_text: str) -> None:
    mutated = workflow_text.replace(
        "    permissions:\n      contents: read\n      issues: write\n    if:",
        "    permissions:\n      contents: read\n      issues: write\n      packages: read\n    if:",
    )
    errors: list[str] = []
    validator.check_no_broker_packages_permission(mutated, errors)
    assert any("packages: read" in e for e in errors)


def test_youknowme_deploy_preserved(validator: ModuleType, workflow_text: str) -> None:
    errors: list[str] = []
    validator.check_youknowme_deploy_preserved(workflow_text, errors)
    assert errors == []


def test_missing_youknowme_secrets_is_rejected(validator: ModuleType, workflow_text: str) -> None:
    mutated = workflow_text
    for key in (
        "VPS_OPS_YKM_CLOUDFLARED_TUNNEL_TOKEN",
        "VPS_OPS_YKM_OPENROUTER_API_KEY",
        "VPS_OPS_YKM_CURATOR_TRIGGER_TOKEN",
        "VPS_OPS_YKM_LOCAL_AUTH_SECRET",
    ):
        mutated = re.sub(rf"(?m)^\s{{10}}{key}:.*\n", "", mutated)
    errors: list[str] = []
    validator.check_youknowme_deploy_preserved(mutated, errors)
    assert any("VPS_OPS_YKM_*" in e for e in errors)
