"""Offline tests for the dedicated Curator agent image contract and provenance.

These exercise the validators and the provenance computation directly, against
the checked-in files. They build no image and reach no registry, so they are the
cheapest check that can fail for this seam (schema/lint, then these, before any
Docker smoke).
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def _load_script(name: str) -> ModuleType:
    path = SCRIPTS / name
    spec = importlib.util.spec_from_file_location(f"_curator_script_{name}", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Image contract validator
# ---------------------------------------------------------------------------


def test_curator_image_contract_validator_passes_on_checked_in_files() -> None:
    validator = _load_script("validate-curator-image-contract.py")
    assert validator.main() == 0


def test_curator_dockerfile_derives_from_runtime_base_via_arg() -> None:
    dockerfile = (ROOT / "Dockerfile.curator").read_text(encoding="utf-8")
    assert "ARG AGENT_RUNTIME_BASE=" in dockerfile
    assert "agent-runtime-base" in dockerfile
    assert "FROM ${AGENT_RUNTIME_BASE}" in dockerfile


def test_curator_image_is_not_the_service_image() -> None:
    dockerfile = (ROOT / "Dockerfile.curator").read_text(encoding="utf-8")
    # The agent image runs the fixed entrypoint, never the service server.
    assert "ykm serve" not in dockerfile
    assert 'ENTRYPOINT ["/usr/local/bin/curator-agent-entrypoint"]' in dockerfile
    # It declares the agent type and does not copy the whole service tree.
    assert "youknowme-curator" in dockerfile
    assert "\nCOPY src ./src\n" not in dockerfile


def test_curator_entrypoint_implements_fixed_contract() -> None:
    entrypoint = (ROOT / "scripts" / "curator-agent-entrypoint.sh").read_text(encoding="utf-8")
    assert "/input/work-item.json" in entrypoint
    assert "curator run" in entrypoint
    assert "--task" in entrypoint
    assert "--output" in entrypoint
    # Mode travels in the typed work item, not on the command line.
    assert "--mode" not in entrypoint


def test_curator_dockerfile_records_provenance_labels_and_manifest() -> None:
    dockerfile = (ROOT / "Dockerfile.curator").read_text(encoding="utf-8")
    assert "ARG SOURCE_REVISION=" in dockerfile
    assert "ARG DEPENDENCY_MANIFEST_SHA256=" in dockerfile
    for label in (
        "io.grubbyhacker.agent-image.type",
        "io.grubbyhacker.agent-image.source-revision",
        "io.grubbyhacker.agent-image.dependency-manifest-sha256",
        "io.grubbyhacker.agent-image.runtime-base",
        "io.grubbyhacker.agent-image.input-contract",
        "io.grubbyhacker.agent-image.output-contract",
    ):
        assert label in dockerfile
    assert "/usr/local/share/agent-image/dependency-manifest.sha256" in dockerfile


def test_contract_validator_fails_when_entrypoint_adds_mode(tmp_path: Path) -> None:
    validator = _load_script("validate-curator-image-contract.py")
    # Point the validator at a temporary bad entrypoint by monkeypatching module
    # constants is brittle; instead assert the check function directly.
    errors: list[str] = []
    validator.check_mode_not_on_command_line(
        'exec curator run --task /input/work-item.json --mode reconcile --output /output',
        errors,
    )
    assert errors, "a --mode on the command line must be rejected"


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------


def test_provenance_dependency_manifest_hash_is_deterministic_and_matches_manual() -> None:
    provenance = _load_script("curator-image-provenance.py")
    computed = provenance.compute_dependency_manifest_sha256(ROOT)

    # Recompute independently with the same domain-separated scheme.
    digest = hashlib.sha256()
    for relative in ("pyproject.toml", "uv.lock"):
        payload = (ROOT / relative).read_bytes()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    assert computed == digest.hexdigest()
    # Stable across calls.
    assert computed == provenance.compute_dependency_manifest_sha256(ROOT)


def test_provenance_record_shape_and_fields() -> None:
    provenance = _load_script("curator-image-provenance.py")
    record = provenance.build_provenance(ROOT, source_revision="deadbeef", platform="linux/amd64")
    assert record["schema_version"] == provenance.PROVENANCE_SCHEMA_VERSION
    assert record["source_revision"] == "deadbeef"
    assert record["platform"] == "linux/amd64"
    assert record["dependency_manifest_files"] == ["pyproject.toml", "uv.lock"]
    assert re.fullmatch(r"[0-9a-f]{64}", record["dependency_manifest_sha256"])


def test_provenance_cli_emits_valid_json() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "curator-image-provenance.py"),
            "--source-revision",
            "abc123",
            "--platform",
            "linux/amd64",
            "--repo-root",
            str(ROOT),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    record = json.loads(result.stdout)
    assert record["source_revision"] == "abc123"
    assert record["platform"] == "linux/amd64"
    assert re.fullmatch(r"[0-9a-f]{64}", record["dependency_manifest_sha256"])
