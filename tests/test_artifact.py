from __future__ import annotations

import json
import tarfile
from pathlib import Path

import pytest

from ykm.artifact import ArtifactError, package_index, validate_index
from ykm.build import build_index
from ykm.contracts import QueryRequest, RetrieveRequest
from ykm.embeddings import FakeEmbeddingProvider
from ykm.index import YkmIndex


def test_validate_index_accepts_built_index(tmp_path: Path) -> None:
    index_path = tmp_path / "index"
    build_index(Path("fixtures/corpus"), index_path, FakeEmbeddingProvider())

    result = validate_index(index_path)

    assert result["status"] == "ok"
    assert result["embedding_provider"] == "fake"
    assert result["chunk_count"] > 0


def test_validate_index_rejects_missing_required_files(tmp_path: Path) -> None:
    index_path = tmp_path / "index"
    index_path.mkdir()

    with pytest.raises(ArtifactError, match="manifest.json"):
        validate_index(index_path)


def test_package_index_writes_redeployable_bundle(tmp_path: Path) -> None:
    index_path = tmp_path / "index"
    out = tmp_path / "artifacts"
    provider = FakeEmbeddingProvider()
    build_index(Path("fixtures/corpus"), index_path, provider)

    result = package_index(index_path, out)

    tarball = Path(result["tarball"])
    sha = Path(result["sha256"])
    report_path = Path(result["build_report"])
    assert tarball.exists()
    assert sha.exists()
    assert report_path.exists()
    assert result["artifact_sha256"] in sha.read_text(encoding="utf-8")

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["artifact_schema_version"] == "1"
    assert report["build_code_package"] == "youknowme"
    assert report["build_code_version"]
    assert report["manifest"]["embedding_provider"] == "fake"

    unpacked = tmp_path / "unpacked"
    unpacked.mkdir()
    with tarfile.open(tarball, "r:gz") as tar:
        tar.extractall(unpacked, filter="data")

    unpacked_index = unpacked / "index"
    validate_index(unpacked_index)
    loaded = YkmIndex(unpacked_index, provider)
    query = loaded.query(QueryRequest(query="weekly spa maintenance", tags=["spa"], limit=1))
    assert query.results

    retrieved = loaded.retrieve(
        RetrieveRequest(locator=query.results[0].section_id, kind="section_id")
    )
    assert retrieved.found is True
