from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Protocol, TypeVar
from urllib.parse import quote

import httpx
from pydantic import BaseModel

from curator.model_tasks import validate_model_response_output
from curator.models import (
    BrokerReadRequest,
    BrokerFixtureState,
    CuratorIssueSnapshot,
    CuratorProbe,
    CuratorPrReviewCommentSnapshot,
    CuratorPrReviewSnapshot,
    CuratorPrReviewThreadSnapshot,
    CuratorPrSnapshot,
    ExecutionIntent,
    ExecutionResult,
    ModelCallBudget,
    ModelCallRequest,
    ModelCallResponse,
    ModelProxyFixtureState,
    PolicyDecision,
    ProposedAction,
    UploadReviewPreview,
)


ModelOutputT = TypeVar("ModelOutputT", bound=BaseModel)


class BrokerAdapter(Protocol):
    def preflight_action(self, action: ProposedAction) -> PolicyDecision:
        """Return the broker-side policy decision for a proposed action."""


class ModelAdapter(Protocol):
    def available(self) -> bool:
        """Return whether the broker/proxy model boundary is reachable."""


class FixtureBrokerAdapter:
    def __init__(self, state: BrokerFixtureState) -> None:
        self.state = state

    @classmethod
    def from_path(cls, path: Path) -> FixtureBrokerAdapter:
        return cls(BrokerFixtureState.model_validate_json(path.read_text(encoding="utf-8")))

    def probe(self, *, required: bool) -> CuratorProbe:
        if not self.state.reachable:
            return CuratorProbe(
                name="broker",
                status="fail" if required else "skip",
                message="broker fixture is unreachable",
            )
        return CuratorProbe(name="broker", status="pass", message="broker fixture is reachable")

    def pr_snapshots(self) -> list[CuratorPrSnapshot]:
        return self.state.pr_snapshots

    def issue_snapshots(self) -> list[CuratorIssueSnapshot]:
        return self.state.issue_snapshots

    def preflight_intents(self, intents: list[ExecutionIntent]) -> list[CuratorProbe]:
        probes: list[CuratorProbe] = []
        existing_branches = set(self.state.existing_branches)
        existing_idempotency_keys = set(self.state.existing_idempotency_keys)
        allowed_operations = set(self.state.allowed_operations)
        for intent in intents:
            if intent.idempotency_key in existing_idempotency_keys:
                probes.append(
                    CuratorProbe(
                        name="broker-preflight",
                        status="fail",
                        message="idempotency key already exists in broker fixture",
                        details={
                            "action_id": intent.action_id,
                            "idempotency_key": intent.idempotency_key,
                        },
                    )
                )
            if intent.operation not in allowed_operations:
                probes.append(
                    CuratorProbe(
                        name="broker-preflight",
                        status="fail",
                        message=f"operation is not broker-fixture allowlisted: {intent.operation}",
                        details={"action_id": intent.action_id, "operation": intent.operation},
                    )
                )
            if intent.branch and intent.branch in existing_branches:
                probes.append(
                    CuratorProbe(
                        name="broker-preflight",
                        status="fail",
                        message="branch already exists in broker fixture",
                        details={"action_id": intent.action_id, "branch": intent.branch},
                    )
                )
        if not probes and intents:
            probes.append(
                CuratorProbe(
                    name="broker-preflight",
                    status="pass",
                    message="broker fixture accepted execution intents",
                    details={"intent_count": len(intents)},
                )
            )
        return probes

    def preflight_upload_review_previews(
        self,
        previews: list[UploadReviewPreview],
    ) -> list[CuratorProbe]:
        probes: list[CuratorProbe] = []
        existing_branches = set(self.state.existing_branches)
        existing_idempotency_keys = set(self.state.existing_idempotency_keys)
        for preview in previews:
            if preview.idempotency_key in existing_idempotency_keys:
                probes.append(
                    CuratorProbe(
                        name="broker-upload-preflight",
                        status="fail",
                        message="upload review idempotency key already exists in broker fixture",
                        details={
                            "action_id": preview.action_id,
                            "upload_id": preview.upload_id,
                            "idempotency_key": preview.idempotency_key,
                        },
                    )
                )
            if preview.branch in existing_branches:
                probes.append(
                    CuratorProbe(
                        name="broker-upload-preflight",
                        status="fail",
                        message="upload review branch already exists in broker fixture",
                        details={
                            "action_id": preview.action_id,
                            "upload_id": preview.upload_id,
                            "branch": preview.branch,
                        },
                    )
                )
        if not probes and previews:
            probes.append(
                CuratorProbe(
                    name="broker-upload-preflight",
                    status="pass",
                    message="broker fixture accepted upload review previews",
                    details={"preview_count": len(previews)},
                )
            )
        return probes

    def create_pull(self, intent: ExecutionIntent) -> ExecutionResult:
        return ExecutionResult(
            action_id=intent.action_id,
            operation=intent.operation,
            idempotency_key=intent.idempotency_key,
            status="simulated",
            target_repo=intent.target_repo,
            branch=intent.branch,
        )

    def add_issue_comment(
        self,
        *,
        target_repo: str,
        issue_number: int,
        body: str,
        action_id: str,
        idempotency_key: str,
        metadata: dict[str, str] | None = None,
    ) -> ExecutionResult:
        return ExecutionResult(
            action_id=action_id,
            operation="issue.comment",
            idempotency_key=idempotency_key,
            status="simulated",
            target_repo=target_repo,
            pr_number=issue_number,
            message="broker fixture simulated issue.comment",
        )

    def dismiss_pull_review(
        self,
        *,
        target_repo: str,
        pr_number: int,
        review_id: str,
        message: str,
        action_id: str,
        idempotency_key: str,
    ) -> ExecutionResult:
        return ExecutionResult(
            action_id=action_id,
            operation="pull.review.dismiss",
            idempotency_key=idempotency_key,
            status="simulated",
            target_repo=target_repo,
            pr_number=pr_number,
            message="broker fixture simulated pull.review.dismiss",
        )

    def resolve_review_thread(
        self,
        *,
        target_repo: str,
        pr_number: int,
        thread_id: str,
        message: str,
        action_id: str,
        idempotency_key: str,
    ) -> ExecutionResult:
        return ExecutionResult(
            action_id=action_id,
            operation="pull.review_thread.resolve",
            idempotency_key=idempotency_key,
            status="simulated",
            target_repo=target_repo,
            pr_number=pr_number,
            message="broker fixture simulated pull.review_thread.resolve",
        )

    def add_issue_label(
        self,
        *,
        target_repo: str,
        issue_number: int,
        label: str,
        action_id: str,
        idempotency_key: str,
    ) -> ExecutionResult:
        return ExecutionResult(
            action_id=action_id,
            operation="issue.label.add",
            idempotency_key=idempotency_key,
            status="simulated",
            target_repo=target_repo,
            pr_number=issue_number,
            message="broker fixture simulated issue.label.add",
        )

    def remove_issue_label(
        self,
        *,
        target_repo: str,
        issue_number: int,
        label: str,
        action_id: str,
        idempotency_key: str,
    ) -> ExecutionResult:
        return ExecutionResult(
            action_id=action_id,
            operation="issue.label.remove",
            idempotency_key=idempotency_key,
            status="simulated",
            target_repo=target_repo,
            pr_number=issue_number,
            message="broker fixture simulated issue.label.remove",
        )

    def simulate_intents(self, intents: list[ExecutionIntent]) -> list[ExecutionResult]:
        return [
            ExecutionResult(
                action_id=intent.action_id,
                operation=intent.operation,
                idempotency_key=intent.idempotency_key,
                status="simulated",
                target_repo=intent.target_repo,
                branch=intent.branch,
            )
            for intent in intents
        ]


class HttpBrokerAdapter:
    def __init__(
        self,
        base_url: str,
        *,
        client: httpx.Client | None = None,
        timeout_seconds: float = 5,
        agent_id: str | None = None,
        agent_secret: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = client
        self.timeout_seconds = timeout_seconds
        self.agent_id = agent_id if agent_id is not None else os.getenv("BROKER_AGENT_ID")
        self.agent_secret = (
            agent_secret if agent_secret is not None else os.getenv("BROKER_AGENT_SECRET")
        )

    def probe(self, *, required: bool) -> CuratorProbe:
        if not self.base_url:
            return CuratorProbe(
                name="broker",
                status="fail" if required else "skip",
                message="broker URL not configured",
            )
        try:
            response = self._get("/healthz")
        except httpx.HTTPError as exc:
            return CuratorProbe(name="broker", status="fail", message=f"broker unreachable: {exc}")
        ok = response.status_code < 500
        return CuratorProbe(
            name="broker",
            status="pass" if ok else "fail",
            message=f"broker health responded with HTTP {response.status_code}",
        )

    def readonly_preflight_requests(
        self, intents: list[ExecutionIntent]
    ) -> list[BrokerReadRequest]:
        requests: list[BrokerReadRequest] = []
        for intent in intents:
            owner, repo = _split_repo(intent.target_repo)
            if intent.operation == "pull.create" and intent.branch:
                requests.append(
                    BrokerReadRequest(
                        operation="pull.list",
                        path=f"/repos/{owner}/{repo}/pulls",
                        target_repo=intent.target_repo,
                        idempotency_key=intent.idempotency_key,
                        params={
                            "state": "all",
                            "head": f"{owner}:{intent.branch}",
                        },
                        purpose="check for existing pull requests that already use the proposed branch",
                    )
                )
            requests.append(
                BrokerReadRequest(
                    operation="issue.search",
                    path=f"/repos/{owner}/{repo}/issues",
                    target_repo=intent.target_repo,
                    idempotency_key=intent.idempotency_key,
                    params={
                        "state": "all",
                        "q": intent.idempotency_key,
                    },
                    purpose="check for existing issue or pull request idempotency markers",
                )
            )
        return requests

    def pr_reconciliation_read_requests(
        self,
        *,
        target_repo: str,
        snapshots: list[CuratorPrSnapshot] | None = None,
    ) -> list[BrokerReadRequest]:
        owner, repo = _split_repo(target_repo)
        requests = [
            BrokerReadRequest(
                operation="pull.list",
                path=f"/repos/{owner}/{repo}/pulls",
                target_repo=target_repo,
                params={
                    "state": "all",
                    "head_prefix": f"{owner}:curator/",
                    "base": "main",
                },
                purpose="discover Curator pull requests by branch prefix",
            ),
            BrokerReadRequest(
                operation="issue.search",
                path=f"/repos/{owner}/{repo}/issues",
                target_repo=target_repo,
                params={
                    "state": "all",
                    "q": "YKM-Curator-Run type:pr",
                },
                purpose="discover Curator pull requests by durable body markers",
            ),
        ]
        for snapshot in snapshots or []:
            requests.extend(self._pr_detail_read_requests(target_repo, owner, repo, snapshot.number))
        return requests

    def pr_reconciliation_preflight(
        self,
        *,
        target_repo: str,
        snapshots: list[CuratorPrSnapshot] | None = None,
    ) -> CuratorProbe:
        requests = self.pr_reconciliation_read_requests(
            target_repo=target_repo,
            snapshots=snapshots,
        )
        return CuratorProbe(
            name="broker-pr-read-preflight",
            status="skip",
            message="HTTP broker PR reconciliation read descriptors generated; live read execution is not enabled",
            details={"requests": [request.model_dump(mode="json") for request in requests]},
        )

    def upload_review_read_requests(
        self,
        *,
        target_repo: str,
        previews: list[UploadReviewPreview],
    ) -> list[BrokerReadRequest]:
        owner, repo = _split_repo(target_repo)
        requests: list[BrokerReadRequest] = []
        for preview in previews:
            requests.extend(
                [
                    BrokerReadRequest(
                        operation="pull.list",
                        path=f"/repos/{owner}/{repo}/pulls",
                        target_repo=target_repo,
                        idempotency_key=preview.idempotency_key,
                        params={
                            "state": "all",
                            "head": f"{owner}:{preview.branch}",
                        },
                        purpose="check for existing pull requests that already use the proposed upload branch",
                    ),
                    BrokerReadRequest(
                        operation="issue.search",
                        path=f"/repos/{owner}/{repo}/issues",
                        target_repo=target_repo,
                        idempotency_key=preview.idempotency_key,
                        params={
                            "state": "all",
                            "q": preview.idempotency_key,
                        },
                        purpose="check for existing upload review issue or pull request idempotency markers",
                    ),
                ]
            )
        return requests

    def upload_review_preflight(
        self,
        *,
        target_repo: str,
        previews: list[UploadReviewPreview],
    ) -> CuratorProbe | None:
        requests = self.upload_review_read_requests(target_repo=target_repo, previews=previews)
        if not requests:
            return None
        return CuratorProbe(
            name="broker-upload-read-preflight",
            status="skip",
            message="HTTP broker upload review read descriptors generated; live read execution is not enabled",
            details={"requests": [request.model_dump(mode="json") for request in requests]},
        )

    def issue_reconciliation_read_requests(
        self,
        *,
        target_repo: str,
        issue_numbers: list[int],
    ) -> list[BrokerReadRequest]:
        owner, repo = _split_repo(target_repo)
        requests: list[BrokerReadRequest] = []
        for issue_number in sorted(set(issue_numbers)):
            requests.extend(
                [
                    BrokerReadRequest(
                        operation="issue.read",
                        path=f"/repos/{owner}/{repo}/issues/{issue_number}",
                        target_repo=target_repo,
                        purpose="read blocking or follow-up issue state for Curator reconciliation",
                    ),
                    BrokerReadRequest(
                        operation="issue.comments",
                        path=f"/repos/{owner}/{repo}/issues/{issue_number}/comments",
                        target_repo=target_repo,
                        purpose="read blocking or follow-up issue comments for Curator reconciliation",
                    ),
                ]
            )
        return requests

    def issue_reconciliation_preflight(
        self,
        *,
        target_repo: str,
        issue_numbers: list[int],
    ) -> CuratorProbe | None:
        requests = self.issue_reconciliation_read_requests(
            target_repo=target_repo,
            issue_numbers=issue_numbers,
        )
        if not requests:
            return None
        return CuratorProbe(
            name="broker-issue-read-preflight",
            status="skip",
            message="HTTP broker issue reconciliation read descriptors generated; live read execution is not enabled",
            details={"requests": [request.model_dump(mode="json") for request in requests]},
        )

    def read_pr_snapshots(self, *, target_repo: str) -> tuple[list[CuratorPrSnapshot], CuratorProbe]:
        try:
            raw_pulls = self._read_curator_pulls(target_repo)
            snapshots = [self._snapshot_from_pull(target_repo, pull) for pull in raw_pulls]
        except (httpx.HTTPError, ValueError) as exc:
            return [], CuratorProbe(
                name="broker-pr-read",
                status="fail",
                message=f"broker PR read failed: {exc}",
            )
        return snapshots, CuratorProbe(
            name="broker-pr-read",
            status="pass",
            message="broker PR snapshots loaded",
            details={"count": len(snapshots)},
        )

    def read_issue_snapshots(
        self,
        *,
        target_repo: str,
        issue_numbers: list[int],
    ) -> tuple[list[CuratorIssueSnapshot], CuratorProbe | None]:
        unique_numbers = sorted(set(issue_numbers))
        if not unique_numbers:
            return [], None
        snapshots: list[CuratorIssueSnapshot] = []
        try:
            for issue_number in unique_numbers:
                issue = self._get_json(
                    f"/v1/repos/{target_repo}/issues/{issue_number}",
                    authenticated=True,
                )
                if not isinstance(issue, dict):
                    raise ValueError(f"issue read returned non-object for #{issue_number}")
                snapshots.append(_issue_snapshot_from_raw(issue))
        except (httpx.HTTPError, ValueError) as exc:
            return [], CuratorProbe(
                name="broker-issue-read",
                status="fail",
                message=f"broker issue read failed: {exc}",
            )
        return snapshots, CuratorProbe(
            name="broker-issue-read",
            status="pass",
            message="broker issue snapshots loaded",
            details={"count": len(snapshots)},
        )

    def preflight_intents(self, intents: list[ExecutionIntent]) -> list[CuratorProbe]:
        requests = self.readonly_preflight_requests(intents)
        if not requests:
            return []
        return [
            CuratorProbe(
                name="broker-preflight",
                status="skip",
                message="HTTP broker read preflight request descriptors generated; live read execution is not enabled",
                details={"requests": [request.model_dump(mode="json") for request in requests]},
            )
        ]

    def create_pull(self, intent: ExecutionIntent) -> ExecutionResult:
        if intent.operation != "pull.create":
            return ExecutionResult(
                action_id=intent.action_id,
                operation=intent.operation,
                idempotency_key=intent.idempotency_key,
                status="failed",
                target_repo=intent.target_repo,
                branch=intent.branch,
                message=f"unsupported broker operation for create_pull: {intent.operation}",
            )
        if not intent.branch:
            return ExecutionResult(
                action_id=intent.action_id,
                operation=intent.operation,
                idempotency_key=intent.idempotency_key,
                status="failed",
                target_repo=intent.target_repo,
                message="pull.create intent requires a branch",
            )
        try:
            response = self._request(
                "POST",
                f"/v1/repos/{intent.target_repo}/pulls",
                authenticated=True,
                idempotency_key=intent.idempotency_key,
                json_body={
                    "title": intent.title or "YouKnowMe Curator upload review",
                    "head": intent.branch,
                    "base": "main",
                    "body": intent.body or "",
                    "draft": False,
                    "metadata": _curator_metadata(intent),
                    "permissions": ["contents:write", "pull_requests:write"],
                },
            )
            if response.status_code >= 400:
                return ExecutionResult(
                    action_id=intent.action_id,
                    operation=intent.operation,
                    idempotency_key=intent.idempotency_key,
                    status="failed",
                    target_repo=intent.target_repo,
                    branch=intent.branch,
                    message=(
                        "broker pull.create failed with HTTP "
                        f"{response.status_code}: {response.text[:500]}"
                    ),
                )
            raw = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            return ExecutionResult(
                action_id=intent.action_id,
                operation=intent.operation,
                idempotency_key=intent.idempotency_key,
                status="failed",
                target_repo=intent.target_repo,
                branch=intent.branch,
                message=f"broker pull.create failed: {exc}",
            )
        return ExecutionResult(
            action_id=intent.action_id,
            operation=intent.operation,
            idempotency_key=intent.idempotency_key,
            status="executed",
            target_repo=intent.target_repo,
            branch=intent.branch,
            pr_number=_int_value(raw.get("number")) if isinstance(raw, dict) else None,
            url=_str_value(raw.get("html_url")) if isinstance(raw, dict) else None,
            message="broker pull.create succeeded",
        )

    def add_issue_comment(
        self,
        *,
        target_repo: str,
        issue_number: int,
        body: str,
        action_id: str,
        idempotency_key: str,
        metadata: dict[str, str] | None = None,
    ) -> ExecutionResult:
        json_body: dict[str, Any] = {"body": body}
        if metadata:
            json_body["metadata"] = metadata
        try:
            response = self._request(
                "POST",
                f"/v1/repos/{target_repo}/issues/{issue_number}/comments",
                authenticated=True,
                idempotency_key=idempotency_key,
                json_body=json_body,
            )
            if response.status_code >= 400:
                return ExecutionResult(
                    action_id=action_id,
                    operation="issue.comment",
                    idempotency_key=idempotency_key,
                    status="failed",
                    target_repo=target_repo,
                    pr_number=issue_number,
                    message=(
                        "broker issue.comment failed with HTTP "
                        f"{response.status_code}: {response.text[:500]}"
                    ),
                )
            raw = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            return ExecutionResult(
                action_id=action_id,
                operation="issue.comment",
                idempotency_key=idempotency_key,
                status="failed",
                target_repo=target_repo,
                pr_number=issue_number,
                message=f"broker issue.comment failed: {exc}",
            )
        return ExecutionResult(
            action_id=action_id,
            operation="issue.comment",
            idempotency_key=idempotency_key,
            status="executed",
            target_repo=target_repo,
            pr_number=issue_number,
            url=_str_value(raw.get("html_url")) if isinstance(raw, dict) else None,
            message="broker issue.comment succeeded",
        )

    def dismiss_pull_review(
        self,
        *,
        target_repo: str,
        pr_number: int,
        review_id: str,
        message: str,
        action_id: str,
        idempotency_key: str,
    ) -> ExecutionResult:
        try:
            response = self._request(
                "PUT",
                f"/v1/repos/{target_repo}/pulls/{pr_number}/reviews/{review_id}/dismissal",
                authenticated=True,
                idempotency_key=idempotency_key,
                json_body={"message": message},
            )
            if response.status_code >= 400:
                return ExecutionResult(
                    action_id=action_id,
                    operation="pull.review.dismiss",
                    idempotency_key=idempotency_key,
                    status="failed",
                    target_repo=target_repo,
                    pr_number=pr_number,
                    message=(
                        "broker pull.review.dismiss failed with HTTP "
                        f"{response.status_code}: {response.text[:500]}"
                    ),
                )
            raw = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            return ExecutionResult(
                action_id=action_id,
                operation="pull.review.dismiss",
                idempotency_key=idempotency_key,
                status="failed",
                target_repo=target_repo,
                pr_number=pr_number,
                message=f"broker pull.review.dismiss failed: {exc}",
            )
        return ExecutionResult(
            action_id=action_id,
            operation="pull.review.dismiss",
            idempotency_key=idempotency_key,
            status="executed",
            target_repo=target_repo,
            pr_number=pr_number,
            url=_str_value(raw.get("html_url")) if isinstance(raw, dict) else None,
            message="broker pull.review.dismiss succeeded",
        )

    def resolve_review_thread(
        self,
        *,
        target_repo: str,
        pr_number: int,
        thread_id: str,
        message: str,
        action_id: str,
        idempotency_key: str,
    ) -> ExecutionResult:
        try:
            response = self._request(
                "PUT",
                f"/v1/repos/{target_repo}/pulls/{pr_number}/review-threads/{thread_id}/resolve",
                authenticated=True,
                idempotency_key=idempotency_key,
                json_body={"message": message},
            )
            if response.status_code >= 400:
                return ExecutionResult(
                    action_id=action_id,
                    operation="pull.review_thread.resolve",
                    idempotency_key=idempotency_key,
                    status="failed",
                    target_repo=target_repo,
                    pr_number=pr_number,
                    message=(
                        "broker pull.review_thread.resolve failed with HTTP "
                        f"{response.status_code}: {response.text[:500]}"
                    ),
                )
            raw = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            return ExecutionResult(
                action_id=action_id,
                operation="pull.review_thread.resolve",
                idempotency_key=idempotency_key,
                status="failed",
                target_repo=target_repo,
                pr_number=pr_number,
                message=f"broker pull.review_thread.resolve failed: {exc}",
            )
        return ExecutionResult(
            action_id=action_id,
            operation="pull.review_thread.resolve",
            idempotency_key=idempotency_key,
            status="executed",
            target_repo=target_repo,
            pr_number=pr_number,
            url=_str_value(raw.get("html_url")) if isinstance(raw, dict) else None,
            message="broker pull.review_thread.resolve succeeded",
        )

    def add_issue_label(
        self,
        *,
        target_repo: str,
        issue_number: int,
        label: str,
        action_id: str,
        idempotency_key: str,
    ) -> ExecutionResult:
        try:
            response = self._request(
                "POST",
                f"/v1/repos/{target_repo}/issues/{issue_number}/labels",
                authenticated=True,
                idempotency_key=idempotency_key,
                json_body={"labels": [label]},
            )
            if response.status_code >= 400:
                return ExecutionResult(
                    action_id=action_id,
                    operation="issue.label.add",
                    idempotency_key=idempotency_key,
                    status="failed",
                    target_repo=target_repo,
                    pr_number=issue_number,
                    message=(
                        "broker issue.label.add failed with HTTP "
                        f"{response.status_code}: {response.text[:500]}"
                    ),
                )
            raw = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            return ExecutionResult(
                action_id=action_id,
                operation="issue.label.add",
                idempotency_key=idempotency_key,
                status="failed",
                target_repo=target_repo,
                pr_number=issue_number,
                message=f"broker issue.label.add failed: {exc}",
            )
        return ExecutionResult(
            action_id=action_id,
            operation="issue.label.add",
            idempotency_key=idempotency_key,
            status="executed",
            target_repo=target_repo,
            pr_number=issue_number,
            url=_str_value(raw.get("html_url")) if isinstance(raw, dict) else None,
            message="broker issue.label.add succeeded",
        )

    def remove_issue_label(
        self,
        *,
        target_repo: str,
        issue_number: int,
        label: str,
        action_id: str,
        idempotency_key: str,
    ) -> ExecutionResult:
        try:
            response = self._request(
                "DELETE",
                f"/v1/repos/{target_repo}/issues/{issue_number}/labels/{quote(label, safe='')}",
                authenticated=True,
                idempotency_key=idempotency_key,
            )
            if response.status_code >= 400:
                return ExecutionResult(
                    action_id=action_id,
                    operation="issue.label.remove",
                    idempotency_key=idempotency_key,
                    status="failed",
                    target_repo=target_repo,
                    pr_number=issue_number,
                    message=(
                        "broker issue.label.remove failed with HTTP "
                        f"{response.status_code}: {response.text[:500]}"
                    ),
                )
            raw = response.json() if response.content else {}
        except (httpx.HTTPError, ValueError) as exc:
            return ExecutionResult(
                action_id=action_id,
                operation="issue.label.remove",
                idempotency_key=idempotency_key,
                status="failed",
                target_repo=target_repo,
                pr_number=issue_number,
                message=f"broker issue.label.remove failed: {exc}",
            )
        return ExecutionResult(
            action_id=action_id,
            operation="issue.label.remove",
            idempotency_key=idempotency_key,
            status="executed",
            target_repo=target_repo,
            pr_number=issue_number,
            url=_str_value(raw.get("html_url")) if isinstance(raw, dict) else None,
            message="broker issue.label.remove succeeded",
        )

    def _pr_detail_read_requests(
        self,
        target_repo: str,
        owner: str,
        repo: str,
        pr_number: int,
    ) -> list[BrokerReadRequest]:
        return [
            BrokerReadRequest(
                operation="pull.read",
                path=f"/repos/{owner}/{repo}/pulls/{pr_number}",
                target_repo=target_repo,
                purpose="read pull request state and branch metadata",
            ),
            BrokerReadRequest(
                operation="pull.comments",
                path=f"/repos/{owner}/{repo}/issues/{pr_number}/comments",
                target_repo=target_repo,
                purpose="read issue comments on Curator pull request",
            ),
            BrokerReadRequest(
                operation="pull.reviews",
                path=f"/repos/{owner}/{repo}/pulls/{pr_number}/reviews",
                target_repo=target_repo,
                purpose="read review submissions on Curator pull request",
            ),
            BrokerReadRequest(
                operation="pull.review_comments",
                path=f"/repos/{owner}/{repo}/pulls/{pr_number}/comments",
                target_repo=target_repo,
                purpose="read inline review comments on Curator pull request",
            ),
            BrokerReadRequest(
                operation="pull.review_threads",
                path=f"/repos/{owner}/{repo}/pulls/{pr_number}/review-threads",
                target_repo=target_repo,
                purpose="read unresolved review threads on Curator pull request",
            ),
            BrokerReadRequest(
                operation="commit.status",
                path=f"/repos/{owner}/{repo}/pulls/{pr_number}/status",
                target_repo=target_repo,
                purpose="read combined status for Curator pull request head",
            ),
            BrokerReadRequest(
                operation="check_runs",
                path=f"/repos/{owner}/{repo}/pulls/{pr_number}/check-runs",
                target_repo=target_repo,
                purpose="read check runs for Curator pull request head",
            ),
        ]

    def _get(self, path: str) -> httpx.Response:
        url = f"{self.base_url}{path}"
        if self._client is not None:
            return self._client.get(url, timeout=self.timeout_seconds)
        return httpx.get(url, timeout=self.timeout_seconds)

    def _get_json(
        self,
        path: str,
        *,
        authenticated: bool,
        params: dict[str, str] | None = None,
    ) -> Any:
        response = self._request("GET", path, authenticated=authenticated, params=params)
        if response.status_code >= 400:
            raise ValueError(f"broker read failed with HTTP {response.status_code}")
        return response.json()

    def _request(
        self,
        method: str,
        path: str,
        *,
        authenticated: bool,
        params: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> httpx.Response:
        url = f"{self.base_url}{path}"
        kwargs: dict[str, Any] = {"timeout": self.timeout_seconds}
        headers: dict[str, str] = {}
        if params:
            kwargs["params"] = params
        if json_body is not None:
            kwargs["json"] = json_body
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        if headers:
            kwargs["headers"] = headers
        if authenticated:
            if not self.agent_id or not self.agent_secret:
                raise ValueError("broker agent credentials are required for broker reads")
            kwargs["auth"] = (self.agent_id, self.agent_secret)
        if self._client is not None:
            return self._client.request(method, url, **kwargs)
        return httpx.request(method, url, **kwargs)

    def _read_curator_pulls(self, target_repo: str) -> list[dict[str, Any]]:
        pulls_by_number: dict[int, dict[str, Any]] = {}
        for params in (
            {"state": "all", "body_marker": "YKM-Curator-Run"},
            {"state": "all", "head_prefix": "curator/"},
        ):
            payload = self._get_json(
                f"/v1/repos/{target_repo}/pulls",
                authenticated=True,
                params=params,
            )
            if not isinstance(payload, list):
                raise ValueError("broker pull list returned non-list payload")
            for raw_pull in payload:
                if not isinstance(raw_pull, dict):
                    continue
                number = _int_value(raw_pull.get("number"))
                if number is None:
                    continue
                pulls_by_number[number] = raw_pull
        return [pulls_by_number[number] for number in sorted(pulls_by_number)]

    def _snapshot_from_pull(self, target_repo: str, pull: dict[str, Any]) -> CuratorPrSnapshot:
        number = _required_int(pull, "number")
        branch = _str_value(pull.get("head_ref")) or _nested_str(pull, "head", "ref")
        head_sha = _str_value(pull.get("head_sha")) or _nested_str(pull, "head", "sha")
        merged = bool(pull.get("merged"))
        state = "merged" if merged else _pr_state_value(_str_value(pull.get("state")))
        reviews = self._reviews(target_repo, number)
        review_threads = self._review_threads(target_repo, number)
        review_decision = _review_decision(reviews)
        review_comments = _review_comments(reviews)
        checks_conclusion = self._checks_conclusion(target_repo, head_sha)
        return CuratorPrSnapshot(
            number=number,
            state=state,
            title=_str_value(pull.get("title")),
            body=_str_value(pull.get("body")) or "",
            branch=branch,
            labels=_label_names(pull.get("labels")),
            review_comments=review_comments,
            reviews=_review_snapshots(reviews),
            review_threads=review_threads,
            checks_conclusion=checks_conclusion,
            unresolved_thread_count=sum(1 for thread in review_threads if not thread.is_resolved),
            review_decision=review_decision,
        )

    def _reviews(self, target_repo: str, pr_number: int) -> list[dict[str, Any]]:
        payload = self._get_json(
            f"/v1/repos/{target_repo}/pulls/{pr_number}/reviews",
            authenticated=True,
        )
        if not isinstance(payload, list):
            raise ValueError(f"broker reviews for PR #{pr_number} returned non-list payload")
        return [item for item in payload if isinstance(item, dict)]

    def _unresolved_thread_count(self, target_repo: str, pr_number: int) -> int:
        return sum(
            1 for thread in self._review_threads(target_repo, pr_number) if not thread.is_resolved
        )

    def _review_threads(
        self,
        target_repo: str,
        pr_number: int,
    ) -> list[CuratorPrReviewThreadSnapshot]:
        payload = self._get_json(
            f"/v1/repos/{target_repo}/pulls/{pr_number}/review-threads",
            authenticated=True,
        )
        if not isinstance(payload, list):
            raise ValueError(f"broker review threads for PR #{pr_number} returned non-list payload")
        return [_review_thread_snapshot(item) for item in payload if isinstance(item, dict)]

    def _checks_conclusion(self, target_repo: str, head_sha: str | None) -> str:
        if not head_sha:
            return "unknown"
        status_payload = self._get_json(
            f"/v1/repos/{target_repo}/commits/{head_sha}/status",
            authenticated=True,
        )
        check_payload = self._get_json(
            f"/v1/repos/{target_repo}/commits/{head_sha}/check-runs",
            authenticated=True,
        )
        status_state = (
            _str_value(status_payload.get("state")) if isinstance(status_payload, dict) else None
        )
        status_entries = (
            status_payload.get("statuses") if isinstance(status_payload, dict) else None
        )
        check_runs = check_payload.get("check_runs") if isinstance(check_payload, dict) else None
        missing_statuses = isinstance(status_entries, list) and not status_entries
        missing_check_runs = isinstance(check_runs, list) and not check_runs
        if missing_statuses and missing_check_runs:
            return "missing"
        if status_state in {"failure", "error"}:
            return "failure"
        if isinstance(check_runs, list):
            conclusions = [
                _str_value(run.get("conclusion"))
                for run in check_runs
                if isinstance(run, dict)
            ]
            if any(
                value in {"failure", "timed_out", "cancelled", "action_required"}
                for value in conclusions
            ):
                return "failure"
            if conclusions and all(value == "success" for value in conclusions):
                return "success"
        if status_state == "success":
            return "success"
        if status_state in {"pending", "expected"}:
            return "pending"
        if missing_check_runs:
            return "missing"
        return "unknown"


class FixtureModelAdapter:
    def __init__(self, state: ModelProxyFixtureState) -> None:
        self.state = state

    @classmethod
    def from_path(cls, path: Path) -> FixtureModelAdapter:
        return cls(ModelProxyFixtureState.model_validate_json(path.read_text(encoding="utf-8")))

    def probe(self, *, required: bool) -> CuratorProbe:
        if not self.state.reachable:
            return CuratorProbe(
                name="model-proxy",
                status="fail" if required else "skip",
                message="model proxy fixture is unreachable",
            )
        return CuratorProbe(
            name="model-proxy",
            status="pass",
            message="model proxy fixture is reachable",
            details={
                "max_calls_per_run": self.state.max_calls_per_run,
                "max_tokens_per_run": self.state.max_tokens_per_run,
            },
        )

    def call(self, request: ModelCallRequest) -> ModelCallResponse:
        response = self.state.responses.get(request.task_name)
        if response is None:
            raise KeyError(f"model fixture response absent for task: {request.task_name}")
        if response.task_name != request.task_name:
            raise ValueError("model fixture response task name does not match request")
        return response

    def call_typed(
        self,
        request: ModelCallRequest,
        output_model: type[ModelOutputT],
    ) -> ModelOutputT:
        response = self.call(request)
        return validate_model_response_output(
            response,
            output_model,
            expected_task_name=request.task_name,
        )

    def budget_probe(self, budget: ModelCallBudget) -> CuratorProbe:
        failures: dict[str, dict[str, int]] = {}
        if budget.max_calls_per_run > self.state.max_calls_per_run:
            failures["max_calls_per_run"] = {
                "requested": budget.max_calls_per_run,
                "available": self.state.max_calls_per_run,
            }
        if budget.max_tokens_per_run > self.state.max_tokens_per_run:
            failures["max_tokens_per_run"] = {
                "requested": budget.max_tokens_per_run,
                "available": self.state.max_tokens_per_run,
            }
        if failures:
            return CuratorProbe(
                name="model-budget",
                status="fail",
                message="requested model budget exceeds model proxy fixture limits",
                details=failures,
            )
        return CuratorProbe(
            name="model-budget",
            status="pass",
            message="requested model budget fits model proxy fixture limits",
            details={
                "max_calls_per_run": budget.max_calls_per_run,
                "max_tokens_per_run": budget.max_tokens_per_run,
            },
        )


class HttpModelProxyAdapter:
    def __init__(
        self,
        base_url: str,
        *,
        token: str | None,
        client: httpx.Client | None = None,
        timeout_seconds: float = 120,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self._client = client
        self.timeout_seconds = timeout_seconds

    def probe(self, *, required: bool) -> CuratorProbe:
        missing = []
        if not self.base_url:
            missing.append("model proxy URL")
        if not self.token:
            missing.append("model proxy token")
        if missing:
            return CuratorProbe(
                name="model-proxy",
                status="fail" if required else "skip",
                message="model proxy probe not configured",
                details={"missing": missing},
            )
        try:
            response = self._get("/healthz")
        except httpx.HTTPError as exc:
            return CuratorProbe(
                name="model-proxy",
                status="fail",
                message=f"model proxy unreachable: {exc}",
            )
        return CuratorProbe(
            name="model-proxy",
            status="pass" if response.status_code == 200 else "fail",
            message=f"model proxy health responded with HTTP {response.status_code}",
        )

    def call(self, request: ModelCallRequest) -> ModelCallResponse:
        if not self.base_url:
            raise ValueError("model proxy URL is required for live model calls")
        if not self.token:
            raise ValueError("model proxy token is required for live model calls")
        if not request.run_id:
            raise ValueError("model proxy call requires run_id")
        if not request.model:
            raise ValueError("model proxy call requires model")
        payload: dict[str, Any] = {
            "run_id": request.run_id,
            "model": request.model,
            "messages": request.input.get("messages"),
            "metadata": {
                "task_name": request.task_name,
                **{
                    str(key): str(value)
                    for key, value in request.input.get("metadata", {}).items()
                    if value is not None
                },
            },
        }
        if not isinstance(payload["messages"], list) or not payload["messages"]:
            raise ValueError("model proxy call requires non-empty messages")
        for optional_key in ("response_format", "temperature"):
            if optional_key in request.input:
                payload[optional_key] = request.input[optional_key]
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens
        try:
            response = self._post("/v1/model/call", payload)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            body = exc.response.text[:500]
            raise RuntimeError(
                f"model proxy call failed with HTTP {exc.response.status_code}: {body}"
            ) from exc
        except httpx.HTTPError as exc:
            raise RuntimeError(f"model proxy call failed: {exc}") from exc
        try:
            raw = response.json()
        except ValueError as exc:
            raise RuntimeError("model proxy returned non-JSON response") from exc
        if not isinstance(raw, dict):
            raise RuntimeError("model proxy response must be a JSON object")
        output = _model_output_from_proxy_content(raw.get("content"))
        usage = raw.get("usage") if isinstance(raw.get("usage"), dict) else {}
        return ModelCallResponse(
            task_name=request.task_name,
            output=output,
            usage={
                "input_tokens": _int_value(usage.get("prompt_tokens")) or 0,
                "output_tokens": _int_value(usage.get("completion_tokens")) or 0,
            },
        )

    def _get(self, path: str) -> httpx.Response:
        url = self._url_for(path)
        headers = {"Authorization": f"Bearer {self.token}"}
        if self._client is not None:
            return self._client.get(url, headers=headers, timeout=self.timeout_seconds)
        return httpx.get(url, headers=headers, timeout=self.timeout_seconds)

    def _post(self, path: str, payload: dict[str, Any]) -> httpx.Response:
        url = self._url_for(path)
        headers = {"Authorization": f"Bearer {self.token}"}
        if self._client is not None:
            return self._client.post(
                url,
                headers=headers,
                json=payload,
                timeout=self.timeout_seconds,
            )
        return httpx.post(url, headers=headers, json=payload, timeout=self.timeout_seconds)

    def _url_for(self, path: str) -> str:
        base = httpx.URL(self.base_url)
        if path == "/healthz" and base.path.rstrip("/") in {"/v1/model/call", "/v1"}:
            return str(base.copy_with(path="/healthz", query=None, fragment=None))
        if path == "/v1/model/call" and base.path.rstrip("/") == "/v1/model/call":
            return self.base_url
        return f"{self.base_url}{path}"


def _model_output_from_proxy_content(content: Any) -> dict[str, Any]:
    if isinstance(content, dict):
        return content
    if isinstance(content, str):
        try:
            parsed = httpx.Response(200, content=content.encode("utf-8")).json()
        except ValueError as exc:
            raise RuntimeError("model proxy content was not JSON object content") from exc
        if isinstance(parsed, dict):
            return parsed
    raise RuntimeError("model proxy content must be a JSON object")


def _split_repo(repo_full_name: str) -> tuple[str, str]:
    parts = repo_full_name.split("/", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError(f"repository must be in owner/name form: {repo_full_name}")
    return parts[0], parts[1]


def _curator_metadata(intent: ExecutionIntent) -> dict[str, str]:
    action_scope = "maintenance"
    if intent.evidence.upload_ids:
        action_scope = "upload"
    elif intent.evidence.feedback_ids:
        action_scope = "feedback"
    metadata = {"YKM-Curator-Action": action_scope}
    run_id = _run_id_from_branch(intent.branch)
    if run_id:
        metadata["YKM-Curator-Run"] = run_id
    return metadata


def _run_id_from_branch(branch: str | None) -> str | None:
    if branch is None:
        return None
    parts = branch.split("/", 2)
    if len(parts) < 3 or parts[0] != "curator":
        return None
    return parts[1] or None


def _issue_snapshot_from_raw(raw: dict[str, Any]) -> CuratorIssueSnapshot:
    state = _str_value(raw.get("state"))
    if state not in {"open", "closed"}:
        raise ValueError(f"broker issue returned invalid state: {state!r}")
    return CuratorIssueSnapshot(
        number=_required_int(raw, "number"),
        state=state,
        title=_str_value(raw.get("title")),
        body=_str_value(raw.get("body")) or "",
    )


def _required_int(raw: dict[str, Any], key: str) -> int:
    value = _int_value(raw.get(key))
    if value is None:
        raise ValueError(f"broker payload missing integer field: {key}")
    return value


def _int_value(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


def _str_value(value: Any) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None


def _label_names(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    labels: list[str] = []
    for item in value:
        if isinstance(item, str) and item:
            labels.append(item)
        elif isinstance(item, dict):
            name = _str_value(item.get("name"))
            if name:
                labels.append(name)
    return labels


def _review_decision(reviews: list[dict[str, Any]]) -> str:
    states = [str(review.get("state", "")).lower() for review in reviews]
    if any(state == "changes_requested" for state in states):
        return "changes_requested"
    if any(state == "approved" for state in states):
        return "approved"
    if any(state == "commented" for state in states):
        return "commented"
    return "none"


def _review_comments(reviews: list[dict[str, Any]]) -> list[str]:
    comments: list[str] = []
    for review in reviews:
        body = _str_value(review.get("body"))
        if body:
            comments.append(body[:2000])
    return comments[:20]


def _review_snapshots(reviews: list[dict[str, Any]]) -> list[CuratorPrReviewSnapshot]:
    return [_review_snapshot(review) for review in reviews]


def _review_snapshot(raw: dict[str, Any]) -> CuratorPrReviewSnapshot:
    raw_id = raw.get("id")
    database_id = (
        _int_value(raw.get("database_id"))
        or _int_value(raw.get("databaseId"))
        or _int_value(raw_id)
    )
    node_id = (
        _str_value(raw.get("node_id"))
        or _str_value(raw.get("nodeId"))
        or (_str_value(raw_id) if not isinstance(raw_id, int) else None)
    )
    state = _str_value(raw.get("state")) or "UNKNOWN"
    return CuratorPrReviewSnapshot(
        id=node_id or (str(database_id) if database_id is not None else None),
        database_id=database_id,
        state=state,
        author_login=_author_login(raw),
        body=_str_value(raw.get("body")) or "",
        submitted_at=_str_value(raw.get("submitted_at")) or _str_value(raw.get("submittedAt")),
    )


def _review_thread_snapshot(raw: dict[str, Any]) -> CuratorPrReviewThreadSnapshot:
    raw_id = raw.get("id")
    database_id = (
        _int_value(raw.get("database_id"))
        or _int_value(raw.get("databaseId"))
        or _int_value(raw_id)
    )
    node_id = (
        _str_value(raw.get("node_id"))
        or _str_value(raw.get("nodeId"))
        or (_str_value(raw_id) if not isinstance(raw_id, int) else None)
    )
    comments_raw = raw.get("comments")
    comments = [
        _review_thread_comment_snapshot(comment)
        for comment in comments_raw
        if isinstance(comment, dict)
    ] if isinstance(comments_raw, list) else []
    return CuratorPrReviewThreadSnapshot(
        id=node_id or (str(database_id) if database_id is not None else None),
        database_id=database_id,
        is_resolved=bool(raw.get("is_resolved") or raw.get("isResolved")),
        path=_str_value(raw.get("path")),
        line=_int_value(raw.get("line")),
        comments=comments,
    )


def _review_thread_comment_snapshot(raw: dict[str, Any]) -> CuratorPrReviewCommentSnapshot:
    raw_id = raw.get("id")
    database_id = (
        _int_value(raw.get("database_id"))
        or _int_value(raw.get("databaseId"))
        or _int_value(raw_id)
    )
    node_id = (
        _str_value(raw.get("node_id"))
        or _str_value(raw.get("nodeId"))
        or (_str_value(raw_id) if not isinstance(raw_id, int) else None)
    )
    return CuratorPrReviewCommentSnapshot(
        id=node_id or (str(database_id) if database_id is not None else None),
        database_id=database_id,
        author_login=_author_login(raw),
        body=_str_value(raw.get("body")) or "",
        path=_str_value(raw.get("path")),
        line=_int_value(raw.get("line")),
    )


def _author_login(raw: dict[str, Any]) -> str | None:
    author = raw.get("author") or raw.get("user")
    if isinstance(author, dict):
        return _str_value(author.get("login"))
    return _str_value(raw.get("author")) or _str_value(raw.get("author_login"))


def _nested_str(raw: dict[str, Any], outer: str, inner: str) -> str | None:
    nested = raw.get(outer)
    if not isinstance(nested, dict):
        return None
    return _str_value(nested.get(inner))


def _pr_state_value(value: str | None) -> str:
    if value in {"open", "closed"}:
        return value
    raise ValueError(f"broker pull returned invalid state: {value!r}")
