#!/usr/bin/env python3
"""Compute the non-secret provenance for the dedicated Curator agent image.

Offline and deterministic: it reads files from the checkout and emits a JSON
provenance record. It never builds an image, reaches a registry, or reads a
secret.

The dependency-manifest SHA256 is the hash of the exact files that define the
Python dependency closure baked into the image -- `pyproject.toml` and
`uv.lock`, hashed in that fixed order with their byte content. The same files
are the first COPY layer in `Dockerfile.curator`, so this hash pins what the
image's dependencies were built from.

Usage:
    provenance.py [--source-revision REV] [--platform linux/amd64]
                  [--repo-root PATH]

Emits, on stdout, a JSON object with:
    source_revision            git revision the image is built from (or "unknown")
    dependency_manifest_sha256 sha256 over the ordered manifest files
    dependency_manifest_files  the files that were hashed, in order
    platform                   the target build platform
    schema_version             provenance record schema version
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

PROVENANCE_SCHEMA_VERSION = "1"

# The files that define the dependency closure, hashed in this fixed order.
DEPENDENCY_MANIFEST_FILES = ("pyproject.toml", "uv.lock")


def compute_dependency_manifest_sha256(repo_root: Path) -> str:
    digest = hashlib.sha256()
    for relative in DEPENDENCY_MANIFEST_FILES:
        path = repo_root / relative
        if not path.is_file():
            raise SystemExit(f"provenance: missing dependency manifest file: {path}")
        # Domain-separate each file so concatenation is unambiguous.
        payload = path.read_bytes()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


def resolve_source_revision(repo_root: Path, override: str | None) -> str:
    if override:
        return override
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    revision = result.stdout.strip()
    return revision or "unknown"


def build_provenance(repo_root: Path, source_revision: str | None, platform: str) -> dict[str, object]:
    return {
        "schema_version": PROVENANCE_SCHEMA_VERSION,
        "source_revision": resolve_source_revision(repo_root, source_revision),
        "dependency_manifest_sha256": compute_dependency_manifest_sha256(repo_root),
        "dependency_manifest_files": list(DEPENDENCY_MANIFEST_FILES),
        "platform": platform,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-revision", default=None)
    parser.add_argument("--platform", default="linux/amd64")
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    args = parser.parse_args(argv)

    provenance = build_provenance(args.repo_root, args.source_revision, args.platform)
    json.dump(provenance, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
