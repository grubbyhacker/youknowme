from __future__ import annotations

import json
import os
import subprocess
import time

import pytest


def test_live_upload_agent_opens_validated_pr_for_path_backed_upload() -> None:
    if os.getenv("YKM_CURATOR_LIVE_UPLOAD_E2E") != "1":
        pytest.skip("set YKM_CURATOR_LIVE_UPLOAD_E2E=1 to run the live upload-agent E2E")
    upload_id = os.getenv("YKM_CURATOR_LIVE_UPLOAD_ID")
    if not upload_id:
        pytest.fail("YKM_CURATOR_LIVE_UPLOAD_ID is required for the live upload-agent E2E")

    host = os.getenv("YKM_CURATOR_LIVE_E2E_HOST", "hermes-vps")
    profile = os.getenv("YKM_CURATOR_LIVE_E2E_PROFILE", "ykm-curator-upload-pr-live")
    run_id = _launch_live_profile(host=host, profile=profile, upload_id=upload_id)
    report = _wait_for_report(host=host, run_id=run_id)

    assert report["status"] == "pass"
    assert report["mode"] == "manual_live"
    assert upload_id in report["included_upload_ids"]
    assert report["upload_review_observation_count"] == 1
    assert report["upload_review_validation_failure_count"] == 0
    assert report["github_mutation_count"] == 1
    assert report["execution_intent_count"] == 1
    assert report["execution_intents"][0]["operation"] == "pull.create"
    assert report["simulated_execution_results"][0]["status"] == "executed"


def _launch_live_profile(*, host: str, profile: str, upload_id: str) -> str:
    payload = json.dumps({"parameters": {"upload_ids": [upload_id]}})
    script = f"""
set -euo pipefail
cd /docker/gh-agent-broker
set -a; . ./.env; set +a
curl -fsS -X POST \
  -H "Authorization: Bearer ${{YKM_CURATOR_SANDBOX_ADMIN_TOKEN}}" \
  -H "Content-Type: application/json" \
  -d {json.dumps(payload)} \
  http://127.0.0.1:8091/v1/launch-profiles/{profile}/launch
"""
    result = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", host, script],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-1000:] or result.stdout[-1000:]
    response = json.loads(result.stdout)
    run_id = response.get("run_id")
    assert isinstance(run_id, str) and run_id
    return run_id


def _wait_for_report(*, host: str, run_id: str) -> dict[str, object]:
    deadline = time.monotonic() + 1800
    while time.monotonic() < deadline:
        result = subprocess.run(
            [
                "ssh",
                "-o",
                "BatchMode=yes",
                host,
                f"cat /srv/sandbox-broker/state/runs/{run_id}/output/run-report.json 2>/dev/null || true",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            payload = json.loads(result.stdout)
            if payload.get("completed_at"):
                return payload
        time.sleep(10)
    pytest.fail(f"timed out waiting for live Curator report for {run_id}")
