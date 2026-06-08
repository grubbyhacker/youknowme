from __future__ import annotations

import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import httpx
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from ykm.github_artifacts import (
    GitHubActionsClient,
    artifact_matches_current_index,
    create_app_jwt,
    read_build_report_from_artifact_zip,
    select_latest_index_artifact,
)


def test_create_app_jwt_sets_github_app_claims() -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")

    token = create_app_jwt(
        "4001682",
        private_pem,
        now=datetime(2026, 6, 8, 20, 0, 0, tzinfo=UTC),
    )

    public_key = private_key.public_key()
    payload = jwt.decode(
        token,
        public_key,
        algorithms=["RS256"],
        issuer="4001682",
        options={"verify_exp": False, "verify_iat": False},
    )
    assert payload["iss"] == "4001682"
    assert payload["iat"] < payload["exp"]


def test_select_latest_index_artifact_uses_successful_main_workflow_artifact() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/repos/grubbyhacker/ykmcorpus/actions/runs":
            return httpx.Response(
                200,
                json={
                    "workflow_runs": [
                        {
                            "id": 20,
                            "name": "Production index artifact",
                            "head_branch": "main",
                            "head_sha": "new-sha",
                            "status": "completed",
                            "conclusion": "success",
                            "created_at": "2026-06-08T20:00:00Z",
                        },
                        {
                            "id": 10,
                            "name": "Production index artifact",
                            "head_branch": "main",
                            "head_sha": "old-sha",
                            "status": "completed",
                            "conclusion": "success",
                            "created_at": "2026-06-08T19:00:00Z",
                        },
                    ],
                },
            )
        if request.url.path == "/repos/grubbyhacker/ykmcorpus/actions/runs/20/artifacts":
            return httpx.Response(
                200,
                json={
                    "artifacts": [
                        {
                            "id": 200,
                            "name": "youknowme-index-new-sha-build",
                            "expired": False,
                            "created_at": "2026-06-08T20:02:00Z",
                        }
                    ],
                },
            )
        raise AssertionError(f"unexpected request: {request.url}")

    http_client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://api.github.test",
        follow_redirects=True,
    )
    client = GitHubActionsClient(
        token="token",
        api_url="https://api.github.test",
        client=http_client,
    )

    selection = select_latest_index_artifact(client=client, repo="grubbyhacker/ykmcorpus")

    assert selection.run_id == 20
    assert selection.artifact_id == 200
    assert selection.artifact_name == "youknowme-index-new-sha-build"


def test_read_build_report_and_match_current_manifest(tmp_path: Path) -> None:
    report = {
        "manifest": {
            "source_commit": "36742b13ce6935d3ba15e2454bb6e1b4bdaee202",
            "build_id": "aed0216abf1746ff847390441a4fb459",
        }
    }
    artifact_zip = tmp_path / "artifact.zip"
    with zipfile.ZipFile(artifact_zip, "w") as archive:
        archive.writestr("youknowme-index.build-report.json", json.dumps(report))
        archive.writestr("youknowme-index.tar.gz", b"fake")
        archive.writestr("youknowme-index.sha256", "fake  youknowme-index.tar.gz\n")

    assert read_build_report_from_artifact_zip(artifact_zip) == report
    assert artifact_matches_current_index(
        build_report=report,
        current_manifest={
            "source_commit": "36742b13ce6935d3ba15e2454bb6e1b4bdaee202",
            "build_id": "aed0216abf1746ff847390441a4fb459",
        },
    )
    assert not artifact_matches_current_index(
        build_report=report,
        current_manifest={
            "source_commit": "36742b13ce6935d3ba15e2454bb6e1b4bdaee202",
            "build_id": "different",
        },
    )
