from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from ykm.contracts import FeedbackRequest, UploadFileInput, UploadRequest
from ykm.intake import IntakeError, IntakeStore


def upload_request(*files: tuple[str, str]) -> UploadRequest:
    return UploadRequest(
        files=[
            UploadFileInput(filename=filename, content=content)
            for filename, content in files
        ],
        purpose="capture useful agent-curated context",
        suggested_type="skill",
        suggested_tags=["Agent-Guidance", "markdown"],
    )


def test_stage_upload_writes_pending_bundle(tmp_path: Path) -> None:
    store = IntakeStore(tmp_path / "intake")

    response = store.stage_upload(
        upload_request(
            (
                "YKM Topic Skill.md",
                """---
id: ykm-topic-skill
type: skill
tags: [youknowme, markdown]
---

# How to Write Durable YouKnowMe Markdown

Use clear headings and label uncertainty.
""",
            )
        ),
        build_id="build-123",
        auth_path="mcp",
    )

    staged_dir = tmp_path / "intake" / response.staged_path
    manifest = json.loads((staged_dir / "manifest.json").read_text(encoding="utf-8"))

    assert response.accepted is True
    assert response.file_count == 1
    assert response.total_bytes > 0
    assert manifest["schema_version"] == "1"
    assert manifest["status"] == "pending"
    assert manifest["build_id"] == "build-123"
    assert manifest["auth_path"] == "mcp"
    assert manifest["suggested_tags"] == ["agent-guidance", "markdown"]
    assert manifest["files"][0]["original_filename"] == "YKM Topic Skill.md"
    assert manifest["files"][0]["stored_filename"] == "ykm-topic-skill.md"
    assert (staged_dir / "files" / "ykm-topic-skill.md").exists()


def test_stage_upload_deduplicates_normalized_filenames(tmp_path: Path) -> None:
    store = IntakeStore(tmp_path / "intake")

    response = store.stage_upload(
        upload_request(("Topic Skill.md", "# One\n"), ("topic-skill.md", "# Two\n")),
        build_id=None,
    )

    staged_dir = tmp_path / "intake" / response.staged_path / "files"
    assert (staged_dir / "topic-skill.md").exists()
    assert (staged_dir / "topic-skill-2.md").exists()


def test_stage_upload_warns_on_unsupported_frontmatter(tmp_path: Path) -> None:
    store = IntakeStore(tmp_path / "intake")

    response = store.stage_upload(
        upload_request(
            (
                "note.md",
                """---
id: test-note
priority: high
---

# Note
""",
            )
        ),
        build_id=None,
    )

    assert response.warnings == [
        "note.md: unsupported frontmatter fields preserved for review: priority"
    ]


def test_stage_upload_rejects_more_than_ten_files() -> None:
    with pytest.raises(ValidationError):
        upload_request(*[(f"file-{index}.md", "# Note\n") for index in range(11)])


def test_stage_upload_rejects_oversized_file(tmp_path: Path) -> None:
    store = IntakeStore(tmp_path / "intake")

    with pytest.raises(IntakeError, match="exceeds 20480 bytes"):
        store.stage_upload(upload_request(("large.md", "# Large\n" + ("x" * 21000))), build_id=None)


def test_stage_upload_rejects_oversized_total(tmp_path: Path) -> None:
    store = IntakeStore(tmp_path / "intake")

    with pytest.raises(IntakeError, match="upload exceeds 81920 total bytes"):
        store.stage_upload(
            upload_request(
                *[
                    (f"file-{index}.md", "# Note\n" + ("x" * 9000))
                    for index in range(10)
                ]
            ),
            build_id=None,
        )


@pytest.mark.parametrize(
    ("filename", "content", "message"),
    [
        ("../secret.md", "# Note\n", "simple markdown filename"),
        ("note.txt", "# Note\n", "only .md files"),
        ("note.md", "-----BEGIN PRIVATE KEY-----\nsecret\n", "rejected: matched"),
        ("note.md", "<script>alert('x')</script>\n", "unsupported HTML"),
        ("note.md", "# Bad\x00Note\n", "NUL bytes"),
    ],
)
def test_stage_upload_rejects_unsafe_input(
    tmp_path: Path, filename: str, content: str, message: str
) -> None:
    store = IntakeStore(tmp_path / "intake")

    with pytest.raises(IntakeError, match=message):
        store.stage_upload(upload_request((filename, content)), build_id=None)


def test_record_feedback_appends_bounded_jsonl(tmp_path: Path) -> None:
    store = IntakeStore(tmp_path / "intake")

    response = store.record_feedback(
        FeedbackRequest(
            category="missing_content",
            comment="I needed the exact SKU and it was not in the returned note.",
            source_id="hottub-home",
            section_id="section-1",
            result_ids=["result-1"],
        ),
        build_id="build-123",
        auth_path="mcp",
    )

    path = tmp_path / "intake" / response.path
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert response.accepted is True
    assert payload["event"] == "feedback"
    assert payload["build_id"] == "build-123"
    assert payload["category"] == "missing_content"
    assert payload["source_id"] == "hottub-home"
    assert "token" not in payload


@pytest.mark.parametrize(
    "category",
    [
        "missing_content",
        "wrong_content",
        "stale_content",
        "unclear_content",
        "agent_note",
        "needs_owner_action",
        "positive_content",
        "non_actionable",
    ],
)
def test_feedback_categories_accept_curator_additions(category: str) -> None:
    request = FeedbackRequest(category=category, comment="bounded observation")

    assert request.category == category


def test_feedback_comment_is_bounded() -> None:
    with pytest.raises(ValidationError):
        FeedbackRequest(category="agent_note", comment="x" * 2001)
