from __future__ import annotations

import gzip
import hashlib
import io
import json
import tarfile
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from ykm.contracts import ArtifactManifest, ChunkRecord
from ykm.embeddings import EmbeddingProvider
from ykm.index import YkmIndex


ARTIFACT_SCHEMA_VERSION = "1"
TARBALL_ROOT = "index"


class ArtifactError(ValueError):
    pass


class ManifestOnlyEmbeddingProvider(EmbeddingProvider):
    def __init__(self, manifest: ArtifactManifest) -> None:
        self.name = manifest.embedding_provider
        self.model = manifest.embedding_model
        self.dimensions = manifest.embedding_dimensions

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("manifest-only embedding provider cannot embed query text")


def validate_index(index: Path) -> dict[str, Any]:
    index = index.resolve()
    if not index.exists():
        raise ArtifactError(f"index path does not exist: {index}")
    if not index.is_dir():
        raise ArtifactError(f"index path is not a directory: {index}")

    manifest_path = index / "manifest.json"
    chunks_path = index / "chunks.jsonl"
    lancedb_path = index / "lancedb"
    for path in (manifest_path, chunks_path, lancedb_path):
        if not path.exists():
            raise ArtifactError(f"index is missing required path: {path.relative_to(index)}")

    manifest = ArtifactManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    if manifest.embedding_dimensions <= 0:
        raise ArtifactError("manifest embedding_dimensions must be positive")
    if not manifest.embedding_provider:
        raise ArtifactError("manifest embedding_provider is required")
    if not manifest.embedding_model:
        raise ArtifactError("manifest embedding_model is required")

    chunks = [
        ChunkRecord.model_validate_json(line)
        for line in chunks_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if len(chunks) != manifest.chunk_count:
        raise ArtifactError(
            f"manifest chunk_count={manifest.chunk_count} does not match chunks.jsonl={len(chunks)}"
        )

    loaded = YkmIndex(index, ManifestOnlyEmbeddingProvider(manifest))
    if len(loaded.chunks) != manifest.chunk_count:
        raise ArtifactError("YkmIndex loaded a different number of chunks than the manifest records")

    if chunks:
        vector = loaded._vector_for(chunks[0].chunk_id)
        if len(vector) != manifest.embedding_dimensions:
            raise ArtifactError(
                f"LanceDB vector length {len(vector)} does not match manifest dimensions "
                f"{manifest.embedding_dimensions}"
            )

    return {
        "status": "ok",
        "index_path": str(index),
        "build_id": manifest.build_id,
        "source_commit": manifest.source_commit,
        "embedding_provider": manifest.embedding_provider,
        "embedding_model": manifest.embedding_model,
        "embedding_dimensions": manifest.embedding_dimensions,
        "chunk_count": manifest.chunk_count,
        "warning_count": manifest.warning_count,
        "quarantined_count": manifest.quarantined_count,
    }


def package_index(index: Path, out: Path, *, artifact_name: str | None = None) -> dict[str, Any]:
    validation = validate_index(index)
    index = index.resolve()
    out = out.resolve()
    out.mkdir(parents=True, exist_ok=True)

    manifest = ArtifactManifest.model_validate_json(
        (index / "manifest.json").read_text(encoding="utf-8")
    )
    name = artifact_name or f"youknowme-index-{_safe_name(manifest.source_commit)}-{manifest.build_id}"
    tarball_path = out / f"{name}.tar.gz"
    sha_path = out / f"{name}.sha256"
    report_path = out / f"{name}.build-report.json"

    tarball_bytes = _deterministic_tar_gz(index)
    tarball_path.write_bytes(tarball_bytes)
    artifact_sha256 = hashlib.sha256(tarball_bytes).hexdigest()
    sha_path.write_text(f"{artifact_sha256}  {tarball_path.name}\n", encoding="utf-8")

    report = {
        "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
        "artifact_name": name,
        "artifact_file": tarball_path.name,
        "artifact_sha256": artifact_sha256,
        "created_at": datetime.now(UTC).isoformat(),
        "tarball_root": TARBALL_ROOT,
        "build_code_package": "youknowme",
        "build_code_version": _package_version(),
        "validation": validation,
        "manifest": manifest.model_dump(mode="json"),
    }
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    return {
        "status": "ok",
        "artifact_name": name,
        "tarball": str(tarball_path),
        "sha256": str(sha_path),
        "build_report": str(report_path),
        "artifact_sha256": artifact_sha256,
        "source_commit": manifest.source_commit,
        "build_id": manifest.build_id,
    }


def _deterministic_tar_gz(index: Path) -> bytes:
    tar_buffer = io.BytesIO()
    with tarfile.open(fileobj=tar_buffer, mode="w", format=tarfile.PAX_FORMAT) as tar:
        _add_directory(tar, TARBALL_ROOT)
        for path in sorted(index.rglob("*"), key=lambda item: item.relative_to(index).as_posix()):
            rel = path.relative_to(index).as_posix()
            arcname = f"{TARBALL_ROOT}/{rel}"
            if path.is_symlink():
                raise ArtifactError(f"index artifacts may not contain symlinks: {rel}")
            if path.is_dir():
                _add_directory(tar, arcname)
            elif path.is_file():
                info = tar.gettarinfo(str(path), arcname)
                _normalize_tarinfo(info)
                with path.open("rb") as handle:
                    tar.addfile(info, handle)
            else:
                raise ArtifactError(f"unsupported index artifact path type: {rel}")

    gzip_buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=gzip_buffer, mode="wb", filename="", mtime=0) as gz:
        gz.write(tar_buffer.getvalue())
    return gzip_buffer.getvalue()


def _add_directory(tar: tarfile.TarFile, arcname: str) -> None:
    info = tarfile.TarInfo(arcname.rstrip("/") + "/")
    info.type = tarfile.DIRTYPE
    _normalize_tarinfo(info)
    tar.addfile(info)


def _normalize_tarinfo(info: tarfile.TarInfo) -> None:
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mtime = 0
    if info.isdir():
        info.mode = 0o755
    else:
        info.mode = 0o644


def _safe_name(value: str) -> str:
    return "".join(char if char.isalnum() or char in "._-" else "-" for char in value)[:120]


def _package_version() -> str:
    try:
        return version("youknowme")
    except PackageNotFoundError:
        return "unknown"
