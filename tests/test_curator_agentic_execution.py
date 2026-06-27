from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from curator.adapters import FixtureBrokerAdapter
from curator.feedback_agent import execute_agentic_feedback_actions
from curator.models import (
    ActionEvidence,
    BrokerFixtureState,
    ExecutionIntent,
    ExecutionResult,
)
from curator.state import deterministic_idempotency_key
from curator.upload_agent import (
    MAX_UPLOAD_AGENT_FILE_CHARS,
    _stage_upload_agent_input,
    _upload_agent_prompt,
    execute_agentic_upload_review_prs,
)
from tests.curator_test_support import _fake_mise, _upload_agent_preview_and_bundle


def test_upload_agent_prompt_uses_staged_input_paths_without_file_content(tmp_path: Path) -> None:
    preview, bundle = _upload_agent_preview_and_bundle(tmp_path)
    upload_input = _stage_upload_agent_input(bundle, tmp_path / "upload-input")
    prompt = _upload_agent_prompt(
        run_id="run-upload-agent",
        preview=preview,
        bundle=bundle,
        upload_input=upload_input,
        validation_command=["mise", "run", "validate"],
        summary_path=tmp_path / "summary.json",
        previous_validation=None,
    )

    assert str(upload_input.root) in prompt
    assert str(upload_input.files[0].path) in prompt
    assert "# Tooling\n\nUse uv for Python dependencies." not in prompt
    assert "Edit markdown corpus files and `.ykm/corpus-policy.yaml` directly" in prompt
    assert "not an immutable permission boundary" in prompt
    assert "Inspect those files with shell tools" in prompt
    assert "summary_path" in prompt
    assert "content_summary" in prompt
    assert "You may make local commits" in prompt
    assert "Do not push" in prompt
    assert "Never create backup" in prompt


def test_upload_agent_stages_failed_runbook_sized_file_without_prompt_body(tmp_path: Path) -> None:
    preview, bundle = _upload_agent_preview_and_bundle(tmp_path)
    source = Path(bundle.path) / "files" / "tooling.md"
    body = "x" * 19923
    source.write_text(body, encoding="utf-8")

    upload_input = _stage_upload_agent_input(bundle, tmp_path / "upload-input")
    prompt = _upload_agent_prompt(
        run_id="run-upload-agent",
        preview=preview,
        bundle=bundle,
        upload_input=upload_input,
        validation_command=["mise", "run", "validate"],
        summary_path=tmp_path / "summary.json",
        previous_validation=None,
    )

    assert upload_input.files[0].char_count == 19923
    assert upload_input.files[0].path.read_text(encoding="utf-8") == body
    assert body not in prompt


def test_upload_agent_rejects_files_over_path_backed_limit(tmp_path: Path) -> None:
    _preview, bundle = _upload_agent_preview_and_bundle(tmp_path)
    source = Path(bundle.path) / "files" / "tooling.md"
    source.write_text("x" * (MAX_UPLOAD_AGENT_FILE_CHARS + 1), encoding="utf-8")

    with pytest.raises(ValueError, match="too large for agentic upload review"):
        _stage_upload_agent_input(bundle, tmp_path / "upload-input")


def test_agentic_upload_review_creates_pr_after_validated_codex_diff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    preview, bundle = _upload_agent_preview_and_bundle(tmp_path)
    broker = FixtureBrokerAdapter(
        BrokerFixtureState(
            schema_version="1",
            reachable=True,
            allowed_operations=["pull.create"],
        )
    )
    _fake_upload_agent_git(tmp_path, monkeypatch)
    _fake_upload_agent_codex(tmp_path, monkeypatch, mode="success")
    _fake_mise(tmp_path, monkeypatch, exit_code=0, stdout="validation ok\n")
    monkeypatch.setenv("BROKER_AGENT_ID", "ykm-curator")
    monkeypatch.setenv("BROKER_AGENT_SECRET", "broker-secret")

    results, observations = execute_agentic_upload_review_prs(
        run_id="run-upload-agent",
        mode="manual_live",
        broker_remote_url="https://broker/git/grubbyhacker/ykmcorpus.git",
        broker_adapter=broker,
        previews=[preview],
        bundles=[bundle],
        model="ykm-codex-gpt-5-mini",
        max_attempts=1,
        validation_command=["mise", "run", "validate"],
        output=tmp_path / "output",
        codex_proxy_base_url="http://proxy:8092",
        codex_proxy_token="proxy-token",
    )

    assert len(results) == 1
    assert results[0].status == "simulated"
    assert len(observations) == 1
    observation = observations[0]
    assert observation.status == "pass"
    assert observation.executor == "codex_proxy"
    assert observation.attempts == 1
    assert observation.changed_files == ["homemaint/tooling.md"]
    assert observation.draft_paths == ["homemaint/tooling.md"]
    assert "homemaint/tooling.md" in (observation.diff_stat or "")
    assert observation.returncode == 0


def test_agentic_upload_review_retries_retryable_codex_stream_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    preview, bundle = _upload_agent_preview_and_bundle(tmp_path)
    broker = FixtureBrokerAdapter(
        BrokerFixtureState(
            schema_version="1",
            reachable=True,
            allowed_operations=["pull.create"],
        )
    )
    _fake_upload_agent_git(tmp_path, monkeypatch)
    _fake_upload_agent_codex(tmp_path, monkeypatch, mode="stream_fail_once")
    _fake_mise(tmp_path, monkeypatch, exit_code=0, stdout="validation ok\n")
    monkeypatch.setenv("BROKER_AGENT_ID", "ykm-curator")
    monkeypatch.setenv("BROKER_AGENT_SECRET", "broker-secret")

    results, observations = execute_agentic_upload_review_prs(
        run_id="run-upload-agent",
        mode="manual_live",
        broker_remote_url="https://broker/git/grubbyhacker/ykmcorpus.git",
        broker_adapter=broker,
        previews=[preview],
        bundles=[bundle],
        model="ykm-codex-gpt-5-mini",
        max_attempts=2,
        validation_command=["mise", "run", "validate"],
        output=tmp_path / "output",
        codex_proxy_base_url="http://proxy:8092",
        codex_proxy_token="proxy-token",
    )

    assert len(results) == 1
    assert results[0].status == "simulated"
    assert len(observations) == 1
    observation = observations[0]
    assert observation.status == "pass"
    assert observation.attempts == 2
    assert observation.changed_files == ["homemaint/tooling.md"]
    assert (tmp_path / "output" / "upload-review-agent" / "upl_tooling" / "codex-attempt-1.txt").exists()
    assert (tmp_path / "output" / "upload-review-agent" / "upl_tooling" / "codex-attempt-2.txt").exists()


def test_agentic_upload_review_reports_actual_attempts_for_nonretryable_codex_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    preview, bundle = _upload_agent_preview_and_bundle(tmp_path)
    broker = FixtureBrokerAdapter(BrokerFixtureState(schema_version="1", reachable=True))
    _fake_upload_agent_git(tmp_path, monkeypatch)
    _fake_upload_agent_codex(tmp_path, monkeypatch, mode="nonretryable_fail")
    _fake_mise(tmp_path, monkeypatch, exit_code=0, stdout="validation ok\n")
    monkeypatch.setenv("BROKER_AGENT_ID", "ykm-curator")
    monkeypatch.setenv("BROKER_AGENT_SECRET", "broker-secret")

    results, observations = execute_agentic_upload_review_prs(
        run_id="run-upload-agent",
        mode="manual_live",
        broker_remote_url="https://broker/git/grubbyhacker/ykmcorpus.git",
        broker_adapter=broker,
        previews=[preview],
        bundles=[bundle],
        model="ykm-codex-gpt-5-mini",
        max_attempts=2,
        validation_command=["mise", "run", "validate"],
        output=tmp_path / "output",
        codex_proxy_base_url="http://proxy:8092",
        codex_proxy_token="proxy-token",
    )

    assert len(results) == 1
    assert results[0].status == "failed"
    assert len(observations) == 1
    assert observations[0].status == "fail"
    assert observations[0].attempts == 1
    assert "Codex execution failed" in observations[0].message


def test_agentic_upload_review_creates_pr_after_agent_local_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    preview, bundle = _upload_agent_preview_and_bundle(tmp_path)
    broker = FixtureBrokerAdapter(
        BrokerFixtureState(
            schema_version="1",
            reachable=True,
            allowed_operations=["pull.create"],
        )
    )
    _fake_upload_agent_git(tmp_path, monkeypatch)
    _fake_upload_agent_codex(tmp_path, monkeypatch, mode="committed")
    _fake_mise(tmp_path, monkeypatch, exit_code=0, stdout="validation ok\n")
    monkeypatch.setenv("BROKER_AGENT_ID", "ykm-curator")
    monkeypatch.setenv("BROKER_AGENT_SECRET", "broker-secret")

    results, observations = execute_agentic_upload_review_prs(
        run_id="run-upload-agent",
        mode="manual_live",
        broker_remote_url="https://broker/git/grubbyhacker/ykmcorpus.git",
        broker_adapter=broker,
        previews=[preview],
        bundles=[bundle],
        model="ykm-codex-gpt-5-mini",
        max_attempts=1,
        validation_command=["mise", "run", "validate"],
        output=tmp_path / "output",
        codex_proxy_base_url="http://proxy:8092",
        codex_proxy_token="proxy-token",
    )

    assert len(results) == 1
    assert results[0].status == "simulated"
    assert len(observations) == 1
    observation = observations[0]
    assert observation.status == "pass"
    assert observation.changed_files == ["homemaint/tooling.md"]
    assert observation.draft_paths == ["homemaint/tooling.md"]
    assert "branch delta" in observation.message


def test_agentic_upload_review_validation_failure_does_not_create_pr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    preview, bundle = _upload_agent_preview_and_bundle(tmp_path)
    broker = FixtureBrokerAdapter(BrokerFixtureState(schema_version="1", reachable=True))
    _fake_upload_agent_git(tmp_path, monkeypatch)
    _fake_upload_agent_codex(tmp_path, monkeypatch, mode="success")
    _fake_mise(tmp_path, monkeypatch, exit_code=1, stderr="bad corpus\n")
    monkeypatch.setenv("BROKER_AGENT_ID", "ykm-curator")
    monkeypatch.setenv("BROKER_AGENT_SECRET", "broker-secret")

    results, observations = execute_agentic_upload_review_prs(
        run_id="run-upload-agent",
        mode="manual_live",
        broker_remote_url="https://broker/git/grubbyhacker/ykmcorpus.git",
        broker_adapter=broker,
        previews=[preview],
        bundles=[bundle],
        model="ykm-codex-gpt-5-mini",
        max_attempts=1,
        validation_command=["mise", "run", "validate"],
        output=tmp_path / "output",
        codex_proxy_base_url="http://proxy:8092",
        codex_proxy_token="proxy-token",
    )

    assert results == []
    assert len(observations) == 1
    assert observations[0].status == "fail"
    assert observations[0].returncode == 1
    assert "bad corpus" in observations[0].stderr_tail


def test_agentic_upload_review_rejects_forbidden_codex_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    preview, bundle = _upload_agent_preview_and_bundle(tmp_path)
    broker = FixtureBrokerAdapter(BrokerFixtureState(schema_version="1", reachable=True))
    _fake_upload_agent_git(tmp_path, monkeypatch)
    _fake_upload_agent_codex(tmp_path, monkeypatch, mode="workflow")
    _fake_mise(tmp_path, monkeypatch, exit_code=0, stdout="validation ok\n")
    monkeypatch.setenv("BROKER_AGENT_ID", "ykm-curator")
    monkeypatch.setenv("BROKER_AGENT_SECRET", "broker-secret")

    results, observations = execute_agentic_upload_review_prs(
        run_id="run-upload-agent",
        mode="manual_live",
        broker_remote_url="https://broker/git/grubbyhacker/ykmcorpus.git",
        broker_adapter=broker,
        previews=[preview],
        bundles=[bundle],
        model="ykm-codex-gpt-5-mini",
        max_attempts=1,
        validation_command=["mise", "run", "validate"],
        output=tmp_path / "output",
        codex_proxy_base_url="http://proxy:8092",
        codex_proxy_token="proxy-token",
    )

    assert results == []
    assert len(observations) == 1
    assert observations[0].status == "fail"
    assert "forbidden" in observations[0].message
    assert observations[0].changed_files == [".github/workflows/validate.yml"]


def test_agentic_upload_review_rejects_backup_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    preview, bundle = _upload_agent_preview_and_bundle(tmp_path)
    broker = FixtureBrokerAdapter(BrokerFixtureState(schema_version="1", reachable=True))
    _fake_upload_agent_git(tmp_path, monkeypatch)
    _fake_upload_agent_codex(tmp_path, monkeypatch, mode="backup")
    _fake_mise(tmp_path, monkeypatch, exit_code=0, stdout="validation ok\n")
    monkeypatch.setenv("BROKER_AGENT_ID", "ykm-curator")
    monkeypatch.setenv("BROKER_AGENT_SECRET", "broker-secret")

    results, observations = execute_agentic_upload_review_prs(
        run_id="run-upload-agent",
        mode="manual_live",
        broker_remote_url="https://broker/git/grubbyhacker/ykmcorpus.git",
        broker_adapter=broker,
        previews=[preview],
        bundles=[bundle],
        model="ykm-codex-gpt-5-mini",
        max_attempts=1,
        validation_command=["mise", "run", "validate"],
        output=tmp_path / "output",
        codex_proxy_base_url="http://proxy:8092",
        codex_proxy_token="proxy-token",
    )

    assert results == []
    assert len(observations) == 1
    assert observations[0].status == "fail"
    assert "backup or temporary files" in observations[0].message
    assert observations[0].changed_files == [".ykm/corpus-policy.yaml.bak"]


def test_feedback_fallback_corpus_issue_includes_private_instruction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence = ActionEvidence(feedback_ids=["fb_comment"], source_ids=["source-note"])
    intent = ExecutionIntent(
        action_id="act_1",
        operation="pull.create",
        idempotency_key=deterministic_idempotency_key("corpus_pr", evidence),
        target_repo="grubbyhacker/ykmcorpus",
        branch="curator/run/feedback",
        evidence=evidence,
    )
    captured: dict[str, ExecutionIntent] = {}

    class Broker:
        def create_issue(self, issue_intent: ExecutionIntent) -> ExecutionResult:
            captured["intent"] = issue_intent
            return ExecutionResult(
                action_id=issue_intent.action_id,
                operation=issue_intent.operation,
                idempotency_key=issue_intent.idempotency_key,
                status="simulated",
                target_repo=issue_intent.target_repo,
                issue_number=42,
            )

    monkeypatch.setattr(
        "curator.feedback_agent._execute_corpus_pr",
        lambda **_kwargs: ExecutionResult(
            action_id="act_1",
            operation="pull.create",
            idempotency_key=intent.idempotency_key,
            status="failed",
            target_repo="grubbyhacker/ykmcorpus",
            branch="curator/run/feedback",
            message="synthetic feedback failure",
        ),
    )

    results = execute_agentic_feedback_actions(
        run_id="run-feedback",
        mode="manual_live",
        broker_remote_url="https://broker/git/grubbyhacker/ykmcorpus.git",
        broker_adapter=Broker(),
        intents=[intent],
        feedback_records=[
            {
                "feedback_id": "fb_comment",
                "intent": "update_existing",
                "source_id": "source-note",
                "instruction": "Add the exact article URL to the existing source note.",
            }
        ],
        model="ykm-codex-gpt-5-mini",
        max_attempts=1,
        validation_command=["mise", "run", "validate"],
        output=Path("/tmp/output"),
        codex_proxy_base_url="http://proxy:8092",
        codex_proxy_token="proxy-token",
    )

    assert results[0].status == "simulated"
    body = captured["intent"].body or ""
    assert "## Corpus Change Requests" in body
    assert "Add the exact article URL to the existing source note." in body
    assert "synthetic feedback failure" in body


def test_agentic_feedback_creates_pr_after_agent_local_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence = ActionEvidence(feedback_ids=["fb_article"], source_ids=["source-note"])
    intent = ExecutionIntent(
        action_id="act_1",
        operation="pull.create",
        idempotency_key=deterministic_idempotency_key("corpus_pr", evidence),
        target_repo="grubbyhacker/ykmcorpus",
        branch="curator/run/feedback",
        evidence=evidence,
    )
    git_calls: list[list[str]] = []

    def fake_run_git(args: list[str], *, cwd: Path | None, env: dict[str, str]) -> None:
        _ = env
        git_calls.append(args)
        if args[:1] == ["clone"]:
            Path(args[-1]).mkdir(parents=True)
        elif cwd is not None:
            cwd.mkdir(parents=True, exist_ok=True)

    def fake_git_output(args: list[str], *, cwd: Path, env: dict[str, str]) -> str:
        _ = cwd, env
        if args[:1] == ["rev-parse"]:
            return "base123\n"
        if args[:2] == ["diff", "--name-only"]:
            return "writing/roger-published-article-urls.md\n"
        if args[:2] == ["rev-list", "--count"]:
            return "1\n"
        return ""

    monkeypatch.setattr("curator.feedback_agent._run_git", fake_run_git)
    monkeypatch.setattr("curator.feedback_agent._git_output", fake_git_output)
    monkeypatch.setattr("curator.feedback_agent._changed_files", lambda _checkout, _env: [])
    monkeypatch.setattr("curator.upload_agent._git_output", fake_git_output)
    monkeypatch.setattr("curator.upload_agent._changed_files", lambda _checkout, _env: [])
    monkeypatch.setattr(
        "curator.feedback_agent._run_validation",
        lambda _checkout, command: subprocess.CompletedProcess(command, 0, "validation ok\n", ""),
    )
    monkeypatch.setattr(
        "curator.feedback_agent._run_codex_feedback_agent",
        lambda **_kwargs: tmp_path / "transcript.txt",
    )
    monkeypatch.setenv("BROKER_AGENT_ID", "ykm-curator")
    monkeypatch.setenv("BROKER_AGENT_SECRET", "broker-secret")

    results = execute_agentic_feedback_actions(
        run_id="run-feedback",
        mode="manual_live",
        broker_remote_url="https://broker/git/grubbyhacker/ykmcorpus.git",
        broker_adapter=FixtureBrokerAdapter(
            BrokerFixtureState(
                schema_version="1",
                reachable=True,
                allowed_operations=["pull.create"],
            )
        ),
        intents=[intent],
        feedback_records=[
            {
                "feedback_id": "fb_article",
                "intent": "update_existing",
                "source_id": "source-note",
                "instruction": "Add the article URL.",
            }
        ],
        model="ykm-codex-gpt-5-mini",
        max_attempts=1,
        validation_command=["mise", "run", "validate"],
        output=tmp_path / "output",
        codex_proxy_base_url="http://proxy:8092",
        codex_proxy_token="proxy-token",
    )

    assert len(results) == 1
    assert results[0].operation == "pull.create"
    assert results[0].status == "simulated"
    assert ["push", "origin", "HEAD:refs/heads/curator/run/feedback"] in git_calls
    assert not any("commit" in call for call in git_calls)


def _fake_upload_agent_codex(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    mode: str,
) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    script = bin_dir / "codex"
    script.write_text(
        (
            "#!/usr/bin/env python3\n"
            "import json\n"
            "import os\n"
            "import pathlib\n"
            "import re\n"
            "import sys\n"
            f"mode = {mode!r}\n"
            "prompt = sys.argv[-1]\n"
            "match = re.search(r'\"summary_path\": \"([^\"]+)\"', prompt)\n"
            "summary = pathlib.Path(match.group(1))\n"
            "summary.parent.mkdir(parents=True, exist_ok=True)\n"
            "payload = json.loads(prompt.split('Upload payload JSON:\\n', 1)[1])\n"
            "upload_file = pathlib.Path(payload['files'][0]['path'])\n"
            "assert upload_file.exists(), upload_file\n"
            "assert 'Use uv for Python dependencies.' in upload_file.read_text()\n"
            "cwd = pathlib.Path.cwd()\n"
            "if mode == 'workflow':\n"
            "    path = cwd / '.github' / 'workflows' / 'validate.yml'\n"
            "    path.parent.mkdir(parents=True, exist_ok=True)\n"
            "    path.write_text('name: validate\\n')\n"
            "    summary.write_text(json.dumps({'content_summary': 'A workflow edit.', "
            "'draft_paths': []}))\n"
            "elif mode == 'backup':\n"
            "    path = cwd / '.ykm' / 'corpus-policy.yaml.bak'\n"
            "    path.parent.mkdir(parents=True, exist_ok=True)\n"
            "    path.write_text('backup copy\\n')\n"
            "    summary.write_text(json.dumps({'content_summary': 'A backup artifact.', "
            "'draft_paths': []}))\n"
            "elif mode == 'stream_fail_once':\n"
            "    marker = pathlib.Path(os.environ['CODEX_HOME']).parent / 'stream-failed'\n"
            "    if not marker.exists():\n"
            "        marker.write_text('1\\n')\n"
            "        path = cwd / 'homemaint' / 'partial.md'\n"
            "        path.parent.mkdir(parents=True, exist_ok=True)\n"
            "        path.write_text('partial failed attempt\\n')\n"
            "        sys.stderr.write('ERROR: Reconnecting... 1/5\\n')\n"
            "        sys.stderr.write('ERROR: stream disconnected before completion: "
            "stream closed before response.completed\\n')\n"
            "        raise SystemExit(1)\n"
            "    path = cwd / 'homemaint' / 'tooling.md'\n"
            "    path.parent.mkdir(parents=True, exist_ok=True)\n"
            "    path.write_text('---\\nid: tooling\\ntype: procedure\\ntags: "
            "[home-maintenance]\\n---\\n\\n# Tooling\\n')\n"
            "    summary.write_text(json.dumps({'content_summary': 'A tooling note about "
            "Python dependencies.', 'draft_paths': ['homemaint/tooling.md']}))\n"
            "elif mode == 'nonretryable_fail':\n"
            "    sys.stderr.write('fatal deterministic prompt contract failure\\n')\n"
            "    raise SystemExit(1)\n"
            "elif mode == 'committed':\n"
            "    path = cwd / 'homemaint' / 'tooling.md'\n"
            "    path.parent.mkdir(parents=True, exist_ok=True)\n"
            "    path.write_text('---\\nid: tooling\\ntype: procedure\\ntags: "
            "[home-maintenance]\\n---\\n\\n# Tooling\\n')\n"
            "    (cwd / '.agent-committed').write_text('1\\n')\n"
            "    summary.write_text(json.dumps({'content_summary': 'A tooling note about "
            "Python dependencies.', 'draft_paths': ['homemaint/tooling.md']}))\n"
            "else:\n"
            "    path = cwd / 'homemaint' / 'tooling.md'\n"
            "    path.parent.mkdir(parents=True, exist_ok=True)\n"
            "    path.write_text('---\\nid: tooling\\ntype: procedure\\ntags: "
            "[home-maintenance]\\n---\\n\\n# Tooling\\n')\n"
            "    summary.write_text(json.dumps({'content_summary': 'A tooling note about "
            "Python dependencies.', 'draft_paths': ['homemaint/tooling.md']}))\n"
            "raise SystemExit(0)\n"
        ),
        encoding="utf-8",
    )
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    return script


def _fake_upload_agent_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    script = bin_dir / "git"
    script.write_text(
        (
            "#!/usr/bin/env python3\n"
            "import pathlib\n"
            "import sys\n"
            "args = sys.argv[1:]\n"
            "if args[:1] == ['clone']:\n"
            "    target = pathlib.Path(args[-1])\n"
            "    (target / '.ykm').mkdir(parents=True, exist_ok=True)\n"
            "    (target / 'homemaint').mkdir(parents=True, exist_ok=True)\n"
            "    (target / '.git').mkdir(parents=True, exist_ok=True)\n"
            "    (target / '.ykm' / 'corpus-policy.yaml').write_text('corpus_roots:\\n  - "
            "homemaint\\nallowed_types:\\n  - procedure\\nallowed_tags:\\n  - "
            "home-maintenance\\n')\n"
            "    (target / 'mise.toml').write_text('[tasks.validate]\\nrun = \"true\"\\n')\n"
            "    raise SystemExit(0)\n"
            "if args[:2] == ['status', '--porcelain']:\n"
            "    cwd = pathlib.Path.cwd()\n"
            "    paths = []\n"
            "    if (cwd / 'homemaint' / 'tooling.md').exists() and not "
            "(cwd / '.agent-committed').exists():\n"
            "        paths.append('?? homemaint/tooling.md')\n"
            "    if (cwd / 'homemaint' / 'partial.md').exists():\n"
            "        paths.append('?? homemaint/partial.md')\n"
            "    if (cwd / '.github' / 'workflows' / 'validate.yml').exists():\n"
            "        paths.append('?? .github/workflows/validate.yml')\n"
            "    if (cwd / '.ykm' / 'corpus-policy.yaml.bak').exists():\n"
            "        paths.append('?? .ykm/corpus-policy.yaml.bak')\n"
            "    sys.stdout.write('\\n'.join(paths) + ('\\n' if paths else ''))\n"
            "    raise SystemExit(0)\n"
            "if args[:3] == ['ls-files', '--others', '--exclude-standard']:\n"
            "    cwd = pathlib.Path.cwd()\n"
            "    paths = []\n"
            "    if (cwd / 'homemaint' / 'tooling.md').exists() and not "
            "(cwd / '.agent-committed').exists():\n"
            "        paths.append('homemaint/tooling.md')\n"
            "    if (cwd / 'homemaint' / 'partial.md').exists():\n"
            "        paths.append('homemaint/partial.md')\n"
            "    if (cwd / '.github' / 'workflows' / 'validate.yml').exists():\n"
            "        paths.append('.github/workflows/validate.yml')\n"
            "    if (cwd / '.ykm' / 'corpus-policy.yaml.bak').exists():\n"
            "        paths.append('.ykm/corpus-policy.yaml.bak')\n"
            "    sys.stdout.write('\\n'.join(paths) + ('\\n' if paths else ''))\n"
            "    raise SystemExit(0)\n"
            "if args[:1] == ['rev-parse']:\n"
            "    sys.stdout.write('base123\\n')\n"
            "    raise SystemExit(0)\n"
            "if args[:2] == ['rev-list', '--count']:\n"
            "    cwd = pathlib.Path.cwd()\n"
            "    sys.stdout.write('1\\n' if (cwd / '.agent-committed').exists() else '0\\n')\n"
            "    raise SystemExit(0)\n"
            "if args[:2] == ['diff', '--name-only']:\n"
            "    cwd = pathlib.Path.cwd()\n"
            "    if (cwd / '.agent-committed').exists():\n"
            "        sys.stdout.write('homemaint/tooling.md\\n')\n"
            "    raise SystemExit(0)\n"
            "if args[:1] == ['reset']:\n"
            "    raise SystemExit(0)\n"
            "if args[:1] == ['clean']:\n"
            "    cwd = pathlib.Path.cwd()\n"
            "    for rel in ['homemaint/partial.md']:\n"
            "        path = cwd / rel\n"
            "        if path.exists():\n"
            "            path.unlink()\n"
            "    raise SystemExit(0)\n"
            "if args[:2] == ['diff', '--stat']:\n"
            "    cwd = pathlib.Path.cwd()\n"
            "    if (cwd / 'homemaint' / 'tooling.md').exists():\n"
            "        sys.stdout.write(' homemaint/tooling.md | 7 +++++++\\n')\n"
            "    raise SystemExit(0)\n"
            "if args[:3] == ['diff', '--cached', '--stat']:\n"
            "    sys.stdout.write(' homemaint/tooling.md | 7 +++++++\\n')\n"
            "    raise SystemExit(0)\n"
            "if args == ['diff', '--cached', '--quiet']:\n"
            "    raise SystemExit(1)\n"
            "raise SystemExit(0)\n"
        ),
        encoding="utf-8",
    )
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    return script
