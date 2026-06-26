from __future__ import annotations

import json
from pathlib import Path

import httpx

from curator.adapters import (
    HttpBrokerAdapter,
)
from curator.models import (
    ActionEvidence,
    CuratorPrSnapshot,
    ExecutionIntent,
    UploadReviewPreview,
)
from curator.state import deterministic_idempotency_key
from ykm.curator import CuratorDryRunConfig, run_curator_dry_run





def test_http_broker_adapter_probe_uses_healthz_without_secret_headers() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"ok": True})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = HttpBrokerAdapter("http://broker:8080", client=client)

    probe = adapter.probe(required=True)

    assert probe.status == "pass"
    assert probe.message == "broker health responded with HTTP 200"
    assert len(requests) == 1
    assert str(requests[0].url) == "http://broker:8080/healthz"
    assert "authorization" not in requests[0].headers
    assert "x-broker-agent-secret" not in requests[0].headers


def test_http_broker_adapter_probe_failure_does_not_expose_credentials() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = HttpBrokerAdapter("http://broker:8080", client=client)

    probe = adapter.probe(required=True)

    assert probe.status == "fail"
    assert "connection refused" in probe.message
    assert "secret" not in probe.model_dump_json().lower()


def test_http_broker_adapter_generates_readonly_preflight_descriptors() -> None:
    evidence = ActionEvidence(feedback_ids=["fb_1"])
    intent = ExecutionIntent(
        action_id="act_1",
        operation="pull.create",
        idempotency_key=deterministic_idempotency_key("corpus_pr", evidence),
        target_repo="grubbyhacker/ykmcorpus",
        branch="curator/run/corpus-pr-fb-1",
        evidence=evidence,
    )

    probes = HttpBrokerAdapter("http://broker:8080").preflight_intents([intent])

    assert len(probes) == 1
    assert probes[0].status == "skip"
    requests = probes[0].details["requests"]
    assert [request["operation"] for request in requests] == ["pull.list", "issue.search"]
    assert requests[0]["method"] == "GET"
    assert requests[0]["path"] == "/repos/grubbyhacker/ykmcorpus/pulls"
    assert requests[0]["params"]["head"] == "grubbyhacker:curator/run/corpus-pr-fb-1"
    assert requests[1]["params"]["q"] == intent.idempotency_key


def test_http_broker_adapter_generates_pr_reconciliation_read_descriptors() -> None:
    probe = HttpBrokerAdapter("http://broker:8080").pr_reconciliation_preflight(
        target_repo="grubbyhacker/ykmcorpus",
        snapshots=[
            CuratorPrSnapshot(
                number=44,
                state="open",
                body="YKM-Curator-Run: run-pr",
                branch="curator/run-pr/upload-upl-1",
            )
        ],
    )

    assert probe.status == "skip"
    requests = probe.details["requests"]
    assert [request["operation"] for request in requests[:2]] == ["pull.list", "issue.search"]
    assert requests[0]["params"] == {
        "state": "all",
        "head_prefix": "grubbyhacker:curator/",
        "base": "main",
    }
    assert [request["operation"] for request in requests[2:]] == [
        "pull.read",
        "pull.comments",
        "pull.reviews",
        "pull.review_comments",
        "pull.review_threads",
        "commit.status",
        "check_runs",
    ]
    assert requests[2]["path"] == "/repos/grubbyhacker/ykmcorpus/pulls/44"


def test_http_broker_adapter_generates_upload_review_read_descriptors() -> None:
    preview = UploadReviewPreview(
        upload_id="upl_1",
        queue="pending",
        action_id="upl_act_1",
        idempotency_key="upload:abc123",
        current_state="pending",
        proposed_state="claimed",
        branch="curator/run-upload/upload-upl-1-abc123",
        reason="preview",
    )

    probe = HttpBrokerAdapter("http://broker:8080").upload_review_preflight(
        target_repo="grubbyhacker/ykmcorpus",
        previews=[preview],
    )

    assert probe is not None
    assert probe.status == "skip"
    requests = probe.details["requests"]
    assert [request["operation"] for request in requests] == ["pull.list", "issue.search"]
    assert requests[0]["path"] == "/repos/grubbyhacker/ykmcorpus/pulls"
    assert requests[0]["params"]["head"] == (
        "grubbyhacker:curator/run-upload/upload-upl-1-abc123"
    )
    assert requests[1]["path"] == "/repos/grubbyhacker/ykmcorpus/issues"
    assert requests[1]["params"]["q"] == "upload:abc123"


def test_http_broker_adapter_creates_pull_with_curator_metadata() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["auth"] = request.headers.get("authorization")
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            201,
            json={
                "number": 12,
                "html_url": "https://github.invalid/grubbyhacker/ykmcorpus/pull/12",
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    intent = ExecutionIntent(
        action_id="upl_act_1",
        operation="pull.create",
        idempotency_key="upload:abc123",
        target_repo="grubbyhacker/ykmcorpus",
        branch="curator/run-upload/upload-upl-1-abc123",
        evidence=ActionEvidence(upload_ids=["upl_1"]),
        title="Upload review",
        body="body",
    )

    result = HttpBrokerAdapter(
        "http://broker:8080",
        client=client,
        agent_id="ykm-curator",
        agent_secret="secret",
    ).create_pull(intent)

    assert result.status == "executed"
    assert result.pr_number == 12
    assert result.url == "https://github.invalid/grubbyhacker/ykmcorpus/pull/12"
    assert captured["path"] == "/v1/repos/grubbyhacker/ykmcorpus/pulls"
    assert captured["auth"] is not None
    body = captured["body"]
    assert isinstance(body, dict)
    assert body["head"] == "curator/run-upload/upload-upl-1-abc123"
    assert body["base"] == "main"
    assert body["metadata"] == {
        "YKM-Curator-Run": "run-upload",
        "YKM-Curator-Action": "upload",
    }
    assert body["permissions"] == ["contents:write", "pull_requests:write"]


def test_http_broker_adapter_creates_issue_via_reporter_mcp(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_call(url: str, name: str, arguments: dict[str, object]) -> dict[str, object]:
        captured["url"] = url
        captured["name"] = name
        captured["arguments"] = arguments
        return {
            "result": {
                "structuredContent": {
                    "number": 44,
                    "html_url": "https://github.invalid/grubbyhacker/ykmcorpus/issues/44",
                }
            }
        }

    monkeypatch.setenv("YKM_REPORTER_MCP_URL", "http://issue-reporter:8090/mcp")
    monkeypatch.setattr("curator.adapters._call_reporter_mcp_tool", fake_call)
    intent = ExecutionIntent(
        action_id="act_1",
        operation="issue.create",
        idempotency_key="corpus_issue:abc123",
        target_repo="grubbyhacker/ykmcorpus",
        evidence=ActionEvidence(feedback_ids=["fb_1"]),
        title="Feedback issue",
        body="body",
        labels=["ykm-curator", "feedback", "corpus"],
    )

    result = HttpBrokerAdapter("http://broker:8080").create_issue(intent)

    assert result.status == "executed"
    assert result.issue_number == 44
    assert result.url == "https://github.invalid/grubbyhacker/ykmcorpus/issues/44"
    assert captured["url"] == "http://issue-reporter:8090/mcp"
    assert captured["name"] == "broker_report_issue"
    arguments = captured["arguments"]
    assert isinstance(arguments, dict)
    assert arguments["repo"] == "grubbyhacker/ykmcorpus"
    assert arguments["dedupe_key"] == "corpus_issue:abc123"
    assert arguments["labels"] == []
    assert arguments["source_agent_id"] == "ykm-curator"


def test_http_broker_adapter_posts_issue_comment_with_agent_auth() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["auth"] = request.headers.get("authorization")
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            201,
            json={
                "html_url": "https://github.invalid/grubbyhacker/ykmcorpus/pull/5#issuecomment-1",
            },
        )

    result = HttpBrokerAdapter(
        "http://broker:8080",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        agent_id="ykm-curator",
        agent_secret="secret",
    ).add_issue_comment(
        target_repo="grubbyhacker/ykmcorpus",
        issue_number=5,
        body="Curator repair completed and this PR is ready for review again.",
        action_id="pr_repair_comment_5",
        idempotency_key="pr-repair-comment:5:curator/run",
        metadata={
            "YKM-Curator-Run": "run-pr-repair",
            "YKM-Curator-Action": "repair",
        },
    )

    assert result.status == "executed"
    assert result.operation == "issue.comment"
    assert result.pr_number == 5
    assert result.url == "https://github.invalid/grubbyhacker/ykmcorpus/pull/5#issuecomment-1"
    assert captured["method"] == "POST"
    assert captured["path"] == "/v1/repos/grubbyhacker/ykmcorpus/issues/5/comments"
    assert captured["auth"] is not None
    assert captured["body"] == {
        "body": "Curator repair completed and this PR is ready for review again.",
        "metadata": {
            "YKM-Curator-Run": "run-pr-repair",
            "YKM-Curator-Action": "repair",
        },
    }


def test_http_broker_adapter_posts_pr_repair_handoff_mutations() -> None:
    requests: list[tuple[str, str, str | None, dict[str, object] | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8")) if request.content else None
        requests.append(
            (
                request.method,
                request.url.path,
                request.headers.get("idempotency-key"),
                body,
            )
        )
        return httpx.Response(200, json={"html_url": "https://github.invalid/result"})

    adapter = HttpBrokerAdapter(
        "http://broker:8080",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        agent_id="ykm-curator",
        agent_secret="secret",
    )

    results = [
        adapter.dismiss_pull_review(
            target_repo="grubbyhacker/ykmcorpus",
            pr_number=5,
            review_id="123",
            message="dismissed",
            action_id="dismiss",
            idempotency_key="dismiss-key",
        ),
        adapter.resolve_review_thread(
            target_repo="grubbyhacker/ykmcorpus",
            pr_number=5,
            thread_id="PRRT_123",
            message="resolved",
            action_id="resolve",
            idempotency_key="resolve-key",
        ),
        adapter.add_issue_label(
            target_repo="grubbyhacker/ykmcorpus",
            issue_number=5,
            label="ym-curator: waiting-review",
            action_id="add-label",
            idempotency_key="add-label-key",
        ),
        adapter.remove_issue_label(
            target_repo="grubbyhacker/ykmcorpus",
            issue_number=5,
            label="ym-curator: needs work",
            action_id="remove-label",
            idempotency_key="remove-label-key",
        ),
    ]

    assert [result.status for result in results] == ["executed"] * 4
    assert requests == [
        (
            "PUT",
            "/v1/repos/grubbyhacker/ykmcorpus/pulls/5/reviews/123/dismissal",
            "dismiss-key",
            {"message": "dismissed"},
        ),
        (
            "PUT",
            "/v1/repos/grubbyhacker/ykmcorpus/pulls/5/review-threads/PRRT_123/resolve",
            "resolve-key",
            {"message": "resolved"},
        ),
        (
            "POST",
            "/v1/repos/grubbyhacker/ykmcorpus/issues/5/labels",
            "add-label-key",
            {"labels": ["ym-curator: waiting-review"]},
        ),
        (
            "DELETE",
            "/v1/repos/grubbyhacker/ykmcorpus/issues/5/labels/ym-curator: needs work",
            "remove-label-key",
            None,
        ),
    ]


def test_http_broker_adapter_generates_issue_reconciliation_read_descriptors() -> None:
    probe = HttpBrokerAdapter("http://broker:8080").issue_reconciliation_preflight(
        target_repo="grubbyhacker/ykmcorpus",
        issue_numbers=[77, 77, 78],
    )

    assert probe is not None
    assert probe.status == "skip"
    requests = probe.details["requests"]
    assert [request["operation"] for request in requests] == [
        "issue.read",
        "issue.comments",
        "issue.read",
        "issue.comments",
    ]
    assert requests[0]["path"] == "/repos/grubbyhacker/ykmcorpus/issues/77"
    assert requests[2]["path"] == "/repos/grubbyhacker/ykmcorpus/issues/78"


def test_http_broker_adapter_reads_pr_and_issue_snapshots_with_agent_auth() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url.path.startswith("/v1/repos/grubbyhacker/ykmcorpus/")
        assert request.headers.get("authorization", "").startswith("Basic ")
        if request.url.path.endswith("/pulls"):
            return httpx.Response(
                200,
                json=[
                    {
                        "number": 44,
                        "state": "open",
                        "title": "Curator PR",
                        "body": "YKM-Curator-Run: run-live-read",
                        "head_ref": "curator/run-live-read/test",
                        "head_sha": "abc123",
                        "merged": False,
                        "labels": [{"name": "ym-curator: needs work"}],
                    },
                    {
                        "number": 45,
                        "state": "open",
                        "title": "Curator PR missing checks",
                        "body": "YKM-Curator-Run: run-missing-checks",
                        "head_ref": "curator/run-missing-checks/test",
                        "head_sha": "def456",
                        "merged": False,
                        "labels": [],
                    },
                ],
            )
        if request.url.path.endswith("/pulls/44/reviews"):
            return httpx.Response(
                200,
                json=[
                    {
                        "id": 123,
                        "node_id": "PRR_123",
                        "state": "CHANGES_REQUESTED",
                        "author": {"login": "grubbyhacker"},
                        "body": "needs repair",
                    }
                ],
            )
        if request.url.path.endswith("/pulls/45/reviews"):
            return httpx.Response(200, json=[])
        if request.url.path.endswith("/pulls/44/review-threads"):
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "PRRT_123",
                        "database_id": 456,
                        "is_resolved": False,
                        "path": "preferences/dev-environment.md",
                        "line": 1,
                        "comments": [
                            {
                                "id": "PRRC_123",
                                "database_id": 789,
                                "body": "fix this",
                                "path": "preferences/dev-environment.md",
                                "line": 1,
                                "author": {"login": "grubbyhacker"},
                            }
                        ],
                    }
                ],
            )
        if request.url.path.endswith("/pulls/45/review-threads"):
            return httpx.Response(200, json=[])
        if request.url.path.endswith("/commits/abc123/status"):
            return httpx.Response(200, json={"state": "success"})
        if request.url.path.endswith("/commits/def456/status"):
            return httpx.Response(200, json={"state": "pending", "statuses": []})
        if request.url.path.endswith("/commits/abc123/check-runs"):
            return httpx.Response(200, json={"check_runs": [{"conclusion": "success"}]})
        if request.url.path.endswith("/commits/def456/check-runs"):
            return httpx.Response(200, json={"check_runs": []})
        if request.url.path.endswith("/issues/77"):
            return httpx.Response(
                200,
                json={
                    "number": 77,
                    "state": "closed",
                    "title": "Owner input",
                    "body": "resolved",
                },
            )
        raise AssertionError(f"unexpected broker request: {request.method} {request.url}")

    adapter = HttpBrokerAdapter(
        "http://broker:8080",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        agent_id="agent",
        agent_secret="broker-secret",
    )

    pr_snapshots, pr_probe = adapter.read_pr_snapshots(target_repo="grubbyhacker/ykmcorpus")
    issue_snapshots, issue_probe = adapter.read_issue_snapshots(
        target_repo="grubbyhacker/ykmcorpus",
        issue_numbers=[77],
    )

    assert pr_probe.status == "pass"
    assert pr_probe.details == {"count": 2}
    assert pr_snapshots[0].number == 44
    assert pr_snapshots[0].state == "open"
    assert pr_snapshots[0].labels == ["ym-curator: needs work"]
    assert pr_snapshots[0].review_decision == "changes_requested"
    assert pr_snapshots[0].reviews[0].id == "PRR_123"
    assert pr_snapshots[0].reviews[0].database_id == 123
    assert pr_snapshots[0].reviews[0].author_login == "grubbyhacker"
    assert pr_snapshots[0].review_threads[0].id == "PRRT_123"
    assert pr_snapshots[0].review_threads[0].database_id == 456
    assert pr_snapshots[0].review_threads[0].comments[0].id == "PRRC_123"
    assert pr_snapshots[0].review_threads[0].comments[0].database_id == 789
    assert pr_snapshots[0].unresolved_thread_count == 1
    assert pr_snapshots[0].checks_conclusion == "success"
    assert pr_snapshots[1].number == 45
    assert pr_snapshots[1].checks_conclusion == "missing"
    assert issue_probe is not None
    assert issue_probe.status == "pass"
    assert issue_snapshots[0].number == 77
    assert issue_snapshots[0].state == "closed"
    assert "broker-secret" not in pr_probe.model_dump_json()
    assert len([request for request in requests if request.url.path.endswith("/pulls")]) == 2


def test_http_broker_adapter_skips_not_found_issue_reads_and_continues() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/issues/77"):
            return httpx.Response(
                200,
                json={
                    "number": 77,
                    "state": "closed",
                    "title": "Owner input",
                    "body": "resolved",
                },
            )
        if request.url.path.endswith("/issues/78"):
            return httpx.Response(404, json={"error": {"code": "github_not_found"}})
        if request.url.path.endswith("/issues/79"):
            return httpx.Response(502, text="GitHub request failed with HTTP 404: Not Found")
        raise AssertionError(f"unexpected broker request: {request.method} {request.url}")

    adapter = HttpBrokerAdapter(
        "http://broker:8080",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        agent_id="agent",
        agent_secret="broker-secret",
    )

    issue_snapshots, issue_probe = adapter.read_issue_snapshots(
        target_repo="grubbyhacker/ykmcorpus",
        issue_numbers=[79, 78, 77, 78],
    )

    assert [snapshot.number for snapshot in issue_snapshots] == [77]
    assert issue_probe is not None
    assert issue_probe.status == "pass"
    assert issue_probe.details["count"] == 1
    assert issue_probe.details["skipped_count"] == 2
    assert issue_probe.details["skipped"] == [
        {
            "number": 78,
            "reason": "not_found",
            "status_code": 404,
            "error_code": "github_not_found",
        },
        {
            "number": 79,
            "reason": "not_found",
            "status_code": 502,
            "error_code": None,
        },
    ]
    assert [request.url.path.rsplit("/", 1)[-1] for request in requests] == ["77", "78", "79"]


def test_http_broker_adapter_fatal_issue_read_errors_still_fail() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/issues/77"):
            return httpx.Response(502, text="upstream timeout")
        raise AssertionError(f"unexpected broker request: {request.method} {request.url}")

    adapter = HttpBrokerAdapter(
        "http://broker:8080",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        agent_id="agent",
        agent_secret="broker-secret",
    )

    issue_snapshots, issue_probe = adapter.read_issue_snapshots(
        target_repo="grubbyhacker/ykmcorpus",
        issue_numbers=[77],
    )

    assert issue_snapshots == []
    assert issue_probe is not None
    assert issue_probe.status == "fail"
    assert issue_probe.message == "broker issue read failed: broker read failed with HTTP 502"


def test_runner_can_use_opt_in_http_broker_reads_for_reconciliation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    intake = tmp_path / "intake"
    feedback = intake / "feedback" / "feedback.jsonl"
    feedback.parent.mkdir(parents=True)
    feedback.write_text("", encoding="utf-8")
    decisions = intake / "feedback" / "curator-decisions.jsonl"
    decisions.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "feedback_id": "fb_blocked",
                "run_id": "old-run",
                "plan_action_id": "act_old",
                "decision": "deferred",
                "issue_number": 77,
                "reentry_trigger": "owner_input_resolved",
                "reason": "waiting on issue",
                "timestamp": "2026-06-08T12:00:00Z",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    def fake_get(url: str, **kwargs: object) -> httpx.Response:
        assert url == "http://broker:8080/healthz"
        return httpx.Response(200)

    def fake_request(method: str, url: str, **kwargs: object) -> httpx.Response:
        assert method == "GET"
        assert kwargs["auth"] == ("agent", "broker-secret")
        if url == "http://broker:8080/v1/repos/grubbyhacker/ykmcorpus/pulls":
            params = kwargs["params"]
            if params == {"state": "all", "body_marker": "YKM-Curator-Run"}:
                return httpx.Response(
                    200,
                    json=[
                        {
                            "number": 44,
                            "state": "open",
                            "title": "Curator PR",
                            "body": (
                                "YKM-Curator-Run: run-live-read\n"
                                "YKM-Curator-Feedback: fb_blocked\n"
                            ),
                            "head_ref": "curator/run-live-read/test",
                            "head_sha": "abc123",
                            "merged": False,
                        }
                    ],
                )
            if params == {"state": "all", "head_prefix": "curator/"}:
                return httpx.Response(200, json=[])
        if url.endswith("/pulls/44/reviews"):
            return httpx.Response(200, json=[])
        if url.endswith("/pulls/44/review-threads"):
            return httpx.Response(200, json=[])
        if url.endswith("/commits/abc123/status"):
            return httpx.Response(200, json={"state": "failure"})
        if url.endswith("/commits/abc123/check-runs"):
            return httpx.Response(200, json={"check_runs": []})
        if url.endswith("/issues/77"):
            return httpx.Response(
                200,
                json={"number": 77, "state": "closed", "title": "done", "body": ""},
            )
        raise AssertionError(f"unexpected broker request: {method} {url}")

    monkeypatch.setattr("curator.adapters.httpx.get", fake_get)
    monkeypatch.setattr("curator.adapters.httpx.request", fake_request)
    monkeypatch.setenv("BROKER_AGENT_ID", "agent")
    monkeypatch.setenv("BROKER_AGENT_SECRET", "broker-secret")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    report = run_curator_dry_run(
        CuratorDryRunConfig(
            run_id="run-live-read",
            intake=intake,
            output=tmp_path / "output",
            broker_url="http://broker:8080",
            enable_broker_reads=True,
        )
    )

    assert report.status == "pass"
    assert next(probe for probe in report.probes if probe.name == "broker-pr-read").status == "pass"
    assert next(probe for probe in report.probes if probe.name == "broker-issue-read").status == "pass"
    assert report.reconciliation["pr_state_counts"] == {"checks_failed": 1}
    assert report.reconciliation["feedback_reentry_preview_count"] == 1
    assert report.reconciliation["feedback_reentry_previews"][0]["feedback_id"] == "fb_blocked"


def test_runner_skips_missing_issue_snapshot_and_continues_reconciliation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    intake = tmp_path / "intake"
    feedback = intake / "feedback" / "feedback.jsonl"
    feedback.parent.mkdir(parents=True)
    feedback.write_text("", encoding="utf-8")
    decisions = intake / "feedback" / "curator-decisions.jsonl"
    decisions.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "feedback_id": "fb_stale_issue",
                "run_id": "old-run",
                "plan_action_id": "act_old",
                "decision": "deferred",
                "issue_number": 77,
                "reentry_trigger": "owner_input_resolved",
                "reason": "waiting on issue",
                "timestamp": "2026-06-08T12:00:00Z",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    deferred = intake / "uploads" / "deferred" / "upl_blocked"
    deferred.mkdir(parents=True)
    (deferred / "manifest.json").write_text('{"upload_id":"upl_blocked"}\n', encoding="utf-8")
    (deferred / "curator.json").write_text(
        json.dumps(
            {
                "schema_version": "1",
                "upload_id": "upl_blocked",
                "state": "deferred",
                "run_id": "old-run",
                "blocking_issue_number": 78,
                "reentry_trigger": "owner_input_resolved",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    task = tmp_path / "task.json"
    task.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "run_id": "run-issue-notfound",
                "mode": "dry_run",
                "enabled_actions": ["reconcile"],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    def fake_get(url: str, **kwargs: object) -> httpx.Response:
        assert url == "http://broker:8080/healthz"
        return httpx.Response(200)

    def fake_request(method: str, url: str, **kwargs: object) -> httpx.Response:
        assert method == "GET"
        assert kwargs["auth"] == ("agent", "broker-secret")
        if url == "http://broker:8080/v1/repos/grubbyhacker/ykmcorpus/pulls":
            return httpx.Response(200, json=[])
        if url.endswith("/issues/77"):
            return httpx.Response(404, json={"error": {"code": "github_not_found"}})
        if url.endswith("/issues/78"):
            return httpx.Response(
                200,
                json={"number": 78, "state": "closed", "title": "done", "body": ""},
            )
        raise AssertionError(f"unexpected broker request: {method} {url}")

    monkeypatch.setattr("curator.adapters.httpx.get", fake_get)
    monkeypatch.setattr("curator.adapters.httpx.request", fake_request)
    monkeypatch.setenv("BROKER_AGENT_ID", "agent")
    monkeypatch.setenv("BROKER_AGENT_SECRET", "broker-secret")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    report = run_curator_dry_run(
        CuratorDryRunConfig(
            run_id="ignored",
            intake=intake,
            output=tmp_path / "output",
            task=task,
            broker_url="http://broker:8080",
            enable_broker_reads=True,
        )
    )

    assert report.status == "pass"
    issue_probe = next(probe for probe in report.probes if probe.name == "broker-issue-read")
    assert issue_probe.status == "pass"
    assert issue_probe.details["count"] == 1
    assert issue_probe.details["skipped_count"] == 1
    assert issue_probe.details["skipped"][0]["number"] == 77
    assert issue_probe.details["skipped"][0]["error_code"] == "github_not_found"
    assert issue_probe.details["skipped"][0]["references"] == [
        {
            "source": "feedback_decision",
            "field": "issue_number",
            "feedback_id": "fb_stale_issue",
            "run_id": "old-run",
        }
    ]
    assert report.partial_failures == []
    assert report.reconciliation["feedback_reentry_preview_count"] == 0
    assert report.reconciliation["upload_transition_preview_count"] == 1
    assert report.reconciliation["upload_transition_previews"][0]["upload_id"] == "upl_blocked"


