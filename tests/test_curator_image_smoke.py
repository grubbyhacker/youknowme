"""Docker-gated smoke test for the dedicated Curator agent image.

Builds `Dockerfile.curator` and runs its fixed entrypoint against a fixture work
item, asserting the container invocation contract end to end:

  * /input is mounted read-only and /input/work-item.json is consumed;
  * versioned results are written under /output (run-report.json with the
    Curator report schema);
  * the image records non-secret provenance (labels + on-disk manifest);
  * the fixed entrypoint requires no mode argument.

This is the single real exercise of the image the design permits for this stage.
It is skipped unless Docker is available AND the runtime base can be resolved,
so it never blocks the offline gate. Set YKM_CURATOR_AGENT_RUNTIME_BASE to pin a
specific base coordinate (CI passes the immutable digest); it defaults to the
moving `main` tag for local runs.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASE = "ghcr.io/grubbyhacker/agent-runtime-base:main"


def _docker() -> str | None:
    return shutil.which("docker")


def _docker_usable(docker: str) -> bool:
    try:
        subprocess.run([docker, "info"], capture_output=True, check=True, timeout=60)
        return True
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False


def _base_resolvable(docker: str, base: str) -> bool:
    # Cheap resolution probe: manifest inspect does not pull layers.
    try:
        result = subprocess.run(
            [docker, "manifest", "inspect", base],
            capture_output=True,
            timeout=120,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def test_curator_image_builds_and_honours_the_contract(tmp_path: Path) -> None:
    docker = _docker()
    if not docker or not _docker_usable(docker):
        pytest.skip("Docker is not available; skipping Curator image smoke test.")

    base = os.getenv("YKM_CURATOR_AGENT_RUNTIME_BASE", DEFAULT_BASE)
    if not _base_resolvable(docker, base):
        pytest.skip(f"runtime base {base} is not resolvable; skipping Curator image smoke test.")

    tag = f"youknowme-curator-smoke:{uuid.uuid4().hex[:12]}"
    source_revision = "smoke-source-revision"
    dep_manifest = subprocess.run(
        [
            "python3",
            str(ROOT / "scripts" / "curator-image-provenance.py"),
            "--source-revision",
            source_revision,
            "--repo-root",
            str(ROOT),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    dep_sha = json.loads(dep_manifest.stdout)["dependency_manifest_sha256"]

    build = subprocess.run(
        [
            docker,
            "build",
            "--platform",
            "linux/amd64",
            "-f",
            str(ROOT / "Dockerfile.curator"),
            "--build-arg",
            f"AGENT_RUNTIME_BASE={base}",
            "--build-arg",
            f"SOURCE_REVISION={source_revision}",
            "--build-arg",
            f"DEPENDENCY_MANIFEST_SHA256={dep_sha}",
            "-t",
            tag,
            str(ROOT),
        ],
        capture_output=True,
        text=True,
    )
    if build.returncode != 0:
        pytest.fail(f"docker build failed:\n{build.stdout}\n{build.stderr}")

    try:
        # Provenance labels are recorded on the image.
        inspect = subprocess.run(
            [docker, "image", "inspect", "--format", "{{json .Config.Labels}}", tag],
            capture_output=True,
            text=True,
            check=True,
        )
        labels = json.loads(inspect.stdout)
        assert labels["io.grubbyhacker.agent-image.type"] == "youknowme-curator"
        assert labels["io.grubbyhacker.agent-image.source-revision"] == source_revision
        assert labels["io.grubbyhacker.agent-image.dependency-manifest-sha256"] == dep_sha
        assert labels["io.grubbyhacker.agent-image.input-contract"] == "/input/work-item.json"
        assert labels["io.grubbyhacker.agent-image.output-contract"] == "/output"

        # Run the fixed entrypoint against the fixture work item.
        input_dir = tmp_path / "input"
        output_dir = tmp_path / "output"
        input_dir.mkdir()
        output_dir.mkdir()
        shutil.copyfile(
            ROOT / "fixtures" / "curator-agent" / "work-item.json",
            input_dir / "work-item.json",
        )

        run = subprocess.run(
            [
                docker,
                "run",
                "--rm",
                "--network",
                "none",
                "-v",
                f"{input_dir}:/input:ro",
                "-v",
                f"{output_dir}:/output",
                tag,
            ],
            capture_output=True,
            text=True,
            timeout=300,
        )
        # The dry-run report may be pass or fail depending on fixture state; the
        # contract we assert is that the report was WRITTEN under /output.
        report_path = output_dir / "run-report.json"
        assert report_path.is_file(), f"missing {report_path}\nSTDOUT:{run.stdout}\nSTDERR:{run.stderr}"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        assert report["schema_version"] == "1"
        assert report["run_id"] == "curator-agent-image-smoke"

        # The on-disk provenance manifest is present inside the image.
        manifest = subprocess.run(
            [
                docker,
                "run",
                "--rm",
                "--entrypoint",
                "cat",
                tag,
                "/usr/local/share/agent-image/dependency-manifest.sha256",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        assert manifest.stdout.strip() == dep_sha
    finally:
        subprocess.run([docker, "image", "rm", "-f", tag], capture_output=True)
