"""Offline tests for the Curator release workflow validator.

Exercise the validator against the checked-in workflow (must pass) and against
mutated copies that break each invariant (must fail). No workflow is run, no
network is joined, no broker is reached.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]
VALIDATOR_PATH = ROOT / "scripts" / "validate-curator-release-workflow.py"
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "curator-release.yml"


def _load_validator() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_curator_release_validator", VALIDATOR_PATH)
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


def test_manual_only_trigger(validator: ModuleType, workflow_text: str) -> None:
    errors: list[str] = []
    validator.check_manual_only(workflow_text, errors)
    assert errors == []


def test_push_trigger_is_rejected(validator: ModuleType, workflow_text: str) -> None:
    mutated = workflow_text.replace(
        "on:\n  workflow_dispatch:",
        "on:\n  push:\n    branches: [main]\n  workflow_dispatch:",
    )
    errors: list[str] = []
    validator.check_manual_only(mutated, errors)
    assert any("manual-only" in e for e in errors)


def test_no_caller_image_or_generation_input(validator: ModuleType, workflow_text: str) -> None:
    errors: list[str] = []
    validator.check_no_caller_image_or_generation(workflow_text, errors)
    assert errors == []


def test_caller_generation_input_is_rejected(validator: ModuleType, workflow_text: str) -> None:
    mutated = workflow_text.replace(
        "    inputs:\n",
        "    inputs:\n      generation:\n        type: string\n",
    )
    errors: list[str] = []
    validator.check_no_caller_image_or_generation(mutated, errors)
    assert any("generation" in e for e in errors)


def test_pinned_broker_commit(validator: ModuleType, workflow_text: str) -> None:
    errors: list[str] = []
    validator.check_pinned_broker_commit(workflow_text, errors)
    assert errors == []


def test_moving_broker_ref_is_rejected(validator: ModuleType, workflow_text: str) -> None:
    mutated = workflow_text.replace(
        "ref: ${{ env.BROKER_COMMIT }}",
        "ref: main",
    )
    errors: list[str] = []
    validator.check_pinned_broker_commit(mutated, errors)
    assert errors, "a moving broker ref must be rejected"


def test_publisher_promoter_separation(validator: ModuleType, workflow_text: str) -> None:
    errors: list[str] = []
    validator.check_publisher_promoter_separation(workflow_text, errors)
    assert errors == []


def test_promoter_token_in_publish_is_rejected(validator: ModuleType, workflow_text: str) -> None:
    mutated = workflow_text.replace(
        "BROKER_PUBLISHER_TOKEN: ${{ secrets.VPS_OPS_GH_BROKER_RELEASE_PUBLISHER_OPERATOR_TOKEN }}",
        "BROKER_PUBLISHER_TOKEN: ${{ secrets.VPS_OPS_GH_BROKER_RELEASE_PUBLISHER_OPERATOR_TOKEN }}\n"
        "          LEAK: ${{ secrets.VPS_OPS_GH_BROKER_RELEASE_PROMOTER_OPERATOR_TOKEN }}",
    )
    errors: list[str] = []
    validator.check_publisher_promoter_separation(mutated, errors)
    assert any("promoter token" in e for e in errors)


def test_tailscale_before_call(validator: ModuleType, workflow_text: str) -> None:
    errors: list[str] = []
    validator.check_tailscale_before_call(workflow_text, errors)
    assert errors == []


def test_archive_and_provenance_binding(validator: ModuleType, workflow_text: str) -> None:
    errors: list[str] = []
    validator.check_archive_and_provenance_binding(workflow_text, errors)
    assert errors == []


def test_generation_only_promotion(validator: ModuleType, workflow_text: str) -> None:
    errors: list[str] = []
    validator.check_generation_only_promotion(workflow_text, errors)
    assert errors == []


def test_promote_with_image_reference_is_rejected(validator: ModuleType, workflow_text: str) -> None:
    mutated = workflow_text.replace(
        '-generation "${{ steps.publish.outputs.generation }}"',
        '-generation "${{ steps.publish.outputs.generation }}" \\\n            -image "ghcr.io/x/y@sha256:deadbeef"',
    )
    errors: list[str] = []
    validator.check_generation_only_promotion(mutated, errors)
    assert any("image reference" in e for e in errors)
