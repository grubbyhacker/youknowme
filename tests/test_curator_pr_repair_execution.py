from __future__ import annotations

import sys
from pathlib import Path


from curator.models import (
    CuratorPrReviewCommentSnapshot,
    CuratorPrReconciliation,
    CuratorPrReviewSnapshot,
    CuratorPrReviewThreadSnapshot,
    CuratorPrSnapshot,
)
from curator.pr_repair import (
    RepairDelta,
    _has_blast_radius_override,
    _has_workflow_changed_file,
    _repair_guardrail_message,
    _repair_prompt,
    execute_pr_repairs,
)



from tests.curator_test_support import (
    _git_for_test,
)


def test_pr_repair_prompt_includes_review_bodies_and_inline_threads() -> None:
    prompt = _repair_prompt(
        CuratorPrReconciliation(
            pr_number=7,
            pr_state="changes_requested",
            branch="curator/run/upload",
            labels=["ym-curator: needs work"],
            upload_ids=["upl_1"],
            reason="PR has changes requested",
        ),
        CuratorPrSnapshot(
            number=7,
            state="open",
            title="Upload review",
            branch="curator/run/upload",
            reviews=[
                CuratorPrReviewSnapshot(
                    state="CHANGES_REQUESTED",
                    author_login="owner",
                    body="Move this somewhere other than skills.",
                )
            ],
            review_threads=[
                CuratorPrReviewThreadSnapshot(
                    path="skills/example.md",
                    line=4,
                    comments=[
                        CuratorPrReviewCommentSnapshot(
                            author_login="owner",
                            body="This is not a reusable skill.",
                        )
                    ],
                )
            ],
        ),
    )

    assert "Move this somewhere other than skills." in prompt
    assert "Inline review on skills/example.md:4 by owner" in prompt
    assert "This is not a reusable skill." in prompt
    assert "create or use that root directly" in prompt
    assert "do not nest it under an existing root such as `preferences/dev/`" in prompt
    assert "Use `dev/` for development environment" in prompt
    assert "Do not edit `.github/workflows/**`" in prompt
    assert "explain what you skipped" in prompt


def test_pr_repair_classifies_workflow_file_changes_as_permission_blocked() -> None:
    assert _has_workflow_changed_file(
        [
            ".github/workflows/corpus-validation.yml",
            ".ykm/corpus-policy.yaml",
        ]
    )
    assert not _has_workflow_changed_file(
        [
            ".github/dependabot.yml",
            "preferences/dev-environment.md",
        ]
    )


def test_pr_repair_discards_workflow_changes_and_pushes_allowed_changes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    branch = "curator/run-pr-repair/upload-upl-20260606"
    remote = tmp_path / "remote.git"
    seed = tmp_path / "seed"
    verify = tmp_path / "verify"
    _git_for_test(["init", "--bare", str(remote)])
    _git_for_test(["clone", str(remote), str(seed)])
    _git_for_test(["checkout", "-b", "main"], cwd=seed)
    (seed / ".github" / "workflows").mkdir(parents=True)
    (seed / "homemaint").mkdir()
    (seed / ".github" / "workflows" / "corpus-validation.yml").write_text(
        "name: original validation\n",
        encoding="utf-8",
    )
    (seed / "homemaint" / "manual.md").write_text("needs repair\n", encoding="utf-8")
    _git_for_test(["add", "--all"], cwd=seed)
    _git_for_test(
        [
            "-c",
            "user.name=Test Curator",
            "-c",
            "user.email=curator@example.test",
            "commit",
            "-m",
            "Seed curator branch",
        ],
        cwd=seed,
    )
    _git_for_test(["push", "origin", "main"], cwd=seed)
    _git_for_test(["checkout", "-b", branch], cwd=seed)
    _git_for_test(["push", "origin", branch], cwd=seed)

    def fake_run_codex(**kwargs) -> Path:
        checkout = kwargs["checkout"]
        (checkout / ".github" / "workflows" / "corpus-validation.yml").write_text(
            "name: forbidden validation edit\n",
            encoding="utf-8",
        )
        (checkout / "homemaint" / "manual.md").write_text("fixed\n", encoding="utf-8")
        transcript = kwargs["output"] / "transcript.txt"
        transcript.parent.mkdir(parents=True, exist_ok=True)
        transcript.write_text("codex repaired allowed file and skipped workflow push\n", encoding="utf-8")
        return transcript

    monkeypatch.setattr("curator.pr_repair._run_codex", fake_run_codex)
    monkeypatch.setenv("BROKER_AGENT_ID", "ykm-curator")
    monkeypatch.setenv("BROKER_AGENT_SECRET", "broker-secret")

    results = execute_pr_repairs(
        run_id="run-pr-repair",
        mode="manual_live",
        reconciliations=[
            CuratorPrReconciliation(
                pr_number=5,
                pr_state="changes_requested",
                branch=branch,
                run_id="run-pr-repair",
                reason="owner requested repair",
            )
        ],
        snapshots=[],
        executor="codex_proxy",
        model="ykm-codex-gpt-5-mini",
        validation_command=[
            sys.executable,
            "-c",
            (
                "from pathlib import Path; "
                "assert Path('homemaint/manual.md').read_text() == 'fixed\\n'; "
                "assert Path('.github/workflows/corpus-validation.yml').read_text() "
                "== 'name: original validation\\n'"
            ),
        ],
        max_repairs=1,
        output=tmp_path / "output",
        broker_remote_url=str(remote),
        codex_proxy_base_url="http://proxy:8092/v1",
        codex_proxy_token="proxy-token",
    )

    assert len(results) == 1
    result = results[0]
    assert result.status == "pushed"
    assert result.pushed
    assert result.changed_files == ["homemaint/manual.md"]
    assert ".github/workflows/corpus-validation.yml" not in result.changed_files
    assert "Discarded GitHub workflow edits" in result.message
    assert result.validation_returncode == 0

    _git_for_test(["clone", "--branch", branch, str(remote), str(verify)])
    assert (verify / "homemaint" / "manual.md").read_text(encoding="utf-8") == "fixed\n"
    assert (
        verify / ".github" / "workflows" / "corpus-validation.yml"
    ).read_text(encoding="utf-8") == "name: original validation\n"


def test_pr_repair_rejects_mass_deletion_attempt(
    tmp_path: Path,
    monkeypatch,
) -> None:
    branch = "curator/run-pr-repair/upload-upl-20260606"
    remote = tmp_path / "remote.git"
    seed = tmp_path / "seed"
    verify = tmp_path / "verify"
    _git_for_test(["init", "--bare", str(remote)])
    _git_for_test(["clone", str(remote), str(seed)])
    _git_for_test(["checkout", "-b", "main"], cwd=seed)
    for path, content in {
        "AGENTS.md": "agent instructions\n",
        "README.md": "readme\n",
        ".gitignore": ".env\n",
        "mise.toml": "[tasks.validate]\nrun = \"true\"\n",
        "pyproject.toml": "[project]\nname = \"ykmcorpus\"\n",
        "uv.lock": "version = 1\n",
        "scripts/validate.sh": "#!/bin/sh\nexit 0\n",
        "tests/test_policy.py": "def test_policy():\n    assert True\n",
        "homemaint/manual.md": "needs repair\n",
    }.items():
        target = seed / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    _git_for_test(["add", "--all"], cwd=seed)
    _git_for_test(
        [
            "-c",
            "user.name=Test Curator",
            "-c",
            "user.email=curator@example.test",
            "commit",
            "-m",
            "Seed corpus",
        ],
        cwd=seed,
    )
    _git_for_test(["push", "origin", "main"], cwd=seed)
    _git_for_test(["checkout", "-b", branch], cwd=seed)
    _git_for_test(["push", "origin", branch], cwd=seed)

    def fake_run_codex(**kwargs) -> Path:
        checkout = kwargs["checkout"]
        for path in (
            "AGENTS.md",
            "README.md",
            ".gitignore",
            "mise.toml",
            "pyproject.toml",
            "uv.lock",
            "scripts/validate.sh",
            "tests/test_policy.py",
            "homemaint/manual.md",
        ):
            (checkout / path).unlink()
        transcript = kwargs["output"] / "transcript.txt"
        transcript.parent.mkdir(parents=True, exist_ok=True)
        transcript.write_text("codex deleted most tracked files\n", encoding="utf-8")
        return transcript

    monkeypatch.setattr("curator.pr_repair._run_codex", fake_run_codex)
    monkeypatch.setenv("BROKER_AGENT_ID", "ykm-curator")
    monkeypatch.setenv("BROKER_AGENT_SECRET", "broker-secret")

    results = execute_pr_repairs(
        run_id="run-pr-repair",
        mode="manual_live",
        reconciliations=[
            CuratorPrReconciliation(
                pr_number=5,
                pr_state="changes_requested",
                branch=branch,
                run_id="run-pr-repair",
                reason="owner requested repair",
            )
        ],
        snapshots=[],
        executor="codex_proxy",
        model="ykm-codex-gpt-5-mini",
        validation_command=[sys.executable, "-c", "raise SystemExit(0)"],
        max_repairs=1,
        output=tmp_path / "output",
        broker_remote_url=str(remote),
        codex_proxy_base_url="http://proxy:8092/v1",
        codex_proxy_token="proxy-token",
    )

    assert len(results) == 1
    result = results[0]
    assert result.status == "rejected"
    assert not result.pushed
    assert "blast-radius guardrail" in result.message
    assert "changed files exceeds limit 5" in result.message
    assert len(result.changed_files) == 9

    _git_for_test(["clone", "--branch", branch, str(remote), str(verify)])
    assert (verify / "README.md").read_text(encoding="utf-8") == "readme\n"
    assert (verify / "homemaint" / "manual.md").read_text(encoding="utf-8") == "needs repair\n"


def test_pr_repair_validates_committed_tree_before_push(
    tmp_path: Path,
    monkeypatch,
) -> None:
    branch = "curator/run-pr-repair/upload-upl-20260606"
    remote = tmp_path / "remote.git"
    seed = tmp_path / "seed"
    verify = tmp_path / "verify"
    _git_for_test(["init", "--bare", str(remote)])
    _git_for_test(["clone", str(remote), str(seed)])
    _git_for_test(["checkout", "-b", "main"], cwd=seed)
    (seed / "homemaint").mkdir()
    (seed / "homemaint" / "manual.md").write_text("needs repair\n", encoding="utf-8")
    _git_for_test(["add", "--all"], cwd=seed)
    _git_for_test(
        [
            "-c",
            "user.name=Test Curator",
            "-c",
            "user.email=curator@example.test",
            "commit",
            "-m",
            "Seed corpus",
        ],
        cwd=seed,
    )
    _git_for_test(["push", "origin", "main"], cwd=seed)
    _git_for_test(["checkout", "-b", branch], cwd=seed)
    _git_for_test(["push", "origin", branch], cwd=seed)

    def fake_run_codex(**kwargs) -> Path:
        checkout = kwargs["checkout"]
        (checkout / "homemaint" / "manual.md").unlink()
        (checkout / "homemaint").mkdir(exist_ok=True)
        transcript = kwargs["output"] / "transcript.txt"
        transcript.parent.mkdir(parents=True, exist_ok=True)
        transcript.write_text("codex left only an empty directory\n", encoding="utf-8")
        return transcript

    monkeypatch.setattr("curator.pr_repair._run_codex", fake_run_codex)
    monkeypatch.setenv("BROKER_AGENT_ID", "ykm-curator")
    monkeypatch.setenv("BROKER_AGENT_SECRET", "broker-secret")

    results = execute_pr_repairs(
        run_id="run-pr-repair",
        mode="manual_live",
        reconciliations=[
            CuratorPrReconciliation(
                pr_number=5,
                pr_state="changes_requested",
                branch=branch,
                run_id="run-pr-repair",
                reason="owner requested repair",
            )
        ],
        snapshots=[],
        executor="codex_proxy",
        model="ykm-codex-gpt-5-mini",
        validation_command=[
            sys.executable,
            "-c",
            "from pathlib import Path; raise SystemExit(0 if Path('homemaint').is_dir() else 1)",
        ],
        max_repairs=1,
        output=tmp_path / "output",
        broker_remote_url=str(remote),
        codex_proxy_base_url="http://proxy:8092/v1",
        codex_proxy_token="proxy-token",
    )

    assert len(results) == 1
    result = results[0]
    assert result.status == "validation_failed"
    assert not result.pushed
    assert result.changed_files == ["homemaint/manual.md"]
    assert "validation failed after commit" in result.message

    _git_for_test(["clone", "--branch", branch, str(remote), str(verify)])
    assert (verify / "homemaint" / "manual.md").read_text(encoding="utf-8") == "needs repair\n"


def test_pr_repair_pushes_semantic_changes_when_only_workflow_filter_blocks_validation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    branch = "curator/run-pr-repair/upload-upl-20260606"
    remote = tmp_path / "remote.git"
    seed = tmp_path / "seed"
    verify = tmp_path / "verify"
    _git_for_test(["init", "--bare", str(remote)])
    _git_for_test(["clone", str(remote), str(seed)])
    _git_for_test(["checkout", "-b", "main"], cwd=seed)
    (seed / ".ykm").mkdir()
    (seed / "preferences" / "dev").mkdir(parents=True)
    (seed / ".ykm" / "corpus-policy.yaml").write_text(
        "corpus_roots:\n  - preferences\n",
        encoding="utf-8",
    )
    (seed / "preferences" / "dev" / "dev-environment.md").write_text(
        "---\nid: dev-environment\n---\n# Dev\n",
        encoding="utf-8",
    )
    (seed / "preferences" / "dev" / "uptime-kuma-dashboard.md").write_text(
        "---\nid: uptime-kuma-dashboard\n---\n# Kuma\n",
        encoding="utf-8",
    )
    _git_for_test(["add", "--all"], cwd=seed)
    _git_for_test(
        [
            "-c",
            "user.name=Test Curator",
            "-c",
            "user.email=curator@example.test",
            "commit",
            "-m",
            "Seed corpus",
        ],
        cwd=seed,
    )
    _git_for_test(["push", "origin", "main"], cwd=seed)
    _git_for_test(["checkout", "-b", branch], cwd=seed)
    _git_for_test(["push", "origin", branch], cwd=seed)

    def fake_run_codex(**kwargs) -> Path:
        checkout = kwargs["checkout"]
        (checkout / "dev").mkdir()
        for name in ("dev-environment.md", "uptime-kuma-dashboard.md"):
            (checkout / "dev" / name).write_text(
                (checkout / "preferences" / "dev" / name).read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            (checkout / "preferences" / "dev" / name).unlink()
        (checkout / ".ykm" / "corpus-policy.yaml").write_text(
            "corpus_roots:\n  - dev\n  - preferences\n",
            encoding="utf-8",
        )
        transcript = kwargs["output"] / "transcript.txt"
        transcript.parent.mkdir(parents=True, exist_ok=True)
        transcript.write_text("codex moved files to dev and left workflows untouched\n", encoding="utf-8")
        return transcript

    validation_command = [
        sys.executable,
        "-c",
        (
            "print('Corpus validation: 1 error(s), 0 warning(s)\\n\\nErrors:\\n"
            "- .github/workflows/production-index-artifact.yml: workflow path filters "
            "do not cover corpus root: dev'); raise SystemExit(1)"
        ),
    ]
    monkeypatch.setattr("curator.pr_repair._run_codex", fake_run_codex)
    monkeypatch.setenv("BROKER_AGENT_ID", "ykm-curator")
    monkeypatch.setenv("BROKER_AGENT_SECRET", "broker-secret")

    results = execute_pr_repairs(
        run_id="run-pr-repair",
        mode="manual_live",
        reconciliations=[
            CuratorPrReconciliation(
                pr_number=18,
                pr_state="changes_requested",
                branch=branch,
                run_id="run-pr-repair",
                reason="owner requested dev root",
            )
        ],
        snapshots=[],
        executor="codex_proxy",
        model="ykm-codex-gpt-5-mini",
        validation_command=validation_command,
        max_repairs=1,
        output=tmp_path / "output",
        broker_remote_url=str(remote),
        codex_proxy_base_url="http://proxy:8092/v1",
        codex_proxy_token="proxy-token",
    )

    assert len(results) == 1
    result = results[0]
    assert result.status == "validation_failed"
    assert result.pushed
    assert result.repair_head_sha
    assert result.review_request_comment_status == "pending"
    assert "workflow path-filter update" in (result.review_request_comment or "")
    assert "YKM-Curator-Action-Type: pr_repair_workflow_blocked" in (
        result.review_request_comment or ""
    )
    assert result.changed_files == [
        ".ykm/corpus-policy.yaml",
        "dev/dev-environment.md",
        "dev/uptime-kuma-dashboard.md",
    ]

    _git_for_test(["clone", "--branch", branch, str(remote), str(verify)])
    assert (verify / "dev" / "dev-environment.md").exists()
    assert (verify / "dev" / "uptime-kuma-dashboard.md").exists()
    assert not (verify / "preferences" / "dev" / "dev-environment.md").exists()
    assert not (verify / "preferences" / "dev" / "uptime-kuma-dashboard.md").exists()


def test_pr_repair_blast_radius_override_label_bypasses_size_guard() -> None:
    reconciliation = CuratorPrReconciliation(
        pr_number=5,
        pr_state="changes_requested",
        branch="curator/run-pr-repair/upload-upl-20260606",
        labels=["curator-blast-radius-override"],
        reason="owner approved broad repair",
    )
    snapshot = CuratorPrSnapshot(
        number=5,
        state="open",
        branch="curator/run-pr-repair/upload-upl-20260606",
    )
    delta = RepairDelta(
        changed_files=[f"homemaint/doc-{index}.md" for index in range(6)],
        deleted_files=[],
    )

    assert _has_blast_radius_override(reconciliation, snapshot)
    assert _repair_guardrail_message(
        delta,
        allow_override=_has_blast_radius_override(reconciliation, snapshot),
    ) is None


def test_pr_repair_allows_policy_change_without_exact_path_authorization(
    tmp_path: Path,
    monkeypatch,
) -> None:
    branch = "curator/run-pr-repair/upload-upl-20260606"
    remote = tmp_path / "remote.git"
    seed = tmp_path / "seed"
    verify = tmp_path / "verify"
    _git_for_test(["init", "--bare", str(remote)])
    _git_for_test(["clone", str(remote), str(seed)])
    _git_for_test(["checkout", "-b", "main"], cwd=seed)
    (seed / ".ykm").mkdir()
    (seed / "homemaint").mkdir()
    (seed / ".ykm" / "corpus-policy.yaml").write_text(
        "corpus_roots:\n  - homemaint\n",
        encoding="utf-8",
    )
    (seed / "homemaint" / "manual.md").write_text("ok\n", encoding="utf-8")
    _git_for_test(["add", "--all"], cwd=seed)
    _git_for_test(
        [
            "-c",
            "user.name=Test Curator",
            "-c",
            "user.email=curator@example.test",
            "commit",
            "-m",
            "Seed corpus",
        ],
        cwd=seed,
    )
    _git_for_test(["push", "origin", "main"], cwd=seed)
    _git_for_test(["checkout", "-b", branch], cwd=seed)
    _git_for_test(["push", "origin", branch], cwd=seed)

    def fake_run_codex(**kwargs) -> Path:
        checkout = kwargs["checkout"]
        (checkout / ".ykm" / "corpus-policy.yaml").write_text(
            "corpus_roots:\n  - homemaint\n  - dev\n",
            encoding="utf-8",
        )
        transcript = kwargs["output"] / "transcript.txt"
        transcript.parent.mkdir(parents=True, exist_ok=True)
        transcript.write_text("codex updated named policy file\n", encoding="utf-8")
        return transcript

    monkeypatch.setattr("curator.pr_repair._run_codex", fake_run_codex)
    monkeypatch.setenv("BROKER_AGENT_ID", "ykm-curator")
    monkeypatch.setenv("BROKER_AGENT_SECRET", "broker-secret")

    results = execute_pr_repairs(
        run_id="run-pr-repair",
        mode="manual_live",
        reconciliations=[
            CuratorPrReconciliation(
                pr_number=5,
                pr_state="changes_requested",
                branch=branch,
                run_id="run-pr-repair",
                reason="owner requested repair",
            )
        ],
        snapshots=[
            CuratorPrSnapshot(
                number=5,
                state="open",
                branch=branch,
                review_comments=["Please create a new top-level corpus root called `dev/`."],
            )
        ],
        executor="codex_proxy",
        model="ykm-codex-gpt-5-mini",
        validation_command=[
            sys.executable,
            "-c",
            (
                "from pathlib import Path; "
                "assert '  - dev\\n' in Path('.ykm/corpus-policy.yaml').read_text()"
            ),
        ],
        max_repairs=1,
        output=tmp_path / "output",
        broker_remote_url=str(remote),
        codex_proxy_base_url="http://proxy:8092/v1",
        codex_proxy_token="proxy-token",
    )

    assert len(results) == 1
    result = results[0]
    assert result.status == "pushed"
    assert result.pushed
    assert result.changed_files == [".ykm/corpus-policy.yaml"]
    assert result.validation_returncode == 0

    _git_for_test(["clone", "--branch", branch, str(remote), str(verify)])
    assert (
        verify / ".ykm" / "corpus-policy.yaml"
    ).read_text(encoding="utf-8") == "corpus_roots:\n  - homemaint\n  - dev\n"


