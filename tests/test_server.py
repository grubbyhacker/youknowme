from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from starlette.testclient import TestClient

import ykm.server as server_module
from ykm.build import build_index
from ykm.embeddings import FakeEmbeddingProvider
from ykm.server import (
    INDEX_LOADING_MESSAGE,
    FEEDBACK_TOOL_DESCRIPTION,
    QUERY_TOOL_DESCRIPTION,
    SEARCH_TOOL_DESCRIPTION,
    UPLOAD_TOOL_DESCRIPTION,
    create_app,
)


def wait_until_ready(client: TestClient) -> None:
    for _ in range(100):
        response = client.get("/readyz")
        if response.status_code == 200:
            return
        time.sleep(0.01)
    raise AssertionError(f"index did not become ready: {response.text}")


def test_tool_descriptions_advertise_owner_specific_triggering() -> None:
    description = f"{QUERY_TOOL_DESCRIPTION} {SEARCH_TOOL_DESCRIPTION}".lower()

    assert "owner-specific" in description
    assert "roger" in description
    assert "my/me/home" in description
    assert "hot tub chemistry" in description
    assert "thermostat" in description
    assert "prefer this over general training data" in description


def test_write_tool_descriptions_advertise_staging_not_publishing() -> None:
    description = f"{UPLOAD_TOOL_DESCRIPTION} {FEEDBACK_TOOL_DESCRIPTION}".lower()

    assert "stage" in description
    assert "ykm-upload-authoring-guidance" in description
    assert "type: skill" in description
    assert "does not publish, index, or merge" in description
    assert "protected intake queue" in description
    assert "inert protected log" in description
    assert "not indexed" in description


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


def test_create_app_does_not_load_index_synchronously(tmp_path: Path, monkeypatch) -> None:
    def fail_if_called(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("index loading should run from lifespan")

    monkeypatch.setenv("YKM_EMBEDDING_PROVIDER", "fake")
    monkeypatch.setenv("YKM_LOCAL_AUTH_SECRET", "secret")
    monkeypatch.setattr(server_module, "YkmIndex", fail_if_called)

    client = TestClient(create_app(tmp_path / "missing-index", mode="local"))

    assert client.get("/livez").status_code == 200
    response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json() == {
        "status": "loading",
        "detail": INDEX_LOADING_MESSAGE,
    }


def test_lifespan_loads_index_in_background(tmp_path: Path, monkeypatch) -> None:
    started = threading.Event()
    finish = threading.Event()

    class BlockingIndex:
        def __init__(self, _path: Path, _provider: FakeEmbeddingProvider) -> None:
            started.set()
            if not finish.wait(5):
                raise TimeoutError("test index load timed out")

    monkeypatch.setenv("YKM_EMBEDDING_PROVIDER", "fake")
    monkeypatch.setenv("YKM_LOCAL_AUTH_SECRET", "secret")
    monkeypatch.setattr(server_module, "YkmIndex", BlockingIndex)

    with TestClient(create_app(tmp_path / "index", mode="local")) as client:
        assert started.wait(5)
        assert client.get("/livez").status_code == 200

        ready = client.get("/readyz")
        assert ready.status_code == 503
        assert ready.json() == {
            "status": "loading",
            "detail": INDEX_LOADING_MESSAGE,
        }

        mcp = client.post("/mcp", headers={"X-YKM-Local-Secret": "secret"})
        assert mcp.status_code == 503
        assert mcp.json() == {"detail": INDEX_LOADING_MESSAGE}

        finish.set()
        wait_until_ready(client)


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
        wait_until_ready(client)
        response = client.post("/mcp", headers={"X-YKM-Local-Secret": "secret"})

    assert response.status_code == 400
    assert response.text == "Invalid Content-Type header"


def test_curator_status_requires_local_secret(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("YKM_EMBEDDING_PROVIDER", "fake")
    monkeypatch.setenv("YKM_LOCAL_AUTH_SECRET", "secret")
    monkeypatch.setenv("YKM_INTAKE_PATH", str(tmp_path / "intake"))

    client = TestClient(create_app(tmp_path / "index", mode="local"))
    response = client.get("/curator/status")

    assert response.status_code == 403


def test_curator_status_returns_empty_queue_without_last_run(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("YKM_EMBEDDING_PROVIDER", "fake")
    monkeypatch.setenv("YKM_LOCAL_AUTH_SECRET", "secret")
    monkeypatch.setenv("YKM_INTAKE_PATH", str(tmp_path / "intake"))

    client = TestClient(create_app(tmp_path / "index", mode="local"))
    response = client.get("/curator/status", headers={"X-YKM-Local-Secret": "secret"})

    assert response.status_code == 200
    assert response.json() == {
        "queue_depth": 0,
        "oldest_pending_seconds": 0,
        "last_run": None,
    }


def test_curator_status_counts_pending_uploads_and_reads_last_run(
    tmp_path: Path, monkeypatch
) -> None:
    intake = tmp_path / "intake"
    pending = intake / "uploads" / "pending"
    old_upload = pending / "upl_old"
    new_upload = pending / "upl_new"
    claimed_upload = pending / "upl_claimed"
    old_upload.mkdir(parents=True)
    new_upload.mkdir()
    claimed_upload.mkdir()
    (claimed_upload / "curator.json").write_text("{}\n", encoding="utf-8")
    (pending / "not-a-directory").write_text("ignored\n", encoding="utf-8")
    os.utime(old_upload, (1_000, 1_000))
    os.utime(new_upload, (7_500, 7_500))
    os.utime(claimed_upload, (500, 500))
    (intake / "curator-status.json").write_text(
        json.dumps(
            {
                "timestamp": "2026-06-13T18:00:00Z",
                "status": "success",
                "message": "opened PR #42",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(server_module.time, "time", lambda: 10_000.0)
    monkeypatch.setenv("YKM_EMBEDDING_PROVIDER", "fake")
    monkeypatch.setenv("YKM_LOCAL_AUTH_SECRET", "secret")
    monkeypatch.setenv("YKM_INTAKE_PATH", str(intake))

    client = TestClient(create_app(tmp_path / "index", mode="local"))
    response = client.get("/curator/status", headers={"X-YKM-Local-Secret": "secret"})

    assert response.status_code == 200
    assert response.json() == {
        "queue_depth": 2,
        "oldest_pending_seconds": 9_000,
        "last_run": {
            "timestamp": "2026-06-13T18:00:00Z",
            "status": "success",
            "message": "opened PR #42",
        },
    }


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
