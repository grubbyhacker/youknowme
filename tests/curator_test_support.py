from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from curator.models import UploadBundleSnapshot, UploadReviewPreview


def _fake_corpus_checkout(tmp_path: Path) -> Path:
    corpus = tmp_path / "corpus"
    (corpus / ".ykm").mkdir(parents=True)
    (corpus / "homemaint").mkdir()
    (corpus / "scripts").mkdir()
    (corpus / "tests").mkdir()
    (corpus / ".ykm" / "corpus-policy.yaml").write_text(
        (
            "corpus_roots:\n"
            "  - homemaint\n"
            "allowed_types:\n"
            "  - procedure\n"
            "allowed_tags:\n"
            "  - home\n"
            "  - home-maintenance\n"
        ),
        encoding="utf-8",
    )
    (corpus / "mise.toml").write_text("[tasks.validate]\nrun = 'true'\n", encoding="utf-8")
    (corpus / "pyproject.toml").write_text("[project]\nname = 'fake-corpus'\n", encoding="utf-8")
    (corpus / "scripts" / "validate_corpus.py").write_text("print('ok')\n", encoding="utf-8")
    return corpus


def _upload_agent_preview_and_bundle(tmp_path: Path) -> tuple[UploadReviewPreview, UploadBundleSnapshot]:
    pending = tmp_path / "intake" / "uploads" / "pending" / "upl_tooling"
    files = pending / "files"
    files.mkdir(parents=True)
    (pending / "manifest.json").write_text(
        json.dumps({"upload_id": "upl_tooling", "title": "Tooling note"}) + "\n",
        encoding="utf-8",
    )
    (files / "tooling.md").write_text("# Tooling\n\nUse uv for Python dependencies.\n", encoding="utf-8")
    preview = UploadReviewPreview(
        upload_id="upl_tooling",
        queue="pending",
        action_id="upl_act_1",
        idempotency_key="upload:test-agentic-upload",
        current_state="pending",
        proposed_state="claimed",
        branch="curator/run-upload-agent/upload-upl-tooling-test",
        reason="test upload",
        draft_status="model_review_candidate",
    )
    bundle = UploadBundleSnapshot(
        upload_id="upl_tooling",
        queue="pending",
        path=str(pending),
        has_manifest=True,
        manifest_upload_id="upl_tooling",
        has_curator_metadata=False,
    )
    return preview, bundle


def _git_for_test(args: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )


def _fake_mise(
    tmp_path: Path,
    monkeypatch,
    *,
    exit_code: int,
    stdout: str = "",
    stderr: str = "",
) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    script = bin_dir / "mise"
    script.write_text(
        (
            "#!/usr/bin/env python3\n"
            "import sys\n"
            "if sys.argv[1:2] == ['trust']:\n"
            "    raise SystemExit(0)\n"
            f"sys.stdout.write({stdout!r})\n"
            f"sys.stderr.write({stderr!r})\n"
            f"raise SystemExit({exit_code})\n"
        ),
        encoding="utf-8",
    )
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    return script


def _fake_git(tmp_path: Path, monkeypatch) -> Path:
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
            "    (target / '.ykm' / 'corpus-policy.yaml').write_text("
            "\"corpus_roots:\\n  - homemaint\\nallowed_types:\\n  - procedure\\n"
            "allowed_tags:\\n  - home\\n  - home-maintenance\\n\")\n"
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


