#!/usr/bin/env python3
"""Offline validator for the dedicated Curator agent image contract.

Encodes the invariant Stage 2 establishes
(agent-infra-docs/design/agent-platform-coupling.md): Curator ships as a
dedicated image derived from the versioned shared runtime base, with a fixed
container invocation contract, and it is NOT the YouKnowMe service image.

This check is offline. It reads `Dockerfile.curator`, the entrypoint script, and
the service `Dockerfile`, and compares their text. It never builds an image or
reaches a registry, so a contract regression is caught in a pull request rather
than at publish or deploy time.

Invariants asserted:

  1. The Curator image derives FROM the shared runtime base
     (ghcr.io/<owner>/agent-runtime-base), via an overridable ARG so CI pins an
     immutable digest.
  2. The fixed entrypoint is the curator agent entrypoint; there is no service
     server CMD (`ykm serve`) anywhere in the Curator image.
  3. The platform input contract is /input/work-item.json and results are
     written under /output; the entrypoint passes exactly those paths to
     `curator run`.
  4. Mode is NOT passed on the command line by the entrypoint (mode travels in
     the typed work item).
  5. The image records non-secret provenance: a source revision and a
     dependency-manifest SHA256, as build args wired into image labels and an
     on-disk manifest under /usr/local/share/agent-image.
  6. The Curator image is not the service image: it does not COPY the whole
     service the way the service Dockerfile does, and it declares the
     youknowme-curator agent type.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CURATOR_DOCKERFILE = REPO_ROOT / "Dockerfile.curator"
CURATOR_ENTRYPOINT = REPO_ROOT / "scripts" / "curator-agent-entrypoint.sh"
SERVICE_DOCKERFILE = REPO_ROOT / "Dockerfile"

INPUT_CONTRACT = "/input/work-item.json"
OUTPUT_CONTRACT = "/output"
AGENT_TYPE = "youknowme-curator"


def _require_file(path: Path, errors: list[str]) -> str:
    if not path.is_file():
        errors.append(f"missing required file: {path}")
        return ""
    return path.read_text(encoding="utf-8")


def check_derives_from_runtime_base(dockerfile: str, errors: list[str]) -> None:
    if not re.search(r"ARG\s+AGENT_RUNTIME_BASE=", dockerfile):
        errors.append(
            "Dockerfile.curator must declare an overridable ARG AGENT_RUNTIME_BASE "
            "so CI can pin an immutable base digest."
        )
    if "agent-runtime-base" not in dockerfile:
        errors.append(
            "Dockerfile.curator must derive from the shared agent-runtime-base image."
        )
    if not re.search(r"FROM\s+\$\{AGENT_RUNTIME_BASE\}", dockerfile):
        errors.append(
            "Dockerfile.curator runtime stage must be FROM ${AGENT_RUNTIME_BASE}."
        )


def check_fixed_entrypoint_no_server(dockerfile: str, entrypoint: str, errors: list[str]) -> None:
    if 'ENTRYPOINT ["/usr/local/bin/curator-agent-entrypoint"]' not in dockerfile:
        errors.append(
            "Dockerfile.curator must set the fixed curator-agent-entrypoint as ENTRYPOINT."
        )
    # The service server must not be the agent image's job.
    if "ykm serve" in dockerfile or "ykm serve" in entrypoint:
        errors.append(
            "The Curator agent image must not run the YouKnowMe server (`ykm serve`)."
        )


def check_io_contract(dockerfile: str, entrypoint: str, errors: list[str]) -> None:
    for text, where in ((dockerfile, "Dockerfile.curator"), (entrypoint, "entrypoint")):
        if INPUT_CONTRACT not in text:
            errors.append(f"{where} must reference the input contract {INPUT_CONTRACT}.")
    # The entrypoint must pass exactly the fixed input and output to `curator run`.
    if not re.search(r"curator run", entrypoint):
        errors.append("entrypoint must invoke `curator run`.")
    if "--task" not in entrypoint or "--output" not in entrypoint:
        errors.append("entrypoint must pass --task and --output to `curator run`.")
    if OUTPUT_CONTRACT not in entrypoint and "YKM_CURATOR_OUTPUT" not in entrypoint:
        errors.append(f"entrypoint must write results under {OUTPUT_CONTRACT}.")


def check_mode_not_on_command_line(entrypoint: str, errors: list[str]) -> None:
    # Mode travels in the typed input; the entrypoint must not inject a --mode.
    if re.search(r"--mode\b", entrypoint):
        errors.append(
            "entrypoint must not pass --mode on the command line; mode travels in "
            "the typed work item."
        )


def check_provenance(dockerfile: str, errors: list[str]) -> None:
    for arg in ("SOURCE_REVISION", "DEPENDENCY_MANIFEST_SHA256"):
        if not re.search(rf"ARG\s+{arg}=", dockerfile):
            errors.append(f"Dockerfile.curator must declare an ARG {arg} for provenance.")
    required_labels = (
        "io.grubbyhacker.agent-image.type",
        "io.grubbyhacker.agent-image.source-revision",
        "io.grubbyhacker.agent-image.dependency-manifest-sha256",
        "io.grubbyhacker.agent-image.runtime-base",
        "io.grubbyhacker.agent-image.input-contract",
        "io.grubbyhacker.agent-image.output-contract",
    )
    for label in required_labels:
        if label not in dockerfile:
            errors.append(f"Dockerfile.curator must set the provenance label {label}.")
    if "/usr/local/share/agent-image/dependency-manifest.sha256" not in dockerfile:
        errors.append(
            "Dockerfile.curator must write the dependency manifest hash to "
            "/usr/local/share/agent-image/dependency-manifest.sha256."
        )


def check_not_service_image(dockerfile: str, service_dockerfile: str, errors: list[str]) -> None:
    if AGENT_TYPE not in dockerfile:
        errors.append(f'Dockerfile.curator must declare the "{AGENT_TYPE}" agent type.')
    # The service image copies the whole `src` tree and runs the server. The agent
    # image copies only the curator application code (+ the single ykm module it
    # imports). Guard against the agent image regressing into `COPY src ./src`.
    if re.search(r"^COPY\s+src\s+\./src\b", dockerfile, flags=re.MULTILINE):
        errors.append(
            "Dockerfile.curator must not COPY the whole `src` tree (that is the "
            "service image). Copy only Curator application code."
        )
    if "ykm serve" not in service_dockerfile:
        # Sanity anchor: if the service image stops running the server, this
        # validator's service/agent distinction needs revisiting.
        errors.append(
            "sanity check failed: the service Dockerfile no longer runs `ykm serve`; "
            "revisit the service/agent-image distinction in this validator."
        )


def main() -> int:
    errors: list[str] = []
    dockerfile = _require_file(CURATOR_DOCKERFILE, errors)
    entrypoint = _require_file(CURATOR_ENTRYPOINT, errors)
    service_dockerfile = _require_file(SERVICE_DOCKERFILE, errors)
    if errors:
        _report(errors)
        return 1

    check_derives_from_runtime_base(dockerfile, errors)
    check_fixed_entrypoint_no_server(dockerfile, entrypoint, errors)
    check_io_contract(dockerfile, entrypoint, errors)
    check_mode_not_on_command_line(entrypoint, errors)
    check_provenance(dockerfile, errors)
    check_not_service_image(dockerfile, service_dockerfile, errors)

    if errors:
        _report(errors)
        return 1

    print("Curator image contract check passed (dedicated image, fixed contract, provenance).")
    return 0


def _report(errors: list[str]) -> None:
    print("Curator image contract check failed:", file=sys.stderr)
    for error in errors:
        print(f"  - {error}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
