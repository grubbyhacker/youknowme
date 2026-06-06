from __future__ import annotations

from pathlib import Path

from starlette.testclient import TestClient

from ykm.build import build_index
from ykm.embeddings import FakeEmbeddingProvider
from ykm.server import QUERY_TOOL_DESCRIPTION, SEARCH_TOOL_DESCRIPTION, create_app


def test_tool_descriptions_advertise_owner_specific_triggering() -> None:
    description = f"{QUERY_TOOL_DESCRIPTION} {SEARCH_TOOL_DESCRIPTION}".lower()

    assert "owner-specific" in description
    assert "roger" in description
    assert "my/me/home" in description
    assert "hot tub chemistry" in description
    assert "thermostat" in description
    assert "prefer this over general training data" in description


def test_private_liveness_has_no_provenance(tmp_path: Path, monkeypatch) -> None:
    index_path = tmp_path / "index"
    build_index(Path("fixtures/corpus"), index_path, FakeEmbeddingProvider())
    monkeypatch.setenv("YKM_EMBEDDING_PROVIDER", "fake")
    monkeypatch.setenv("YKM_LOCAL_AUTH_SECRET", "secret")

    client = TestClient(create_app(index_path, mode="local"))
    response = client.get("/livez")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "YouKnowMe"}


def test_http_health_has_no_provenance(tmp_path: Path, monkeypatch) -> None:
    index_path = tmp_path / "index"
    build_index(Path("fixtures/corpus"), index_path, FakeEmbeddingProvider())
    monkeypatch.setenv("YKM_EMBEDDING_PROVIDER", "fake")
    monkeypatch.setenv("YKM_LOCAL_AUTH_SECRET", "secret")

    client = TestClient(create_app(index_path, mode="local"))
    response = client.get("/health")

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

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == (
        'Bearer realm="YouKnowMe", '
        'resource_metadata="https://mcp.fleiglabs.cc/.well-known/oauth-protected-resource/mcp"'
    )
    assert response.json() == {"detail": "unauthorized"}


def test_oauth_protected_resource_metadata_paths(tmp_path: Path, monkeypatch) -> None:
    index_path = tmp_path / "index"
    build_index(Path("fixtures/corpus"), index_path, FakeEmbeddingProvider())
    monkeypatch.setenv("YKM_EMBEDDING_PROVIDER", "fake")
    monkeypatch.setenv("YKM_OWNER_EMAIL", "owner@example.com")
    monkeypatch.setenv("YKM_CLOUDFLARE_TEAM_DOMAIN", "https://team.cloudflareaccess.com")
    monkeypatch.setenv("YKM_CLOUDFLARE_AUD", "aud")
    monkeypatch.setenv("YKM_MCP_RESOURCE_URL", "https://mcp.fleiglabs.cc/mcp")

    client = TestClient(create_app(index_path, mode="public"))

    for path in (
        "/.well-known/oauth-protected-resource",
        "/.well-known/oauth-protected-resource/mcp",
    ):
        response = client.get(path)

        assert response.status_code == 200
        assert response.json() == {
            "resource": "https://mcp.fleiglabs.cc/mcp",
            "authorization_servers": ["https://team.cloudflareaccess.com"],
            "bearer_methods_supported": ["header"],
        }
