from __future__ import annotations

import json
from pathlib import Path


from curator.model_tasks import (
    UploadReviewModelOutput,
)
from curator.models import (
    ModelCallBudget,
)
from curator.upload_observe import (
    apply_upload_review_draft_to_checkout,
    observe_upload_review_draft,
)
from ykm.curator import CuratorDryRunConfig, run_curator_dry_run
from curator.runner import (
    _upload_review_model_request,
)



from tests.curator_test_support import (
    _fake_corpus_checkout,
    _fake_mise,
)


def test_upload_snapshot_reads_curator_metadata(tmp_path: Path, monkeypatch) -> None:
    intake = tmp_path / "intake"
    claimed = intake / "uploads" / "claimed" / "upl_claimed"
    claimed.mkdir(parents=True)
    (claimed / "manifest.json").write_text('{"upload_id":"upl_claimed"}\n', encoding="utf-8")
    (claimed / "curator.json").write_text(
        json.dumps(
            {
                "schema_version": "1",
                "upload_id": "upl_claimed",
                "state": "claimed",
                "decision": "deferred",
                "run_id": "run-old",
                "blocking_reason": "waiting on owner",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    report = run_curator_dry_run(
        CuratorDryRunConfig(run_id="run-upload-meta", intake=intake, output=tmp_path / "output")
    )

    assert report.status == "pass"
    assert report.upload_bundles[0]["upload_id"] == "upl_claimed"
    assert report.upload_bundles[0]["has_manifest"] is True
    assert report.upload_bundles[0]["curator_metadata"]["blocking_reason"] == "waiting on owner"
    assert report.reconciliation["upload_metadata_state_counts"] == {"claimed": 1}


def test_upload_plan_proposes_review_deferrals_without_queue_moves(
    tmp_path: Path, monkeypatch
) -> None:
    intake = tmp_path / "intake"
    pending = intake / "uploads" / "pending" / "upl_pending"
    deferred = intake / "uploads" / "deferred" / "upl_deferred"
    archive = intake / "uploads" / "archive" / "upl_archived"
    pr_opened = intake / "uploads" / "claimed" / "upl_pr_opened"
    for path in (pending, deferred, archive, pr_opened):
        path.mkdir(parents=True)
        (path / "manifest.json").write_text(json.dumps({"upload_id": path.name}) + "\n")
    (pr_opened / "curator.json").write_text(
        json.dumps(
            {
                "schema_version": "1",
                "upload_id": "upl_pr_opened",
                "state": "pr_opened",
                "decision": "integrated",
                "run_id": "old",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    report = run_curator_dry_run(
        CuratorDryRunConfig(run_id="run-upload-plan", intake=intake, output=tmp_path / "output")
    )
    plan = json.loads(
        (intake / "uploads" / "runs" / "run-upload-plan" / "upload-plan.json").read_text(
            encoding="utf-8"
        )
    )

    assert report.status == "pass"
    assert report.included_upload_ids == ["upl_pending", "upl_deferred"]
    assert report.upload_proposed_action_count == 2
    assert report.upload_review_preview_count == 2
    assert len(report.upload_plan_paths) == 2
    assert plan["included_upload_ids"] == ["upl_pending", "upl_deferred"]
    assert [action["action_type"] for action in plan["proposed_actions"]] == ["defer", "defer"]
    assert [preview["upload_id"] for preview in plan["review_previews"]] == [
        "upl_pending",
        "upl_deferred",
    ]
    assert plan["review_previews"][0]["current_state"] == "pending"
    assert plan["review_previews"][0]["proposed_state"] == "claimed"
    assert plan["review_previews"][0]["idempotency_key"].startswith("upload:")
    assert plan["review_previews"][0]["branch"].startswith("curator/run-upload-plan/upload-upl-pending-")
    assert report.upload_review_previews == plan["review_previews"]
    markdown = (tmp_path / "output" / "run-report.md").read_text(encoding="utf-8")
    assert "## Upload Review Previews" in markdown
    assert pending.exists()
    assert deferred.exists()
    assert archive.exists()
    assert pr_opened.exists()


def test_upload_plan_can_be_scoped_by_task_upload_ids(tmp_path: Path, monkeypatch) -> None:
    intake = tmp_path / "intake"
    for upload_id in ("upl_first", "upl_second"):
        pending = intake / "uploads" / "pending" / upload_id
        pending.mkdir(parents=True)
        (pending / "manifest.json").write_text(
            json.dumps({"upload_id": upload_id}) + "\n",
            encoding="utf-8",
        )
    task = tmp_path / "task.json"
    task.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "run_id": "run-upload-scope",
                "mode": "dry_run",
                "enabled_actions": ["plan_uploads"],
                "upload_ids": ["upl_second"],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    report = run_curator_dry_run(
        CuratorDryRunConfig(
            run_id="ignored",
            intake=intake,
            output=tmp_path / "output",
            task=task,
        )
    )

    assert report.status == "pass"
    assert report.included_upload_ids == ["upl_second"]
    assert [preview["upload_id"] for preview in report.upload_review_previews] == ["upl_second"]
    assert any(probe.name == "upload-scope" and probe.status == "pass" for probe in report.probes)


def test_upload_plan_scope_fails_closed_for_missing_upload(tmp_path: Path, monkeypatch) -> None:
    intake = tmp_path / "intake"
    pending = intake / "uploads" / "pending" / "upl_present"
    pending.mkdir(parents=True)
    (pending / "manifest.json").write_text(
        json.dumps({"upload_id": "upl_present"}) + "\n",
        encoding="utf-8",
    )
    task = tmp_path / "task.json"
    task.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "run_id": "run-upload-scope-missing",
                "mode": "dry_run",
                "enabled_actions": ["plan_uploads"],
                "model_upload_review": True,
                "upload_review_model": "anthropic/claude-sonnet-4.6",
                "model_call_budget": {"max_calls_per_run": 1, "max_tokens_per_run": 1000},
                "upload_ids": ["upl_missing"],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    report = run_curator_dry_run(
        CuratorDryRunConfig(
            run_id="ignored",
            intake=intake,
            output=tmp_path / "output",
            task=task,
        )
    )

    assert report.status == "fail"
    assert report.included_upload_ids == []
    assert report.upload_review_preview_count == 0
    assert report.model_call_count == 0
    probe = next(probe for probe in report.probes if probe.name == "upload-scope")
    assert probe.status == "fail"
    assert probe.details["upload_ids"] == ["upl_missing"]


def test_upload_plan_marks_corpus_ready_upload_draft(tmp_path: Path, monkeypatch) -> None:
    intake = tmp_path / "intake"
    pending = intake / "uploads" / "pending" / "upl_hot_tub"
    files = pending / "files"
    files.mkdir(parents=True)
    (pending / "manifest.json").write_text(
        json.dumps({"upload_id": "upl_hot_tub"}) + "\n",
        encoding="utf-8",
    )
    (files / "hot-tub-note.md").write_text(
        """---
id: hot-tub-note
type: procedure
tags: [home-maintenance, hot-tub]
---

# Hot Tub Note

Use the documented maintenance procedure.
""",
        encoding="utf-8",
    )
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    report = run_curator_dry_run(
        CuratorDryRunConfig(run_id="run-upload-draft", intake=intake, output=tmp_path / "output")
    )
    preview = report.upload_review_previews[0]

    assert preview["draft_status"] == "corpus_pr_candidate"
    assert preview["draft_paths"] == ["homemaint/hot-tub-note.md"]
    assert preview["blocking_reason"] is None
    assert preview["warnings"] == []
    markdown = (tmp_path / "output" / "run-report.md").read_text(encoding="utf-8")
    assert "draft `corpus_pr_candidate` -> `homemaint/hot-tub-note.md`" in markdown


def test_upload_plan_marks_unknown_vocabulary_for_model_review(
    tmp_path: Path, monkeypatch
) -> None:
    intake = tmp_path / "intake"
    pending = intake / "uploads" / "pending" / "upl_birthdays"
    files = pending / "files"
    files.mkdir(parents=True)
    (pending / "manifest.json").write_text(
        json.dumps({"upload_id": "upl_birthdays"}) + "\n",
        encoding="utf-8",
    )
    (files / "important-birthdays.md").write_text(
        """---
id: important-birthdays
type: preference
tags: [birthday, personal, reminder]
---

# Important Birthdays

Remember these birthdays for future reminders.
""",
        encoding="utf-8",
    )
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    report = run_curator_dry_run(
        CuratorDryRunConfig(run_id="run-upload-model-needed", intake=intake, output=tmp_path / "output")
    )
    preview = report.upload_review_previews[0]

    assert preview["draft_status"] == "model_review_candidate"
    assert preview["draft_paths"] == []
    assert preview["blocking_reason"] == (
        "important-birthdays.md: unsupported frontmatter type: preference"
    )
    markdown = (tmp_path / "output" / "run-report.md").read_text(encoding="utf-8")
    assert "draft `model_review_candidate`: important-birthdays.md: unsupported" in markdown


def test_upload_plan_marks_invalid_id_as_needing_owner_action(
    tmp_path: Path, monkeypatch
) -> None:
    intake = tmp_path / "intake"
    pending = intake / "uploads" / "pending" / "upl_invalid_id"
    files = pending / "files"
    files.mkdir(parents=True)
    (pending / "manifest.json").write_text(
        json.dumps({"upload_id": "upl_invalid_id"}) + "\n",
        encoding="utf-8",
    )
    (files / "bad-id.md").write_text(
        """---
id: Bad ID
type: procedure
tags: [home]
---

# Bad ID
""",
        encoding="utf-8",
    )
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    report = run_curator_dry_run(
        CuratorDryRunConfig(run_id="run-upload-blocked", intake=intake, output=tmp_path / "output")
    )
    preview = report.upload_review_previews[0]

    assert preview["draft_status"] == "needs_owner_action"
    assert preview["draft_paths"] == []
    assert preview["blocking_reason"] == "bad-id.md: invalid frontmatter id"
    markdown = (tmp_path / "output" / "run-report.md").read_text(encoding="utf-8")
    assert "draft `needs_owner_action`: bad-id.md: invalid frontmatter id" in markdown


def test_upload_review_observe_applies_model_draft_to_temp_checkout_and_validates(
    tmp_path: Path,
    monkeypatch,
) -> None:
    corpus = _fake_corpus_checkout(tmp_path)
    _fake_mise(tmp_path, monkeypatch, exit_code=0, stdout="validated draft\n")
    output = UploadReviewModelOutput(
        upload_id="upl_tooling",
        decision="integrated",
        files=[
            {
                "path": "homemaint/tooling-note.md",
                "content": (
                    "---\n"
                    "id: tooling-note\n"
                    "type: procedure\n"
                    "tags: [home-maintenance, uv]\n"
                    "---\n\n"
                    "# Tooling Note\n"
                ),
            }
        ],
        policy_patch={"allowed_types_add": [], "allowed_tags_add": ["uv"]},
        content_summary="A tooling note about uv usage.",
        rationale="The note is normalized into corpus markdown.",
        reason="ready for validation",
    )

    observation = observe_upload_review_draft(
        corpus_checkout=corpus,
        output=output,
        action_id="upl_act_1",
    )

    assert observation.status == "pass"
    assert observation.command == ["mise", "run", "validate"]
    assert observation.returncode == 0
    assert observation.draft_paths == ["homemaint/tooling-note.md"]
    assert observation.policy_tags_add == ["uv"]
    assert "validated draft" in observation.stdout_tail
    assert not (corpus / "homemaint" / "tooling-note.md").exists()


def test_upload_review_policy_patch_can_add_corpus_root_type_and_tag(tmp_path: Path) -> None:
    corpus = _fake_corpus_checkout(tmp_path)
    output = UploadReviewModelOutput(
        upload_id="upl_project",
        decision="integrated",
        files=[
            {
                "path": "projects/vps-hardening.md",
                "content": (
                    "---\n"
                    "id: vps-hardening\n"
                    "type: project\n"
                    "tags: [vps]\n"
                    "---\n\n"
                    "# VPS Hardening\n"
                ),
            }
        ],
        policy_patch={
            "corpus_roots_add": ["projects"],
            "allowed_types_add": ["project"],
            "allowed_tags_add": ["vps"],
        },
        content_summary="A VPS hardening project note.",
        rationale="A review PR can ask for the bounded schema addition.",
        reason="ready for validation",
    )

    paths = apply_upload_review_draft_to_checkout(corpus, output)

    assert paths == ["projects/vps-hardening.md"]
    policy = (corpus / ".ykm" / "corpus-policy.yaml").read_text(encoding="utf-8")
    assert "corpus_roots:\n  - homemaint\n  - projects\n" in policy
    assert "allowed_types:\n  - procedure\n  - project\n" in policy
    assert "allowed_tags:\n  - home\n  - home-maintenance\n  - vps\n" in policy
    assert (corpus / "projects" / "vps-hardening.md").exists()


def test_upload_review_observe_records_validation_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    corpus = _fake_corpus_checkout(tmp_path)
    _fake_mise(tmp_path, monkeypatch, exit_code=7, stderr="bad frontmatter\n")
    output = UploadReviewModelOutput(
        upload_id="upl_bad",
        decision="integrated",
        files=[
            {
                "path": "homemaint/bad-note.md",
                "content": "---\nid: bad-note\ntype: procedure\ntags: [home]\n---\n\n# Bad\n",
            }
        ],
        policy_patch={"allowed_types_add": [], "allowed_tags_add": []},
        content_summary="A malformed draft note for validation failure coverage.",
        rationale="The note is normalized into corpus markdown.",
        reason="ready for validation",
    )

    observation = observe_upload_review_draft(corpus_checkout=corpus, output=output)

    assert observation.status == "fail"
    assert observation.returncode == 7
    assert observation.message == "corpus validation failed"
    assert "bad frontmatter" in observation.stderr_tail


def test_upload_review_model_prompt_requires_policy_patch_for_new_metadata(
    tmp_path: Path,
) -> None:
    bundle_path = tmp_path / "upl_writing"
    files = bundle_path / "files"
    files.mkdir(parents=True)
    (bundle_path / "manifest.json").write_text(
        json.dumps({"upload_id": "upl_writing"}) + "\n",
        encoding="utf-8",
    )
    (files / "summary.md").write_text(
        "---\nid: narrow-pipe-summary\ntype: writing-sample\ntags: [writing]\n"
        "related: [the-narrow-pipe]\n---\n\n# Summary\n",
        encoding="utf-8",
    )
    bundle = type("Bundle", (), {"upload_id": "upl_writing", "path": bundle_path})()

    request = _upload_review_model_request(
        run_id="run-upload-model",
        model="anthropic/claude-sonnet-4.6",
        model_call_budget=ModelCallBudget(max_calls_per_run=1, max_tokens_per_run=1000),
        bundle=bundle,
        curator_guidance="Never create backup files in git.",
    )

    user_message = request.input["messages"][1]
    prompt_input = json.loads(user_message["content"])
    constraints = prompt_input["constraints"]
    assert (
        "Every frontmatter type must already be in corpus_policy.allowed_types or be listed in "
        "policy_patch.allowed_types_add."
    ) in constraints
    assert (
        "Every frontmatter tag must already be in corpus_policy.allowed_tags or be listed in "
        "policy_patch.allowed_tags_add."
    ) in constraints
    assert (
        "Every output path must start with an existing corpus_policy.corpus_roots value or one "
        "listed in policy_patch.corpus_roots_add."
    ) in constraints
    assert (
        "Choose corpus roots semantically. Use `dev/` for development environment, personal "
        "production infrastructure, software operations, and service runbooks; do not place that "
        "material under `preferences/` merely because it describes Roger."
    ) in constraints
    assert "Use `preferences/` only for stable preferences, defaults, tastes, and communication style." in constraints
    assert (
        "Do not invent related IDs. Only include related when the upload itself names an exact "
        "existing corpus id; otherwise mention the relationship in prose instead."
    ) in constraints
    assert "Never create backup, temporary, swap, `.bak`, `.orig`, `.rej`, or `~` files in git." in constraints
    assert prompt_input["curator_guidance"] == "Never create backup files in git."


def test_upload_plan_reenters_deferred_metadata_only_when_trigger_is_ready(
    tmp_path: Path,
    monkeypatch,
) -> None:
    intake = tmp_path / "intake"
    ready = intake / "uploads" / "deferred" / "upl_ready"
    past = intake / "uploads" / "deferred" / "upl_past"
    future = intake / "uploads" / "deferred" / "upl_future"
    blocked = intake / "uploads" / "deferred" / "upl_blocked"
    for path in (ready, past, future, blocked):
        path.mkdir(parents=True)
        (path / "manifest.json").write_text(json.dumps({"upload_id": path.name}) + "\n")
    metadata_by_upload = {
        "upl_ready": {"reentry_trigger": "next_run"},
        "upl_past": {
            "reentry_trigger": "retry_after",
            "retry_after": "2026-01-01T00:00:00Z",
        },
        "upl_future": {
            "reentry_trigger": "retry_after",
            "retry_after": "2999-01-01T00:00:00Z",
        },
        "upl_blocked": {"reentry_trigger": "owner_input_resolved"},
    }
    for path in (ready, past, future, blocked):
        metadata = {
            "schema_version": "1",
            "upload_id": path.name,
            "state": "deferred",
            "decision": "deferred",
            "run_id": "run-old",
            **metadata_by_upload[path.name],
        }
        (path / "curator.json").write_text(json.dumps(metadata) + "\n", encoding="utf-8")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    report = run_curator_dry_run(
        CuratorDryRunConfig(run_id="run-upload-reentry", intake=intake, output=tmp_path / "output")
    )

    assert report.status == "pass"
    assert report.included_upload_ids == ["upl_past", "upl_ready"]
    assert report.upload_proposed_action_count == 2
    assert [preview["current_state"] for preview in report.upload_review_previews] == [
        "deferred",
        "deferred",
    ]
    assert future.exists()
    assert blocked.exists()


def test_enabled_actions_can_disable_upload_planning(tmp_path: Path, monkeypatch) -> None:
    intake = tmp_path / "intake"
    pending = intake / "uploads" / "pending" / "upl_pending"
    pending.mkdir(parents=True)
    task = tmp_path / "task.json"
    task.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "run_id": "run-no-upload-plan",
                "mode": "dry_run",
                "enabled_actions": ["reconcile", "plan_feedback"],
                "github_mutation_budget": {
                    "max_new_objects_per_run": 0,
                    "upload": 0,
                    "feedback": 0,
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    report = run_curator_dry_run(
        CuratorDryRunConfig(run_id="ignored", intake=intake, output=tmp_path / "output", task=task)
    )
    plan = json.loads(
        (intake / "uploads" / "runs" / "run-no-upload-plan" / "upload-plan.json").read_text(
            encoding="utf-8"
        )
    )

    assert report.upload_queue_counts["pending"] == 1
    assert report.included_upload_ids == []
    assert report.upload_proposed_action_count == 0
    assert report.upload_review_preview_count == 0
    assert plan["included_upload_ids"] == []


def test_invalid_upload_curator_metadata_is_reported(tmp_path: Path, monkeypatch) -> None:
    intake = tmp_path / "intake"
    deferred = intake / "uploads" / "deferred" / "upl_bad"
    deferred.mkdir(parents=True)
    (deferred / "curator.json").write_text('{"upload_id":"upl_bad"}\n', encoding="utf-8")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    report = run_curator_dry_run(
        CuratorDryRunConfig(run_id="run-upload-bad", intake=intake, output=tmp_path / "output")
    )

    assert report.status == "fail"
    assert report.validation_failure_count == 1
    assert report.partial_failures[0]["name"] == "upload-metadata"
    assert report.upload_bundles[0]["metadata_error"]


def test_upload_curator_metadata_retry_after_without_timestamp_is_reported(
    tmp_path: Path, monkeypatch
) -> None:
    intake = tmp_path / "intake"
    deferred = intake / "uploads" / "deferred" / "upl_bad_retry"
    deferred.mkdir(parents=True)
    (deferred / "curator.json").write_text(
        json.dumps(
            {
                "schema_version": "1",
                "upload_id": "upl_bad_retry",
                "state": "deferred",
                "run_id": "old-run",
                "reentry_trigger": "retry_after",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    report = run_curator_dry_run(
        CuratorDryRunConfig(run_id="run-upload-bad-retry", intake=intake, output=tmp_path / "output")
    )

    assert report.status == "fail"
    assert report.validation_failure_count == 1
    assert "retry_after reentry trigger requires" in report.upload_bundles[0]["metadata_error"]
    assert any(failure["name"] == "upload-metadata" for failure in report.partial_failures)


def test_upload_manifest_and_curator_metadata_id_mismatch_is_reported(
    tmp_path: Path, monkeypatch
) -> None:
    intake = tmp_path / "intake"
    claimed = intake / "uploads" / "claimed" / "upl_bundle"
    claimed.mkdir(parents=True)
    (claimed / "manifest.json").write_text(
        json.dumps({"upload_id": "upl_manifest"}) + "\n",
        encoding="utf-8",
    )
    (claimed / "curator.json").write_text(
        json.dumps(
            {
                "schema_version": "1",
                "upload_id": "upl_metadata",
                "state": "claimed",
                "run_id": "old-run",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    report = run_curator_dry_run(
        CuratorDryRunConfig(run_id="run-upload-id-mismatch", intake=intake, output=tmp_path / "output")
    )

    assert report.status == "fail"
    assert report.validation_failure_count == 1
    assert report.included_upload_ids == []
    assert report.upload_review_preview_count == 0
    assert "does not match manifest" in report.upload_bundles[0]["metadata_error"]
    assert any(failure["name"] == "upload-metadata" for failure in report.partial_failures)


def test_invalid_upload_manifest_is_reported_and_excluded_from_upload_plan(
    tmp_path: Path, monkeypatch
) -> None:
    intake = tmp_path / "intake"
    bad = intake / "uploads" / "pending" / "upl_bad_manifest"
    good = intake / "uploads" / "pending" / "upl_good_manifest"
    bad.mkdir(parents=True)
    good.mkdir(parents=True)
    (bad / "manifest.json").write_text('{"upload_id":\n', encoding="utf-8")
    (good / "manifest.json").write_text(
        json.dumps({"upload_id": "upl_good_manifest"}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    report = run_curator_dry_run(
        CuratorDryRunConfig(run_id="run-upload-manifest", intake=intake, output=tmp_path / "output")
    )
    plan = json.loads(
        (tmp_path / "output" / "uploads" / "runs" / "run-upload-manifest" / "upload-plan.json")
        .read_text(encoding="utf-8")
    )

    assert report.status == "fail"
    assert report.validation_failure_count == 1
    assert report.included_upload_ids == ["upl_good_manifest"]
    assert plan["included_upload_ids"] == ["upl_good_manifest"]
    bad_bundle = next(bundle for bundle in report.upload_bundles if bundle["upload_id"] == "upl_bad_manifest")
    assert bad_bundle["manifest_error"]
    assert any(failure["name"] == "upload-manifest" for failure in report.partial_failures)
    markdown = (tmp_path / "output" / "run-report.md").read_text(encoding="utf-8")
    assert "## Upload Manifest Errors" in markdown


