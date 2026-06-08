from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from ykm.github_artifacts import (
    GitHubActionsClient,
    GitHubArtifactError,
    artifact_matches_current_index,
    create_installation_token,
    promote_artifact,
    read_build_report_from_artifact_zip,
    read_manifest,
    select_latest_index_artifact,
)


CURRENT_EXIT_CODE = 10


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Download the newest official ykmcorpus index artifact and optionally promote it."
    )
    parser.add_argument("--repo", default="grubbyhacker/ykmcorpus")
    parser.add_argument("--branch", default="main")
    parser.add_argument("--event", default="push")
    parser.add_argument("--workflow-name", default="Production index artifact")
    parser.add_argument("--artifact-prefix", default="youknowme-index-")
    parser.add_argument("--app-id", default=os.getenv("YKM_GITHUB_APP_ID"))
    parser.add_argument("--installation-id", default=os.getenv("YKM_GITHUB_INSTALLATION_ID"))
    parser.add_argument("--private-key", default=os.getenv("YKM_GITHUB_PRIVATE_KEY_PATH"))
    parser.add_argument("--api-url", default=os.getenv("YKM_GITHUB_API_URL", "https://api.github.com"))
    parser.add_argument("--out-dir", type=Path, default=Path("/opt/youknowme/incoming"))
    parser.add_argument("--deploy-root", type=Path, default=Path("/opt/youknowme"))
    parser.add_argument(
        "--artifact-path-file",
        type=Path,
        help="Write the downloaded artifact ZIP path here when the artifact is newer than current.",
    )
    parser.add_argument(
        "--exit-code-current",
        action="store_true",
        help=f"Exit {CURRENT_EXIT_CODE} when the latest artifact already matches index-current.",
    )
    parser.add_argument(
        "--promote-script",
        type=Path,
        default=Path("/opt/youknowme/bin/relaunch-container-with-new-index.sh"),
    )
    parser.add_argument(
        "--promote",
        action="store_true",
        help="Invoke the promotion script after downloading a newer artifact.",
    )
    parser.add_argument("--sudo", action="store_true", help="Run the promotion script through sudo.")
    parser.add_argument(
        "--promote-arg",
        action="append",
        default=[],
        help="Additional argument forwarded to the promotion script. Repeat for multiple args.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Download and promote even if the artifact matches index-current.",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    try:
        return run(args)
    except (GitHubArtifactError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def run(args: argparse.Namespace) -> int:
    _require(args.app_id, "--app-id or YKM_GITHUB_APP_ID")
    _require(args.installation_id, "--installation-id or YKM_GITHUB_INSTALLATION_ID")
    _require(args.private_key, "--private-key or YKM_GITHUB_PRIVATE_KEY_PATH")
    private_key_path = Path(args.private_key).expanduser()
    private_key_pem = private_key_path.read_text(encoding="utf-8")

    if args.artifact_path_file and args.artifact_path_file.exists():
        args.artifact_path_file.unlink()

    token = create_installation_token(
        app_id=args.app_id,
        installation_id=args.installation_id,
        private_key_pem=private_key_pem,
        api_url=args.api_url,
    )

    client = GitHubActionsClient(token=token, api_url=args.api_url)
    try:
        selection = select_latest_index_artifact(
            client=client,
            repo=args.repo,
            branch=args.branch,
            event=args.event or None,
            workflow_name=args.workflow_name or None,
            artifact_prefix=args.artifact_prefix,
        )
        target = args.out_dir / f"{selection.artifact_name}.zip"

        print(
            "selected artifact "
            f"{selection.artifact_name} from run {selection.run_id} at {selection.head_sha}"
        )

        current_manifest = read_manifest(args.deploy_root / "index-current")
        if not args.force and workflow_head_matches_current_index(
            head_sha=selection.head_sha,
            current_manifest=current_manifest,
        ):
            print(f"current index already matches latest successful workflow head {selection.head_sha}")
            return CURRENT_EXIT_CODE if args.exit_code_current else 0

        if not args.dry_run:
            client.download_artifact_zip(
                repo=args.repo,
                artifact_id=selection.artifact_id,
                out=target,
            )
        elif not target.exists():
            print(f"dry run: would download to {target}")
            return 0
    finally:
        client.close()

    if args.dry_run:
        print(f"dry run: existing local artifact would be inspected at {target}")
        return 0

    build_report = read_build_report_from_artifact_zip(target)
    current_manifest = read_manifest(args.deploy_root / "index-current")
    if not args.force and artifact_matches_current_index(
        build_report=build_report,
        current_manifest=current_manifest,
    ):
        manifest = build_report["manifest"]
        print(
            "current index already matches artifact "
            f"{manifest['source_commit']}-{manifest['build_id']}"
        )
        return CURRENT_EXIT_CODE if args.exit_code_current else 0

    if args.artifact_path_file:
        args.artifact_path_file.parent.mkdir(parents=True, exist_ok=True)
        args.artifact_path_file.write_text(f"{target}\n", encoding="utf-8")

    if not args.promote:
        print(f"downloaded newer artifact: {target}")
        print("promotion skipped; rerun with --promote to deploy it")
        return 0

    promote_artifact(
        artifact_zip=target,
        promote_script=args.promote_script,
        sudo=args.sudo,
        extra_args=args.promote_arg,
    )
    return 0


def _require(value: str | None, name: str) -> None:
    if not value:
        raise ValueError(f"{name} is required")


def workflow_head_matches_current_index(
    *,
    head_sha: str,
    current_manifest: dict[str, object] | None,
) -> bool:
    if current_manifest is None:
        return False
    return current_manifest.get("source_commit") == head_sha
