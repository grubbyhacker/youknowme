from __future__ import annotations

import json
import subprocess
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import jwt


GITHUB_API_URL = "https://api.github.com"
INDEX_ARTIFACT_PREFIX = "youknowme-index-"


class GitHubArtifactError(RuntimeError):
    pass


@dataclass(frozen=True)
class ArtifactSelection:
    run: dict[str, Any]
    artifact: dict[str, Any]
    build_report: dict[str, Any] | None = None

    @property
    def artifact_id(self) -> int:
        return int(self.artifact["id"])

    @property
    def artifact_name(self) -> str:
        return str(self.artifact["name"])

    @property
    def run_id(self) -> int:
        return int(self.run["id"])

    @property
    def head_sha(self) -> str:
        return str(self.run["head_sha"])


class GitHubActionsClient:
    def __init__(
        self,
        *,
        token: str,
        api_url: str = GITHUB_API_URL,
        timeout: float = 30.0,
        client: httpx.Client | None = None,
    ) -> None:
        self._client = client or httpx.Client(timeout=timeout, follow_redirects=True)
        self._api_url = api_url.rstrip("/")
        self._headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def close(self) -> None:
        self._client.close()

    def list_successful_runs(
        self,
        *,
        repo: str,
        branch: str,
        event: str | None,
        workflow_name: str | None,
        per_page: int = 20,
    ) -> list[dict[str, Any]]:
        params: dict[str, str | int] = {
            "branch": branch,
            "status": "success",
            "per_page": per_page,
        }
        if event:
            params["event"] = event

        payload = self._get_json(f"/repos/{repo}/actions/runs", params=params)
        runs = payload.get("workflow_runs", [])
        if not isinstance(runs, list):
            raise GitHubArtifactError("GitHub workflow-runs response did not contain a list")

        selected = [
            run
            for run in runs
            if run.get("head_branch") == branch
            and run.get("status") == "completed"
            and run.get("conclusion") == "success"
            and (workflow_name is None or run.get("name") == workflow_name)
        ]
        return sorted(selected, key=lambda run: run.get("created_at", ""), reverse=True)

    def list_run_artifacts(self, *, repo: str, run_id: int) -> list[dict[str, Any]]:
        payload = self._get_json(f"/repos/{repo}/actions/runs/{run_id}/artifacts")
        artifacts = payload.get("artifacts", [])
        if not isinstance(artifacts, list):
            raise GitHubArtifactError("GitHub artifacts response did not contain a list")
        return sorted(artifacts, key=lambda artifact: artifact.get("created_at", ""), reverse=True)

    def download_artifact_zip(self, *, repo: str, artifact_id: int, out: Path) -> None:
        response = self._client.get(
            f"{self._api_url}/repos/{repo}/actions/artifacts/{artifact_id}/zip",
            headers=self._headers,
        )
        _raise_for_status(response)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(response.content)

    def _get_json(self, path: str, params: dict[str, str | int] | None = None) -> dict[str, Any]:
        response = self._client.get(f"{self._api_url}{path}", headers=self._headers, params=params)
        _raise_for_status(response)
        payload = response.json()
        if not isinstance(payload, dict):
            raise GitHubArtifactError("GitHub response was not a JSON object")
        return payload


def create_app_jwt(app_id: str, private_key_pem: str, *, now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    payload = {
        "iat": int((now - timedelta(seconds=60)).timestamp()),
        "exp": int((now + timedelta(minutes=9)).timestamp()),
        "iss": app_id,
    }
    return jwt.encode(payload, private_key_pem, algorithm="RS256")


def create_installation_token(
    *,
    app_id: str,
    installation_id: str,
    private_key_pem: str,
    api_url: str = GITHUB_API_URL,
    timeout: float = 30.0,
) -> str:
    app_jwt = create_app_jwt(app_id, private_key_pem)
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {app_jwt}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    with httpx.Client(timeout=timeout) as client:
        response = client.post(
            f"{api_url.rstrip('/')}/app/installations/{installation_id}/access_tokens",
            headers=headers,
        )
    _raise_for_status(response)
    payload = response.json()
    token = payload.get("token")
    if not isinstance(token, str) or not token:
        raise GitHubArtifactError("GitHub installation-token response did not include a token")
    return token


def select_latest_index_artifact(
    *,
    client: GitHubActionsClient,
    repo: str,
    branch: str = "main",
    event: str | None = "push",
    workflow_name: str | None = "Production index artifact",
    artifact_prefix: str = INDEX_ARTIFACT_PREFIX,
    limit: int = 20,
) -> ArtifactSelection:
    runs = client.list_successful_runs(
        repo=repo,
        branch=branch,
        event=event,
        workflow_name=workflow_name,
        per_page=limit,
    )
    for run in runs:
        artifacts = client.list_run_artifacts(repo=repo, run_id=int(run["id"]))
        for artifact in artifacts:
            if artifact.get("expired") is True:
                continue
            name = artifact.get("name")
            if isinstance(name, str) and name.startswith(artifact_prefix):
                return ArtifactSelection(run=run, artifact=artifact)

    raise GitHubArtifactError(
        f"no unexpired artifact with prefix {artifact_prefix!r} found for successful "
        f"{branch!r} workflow runs in {repo}"
    )


def read_build_report_from_artifact_zip(path: Path) -> dict[str, Any]:
    with zipfile.ZipFile(path) as archive:
        report_names = [
            info.filename
            for info in archive.infolist()
            if info.filename.endswith(".build-report.json") and "/" not in info.filename.strip("/")
        ]
        if len(report_names) != 1:
            raise GitHubArtifactError(
                "artifact ZIP must contain exactly one top-level .build-report.json"
            )
        with archive.open(report_names[0]) as handle:
            payload = json.loads(handle.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise GitHubArtifactError("build report was not a JSON object")
    return payload


def read_manifest(path: Path) -> dict[str, Any] | None:
    manifest_path = path / "manifest.json"
    if not manifest_path.exists():
        return None
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise GitHubArtifactError(f"manifest was not a JSON object: {manifest_path}")
    return payload


def artifact_matches_current_index(*, build_report: dict[str, Any], current_manifest: dict[str, Any] | None) -> bool:
    if current_manifest is None:
        return False

    artifact_manifest = build_report.get("manifest")
    if not isinstance(artifact_manifest, dict):
        return False

    return (
        artifact_manifest.get("source_commit") == current_manifest.get("source_commit")
        and artifact_manifest.get("build_id") == current_manifest.get("build_id")
    )


def promote_artifact(
    *,
    artifact_zip: Path,
    promote_script: Path,
    sudo: bool = False,
    extra_args: list[str] | None = None,
) -> None:
    command = [str(promote_script), "--artifact", str(artifact_zip)]
    if extra_args:
        command.extend(extra_args)
    if sudo:
        command.insert(0, "sudo")
    subprocess.run(command, check=True)


def _raise_for_status(response: httpx.Response) -> None:
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text[:500]
        raise GitHubArtifactError(
            f"GitHub API request failed: {exc.response.status_code} {detail}"
        ) from exc
