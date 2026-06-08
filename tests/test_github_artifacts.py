from __future__ import annotations

import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

import ykm.github_artifacts_cli as cli
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


def test_cli_returns_current_exit_code_without_path_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_github_download(
        tmp_path,
        monkeypatch,
        source_commit="same-source",
        build_id="same-build",
    )
    _write_current_manifest(tmp_path, source_commit="same-source", build_id="same-build")
    path_file = tmp_path / "watcher-state" / "artifact.path"

    result = cli.run(
        _args(
            tmp_path,
            artifact_path_file=path_file,
            exit_code_current=True,
        )
    )

    assert result == cli.CURRENT_EXIT_CODE
    assert not path_file.exists()


def test_cli_writes_path_file_for_new_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_github_download(
        tmp_path,
        monkeypatch,
        source_commit="new-source",
        build_id="new-build",
    )
    _write_current_manifest(tmp_path, source_commit="old-source", build_id="old-build")
    path_file = tmp_path / "watcher-state" / "artifact.path"

    result = cli.run(
        _args(
            tmp_path,
            artifact_path_file=path_file,
            exit_code_current=True,
        )
    )

    assert result == 0
    assert path_file.read_text(encoding="utf-8").strip() == str(
        tmp_path / "incoming" / "youknowme-index-new-source.zip"
    )


def test_cli_skips_download_when_workflow_head_matches_current_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_github_download(
        tmp_path,
        monkeypatch,
        source_commit="same-source",
        build_id="different-build-that-should-not-matter-for-metadata-skip",
        fail_on_download=True,
    )
    _write_current_manifest(tmp_path, source_commit="same-source", build_id="same-build")

    result = cli.run(_args(tmp_path, exit_code_current=True))

    assert result == cli.CURRENT_EXIT_CODE
    assert not (tmp_path / "incoming" / "youknowme-index-same-source.zip").exists()


def test_cli_force_downloads_even_when_workflow_head_matches_current_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_github_download(
        tmp_path,
        monkeypatch,
        source_commit="same-source",
        build_id="same-build",
    )
    _write_current_manifest(tmp_path, source_commit="same-source", build_id="same-build")

    result = cli.run(_args(tmp_path, exit_code_current=True, force=True))

    assert result == 0
    assert (tmp_path / "incoming" / "youknowme-index-same-source.zip").exists()


def _args(tmp_path: Path, **overrides: object) -> SimpleNamespace:
    private_key = tmp_path / "private-key.pem"
    private_key.write_text("fake-key", encoding="utf-8")
    values = {
        "repo": "grubbyhacker/ykmcorpus",
        "branch": "main",
        "event": "push",
        "workflow_name": "Production index artifact",
        "artifact_prefix": "youknowme-index-",
        "app_id": "4001682",
        "installation_id": "138954168",
        "private_key": str(private_key),
        "api_url": "https://api.github.test",
        "out_dir": tmp_path / "incoming",
        "deploy_root": tmp_path,
        "artifact_path_file": None,
        "exit_code_current": False,
        "promote_script": tmp_path / "promote.sh",
        "promote": False,
        "sudo": False,
        "promote_arg": [],
        "force": False,
        "dry_run": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _write_current_manifest(tmp_path: Path, *, source_commit: str, build_id: str) -> None:
    index_current = tmp_path / "index-current"
    index_current.mkdir()
    (index_current / "manifest.json").write_text(
        json.dumps({"source_commit": source_commit, "build_id": build_id}),
        encoding="utf-8",
    )


def _install_fake_github_download(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    source_commit: str,
    build_id: str,
    fail_on_download: bool = False,
) -> None:
    class FakeSelection:
        artifact_id = 200
        artifact_name = f"youknowme-index-{source_commit}"
        run_id = 20
        head_sha = source_commit

    class FakeClient:
        def __init__(self, *, token: str, api_url: str) -> None:
            self.token = token
            self.api_url = api_url

        def close(self) -> None:
            return None

        def download_artifact_zip(self, *, repo: str, artifact_id: int, out: Path) -> None:
            if fail_on_download:
                raise AssertionError("download should have been skipped")
            out.parent.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(out, "w") as archive:
                archive.writestr(
                    "youknowme-index.build-report.json",
                    json.dumps(
                        {
                            "manifest": {
                                "source_commit": source_commit,
                                "build_id": build_id,
                            }
                        }
                    ),
                )
                archive.writestr("youknowme-index.tar.gz", b"fake")
                archive.writestr("youknowme-index.sha256", "fake  youknowme-index.tar.gz\n")

    monkeypatch.setattr(cli, "create_installation_token", lambda **_: "token")
    monkeypatch.setattr(cli, "GitHubActionsClient", FakeClient)
    monkeypatch.setattr(cli, "select_latest_index_artifact", lambda **_: FakeSelection())
