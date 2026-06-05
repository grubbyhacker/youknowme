from __future__ import annotations

from pathlib import Path

from starlette.testclient import TestClient

from ykm.build import build_index
from ykm.embeddings import FakeEmbeddingProvider
from ykm.server import create_app


def test_private_liveness_has_no_provenance(tmp_path: Path, monkeypatch) -> None:
    index_path = tmp_path / "index"
    build_index(Path("fixtures/corpus"), index_path, FakeEmbeddingProvider())
    monkeypatch.setenv("YKM_EMBEDDING_PROVIDER", "fake")
    monkeypatch.setenv("YKM_LOCAL_AUTH_SECRET", "secret")

    client = TestClient(create_app(index_path, mode="local"))
    response = client.get("/livez")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "YouKnowMe"}


def test_local_mcp_path_requires_local_secret(tmp_path: Path, monkeypatch) -> None:
    index_path = tmp_path / "index"
    build_index(Path("fixtures/corpus"), index_path, FakeEmbeddingProvider())
    monkeypatch.setenv("YKM_EMBEDDING_PROVIDER", "fake")
    monkeypatch.setenv("YKM_LOCAL_AUTH_SECRET", "secret")

    client = TestClient(create_app(index_path, mode="local"))
    response = client.post("/mcp")

    assert response.status_code == 403


def test_local_mcp_path_accepts_local_secret_before_transport_validation(
    tmp_path: Path, monkeypatch
) -> None:
    index_path = tmp_path / "index"
    build_index(Path("fixtures/corpus"), index_path, FakeEmbeddingProvider())
    monkeypatch.setenv("YKM_EMBEDDING_PROVIDER", "fake")
    monkeypatch.setenv("YKM_LOCAL_AUTH_SECRET", "secret")

    with TestClient(create_app(index_path, mode="local")) as client:
        response = client.post("/mcp", headers={"X-YKM-Local-Secret": "secret"})

    assert response.status_code == 400
    assert response.text == "Invalid Content-Type header"


def test_public_mcp_path_fails_closed_without_cloudflare_jwt(tmp_path: Path, monkeypatch) -> None:
    index_path = tmp_path / "index"
    build_index(Path("fixtures/corpus"), index_path, FakeEmbeddingProvider())
    monkeypatch.setenv("YKM_EMBEDDING_PROVIDER", "fake")
    monkeypatch.setenv("YKM_OWNER_EMAIL", "owner@example.com")
    monkeypatch.setenv("YKM_CLOUDFLARE_TEAM_DOMAIN", "https://team.cloudflareaccess.com")
    monkeypatch.setenv("YKM_CLOUDFLARE_AUD", "aud")

    client = TestClient(create_app(index_path, mode="public"))
    response = client.post("/mcp")

    assert response.status_code == 403
    assert response.json() == {
        "detail": "forbidden",
        "reason": "missing Cloudflare Access JWT",
    }
