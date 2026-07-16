#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'USAGE'
Usage: scripts/prod-feedback-status.sh [feedback-id] [options]

Read-only production diagnostic for YouKnowMe feedback intake and Curator status.

Options:
  --host HOST          SSH host (default: YKM_PROD_SSH_HOST or 100.66.40.39)
  --ssh-user USER      SSH user (default: YKM_PROD_SSH_USER or github-deployer)
  --identity PATH      SSH private key (default: YKM_PROD_SSH_IDENTITY or ~/.ssh/hermes-deploy)
USAGE
}

host="${YKM_PROD_SSH_HOST:-100.66.40.39}"
ssh_user="${YKM_PROD_SSH_USER:-github-deployer}"
identity="${YKM_PROD_SSH_IDENTITY:-$HOME/.ssh/hermes-deploy}"
feedback_id=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host)
      [[ $# -ge 2 ]] || { usage; exit 2; }
      host="$2"
      shift 2
      ;;
    --ssh-user)
      [[ $# -ge 2 ]] || { usage; exit 2; }
      ssh_user="$2"
      shift 2
      ;;
    --identity)
      [[ $# -ge 2 ]] || { usage; exit 2; }
      identity="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    -*)
      usage
      exit 2
      ;;
    *)
      [[ -z "$feedback_id" ]] || { usage; exit 2; }
      feedback_id="$1"
      shift
      ;;
  esac
done

if [[ -n "$feedback_id" && ! "$feedback_id" =~ ^fb_[0-9]{8}_[0-9]{6}_[0-9a-f]{8}$ ]]; then
  echo "invalid feedback id: $feedback_id" >&2
  exit 2
fi

feedback_arg="${feedback_id:--}"

[[ -r "$identity" ]] || {
  echo "readable SSH identity required; set --identity or YKM_PROD_SSH_IDENTITY: $identity" >&2
  exit 2
}

ssh -o BatchMode=yes -o IdentitiesOnly=yes -i "$identity" "$ssh_user@$host" python3 - "$feedback_arg" <<'PY'
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


requested_feedback_id = None if sys.argv[1] == "-" else sys.argv[1]

intake_root = Path("/docker/youknowme/data/intake")
feedback_path = intake_root / "feedback" / "feedback.jsonl"
decisions_path = intake_root / "feedback" / "curator-decisions.jsonl"
status_path = intake_root / "curator-status.json"
broker_runs_helper = (
    "sudo",
    "-n",
    "/usr/local/libexec/ykm-curator-run-reports",
    "--limit",
    "5",
)


def read_jsonl(path: Path) -> list[tuple[int, dict[str, object]]]:
    if not path.exists():
        return []
    records: list[tuple[int, dict[str, object]]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                payload = {"_invalid_json": str(exc), "_raw": line.rstrip("\n")}
            if isinstance(payload, dict):
                records.append((line_number, payload))
            else:
                records.append((line_number, {"_invalid_record": payload}))
    return records


feedback_records = read_jsonl(feedback_path)
decision_records = read_jsonl(decisions_path)

latest_decisions: dict[str, dict[str, object]] = {}
for line_number, decision in decision_records:
    feedback_id = decision.get("feedback_id")
    if isinstance(feedback_id, str):
        latest_decisions[feedback_id] = {"line": line_number, "record": decision}

feedback_ids = [
    record.get("feedback_id")
    for _, record in feedback_records
    if isinstance(record.get("feedback_id"), str)
]
undecided_ids = [
    feedback_id for feedback_id in feedback_ids if feedback_id not in latest_decisions
]

requested = None
if requested_feedback_id:
    requested = {
        "found": False,
        "feedback": None,
        "decision": latest_decisions.get(requested_feedback_id),
    }
    for line_number, record in feedback_records:
        if record.get("feedback_id") == requested_feedback_id:
            requested["found"] = True
            requested["feedback"] = {"line": line_number, "record": record}
            break

last_status = None
if status_path.exists():
    try:
        last_status = json.loads(status_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        last_status = {"_invalid_json": str(exc)}

def recent_curator_runs() -> dict[str, object]:
    """Read broker reports through the narrowly-scoped managed API helper."""
    try:
        completed = subprocess.run(
            broker_runs_helper,
            capture_output=True,
            check=False,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return {
            "availability": "inaccessible",
            "source": "sandbox_broker_operator_api",
            "runs": [],
            "reason": "managed_api_reader_unavailable",
            "required_capability": "read-only broker run-report access",
        }
    if completed.returncode != 0:
        return {
            "availability": "inaccessible",
            "source": "sandbox_broker_operator_api",
            "runs": [],
            "reason": "managed_api_reader_denied_or_failed",
            "required_capability": "read-only broker run-report access",
        }
    try:
        listing = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return {
            "availability": "unknown",
            "source": "sandbox_broker_operator_api",
            "runs": [],
            "reason": "managed_api_reader_invalid_response",
            "required_capability": "read-only broker run-report access",
        }

    if isinstance(listing, dict):
        runs = listing.get("runs")
    else:
        runs = listing
    if not isinstance(runs, list) or not all(isinstance(run, dict) for run in runs):
        return {
            "availability": "unknown",
            "source": "sandbox_broker_operator_api",
            "runs": [],
            "reason": "operator_api_unexpected_run_listing",
            "required_capability": "read-only broker run-report access",
        }

    # Do not echo arbitrary broker metadata. Keep this diagnostic to run-report fields only.
    allowed_fields = (
        "run_id",
        "id",
        "profile",
        "status",
        "mode",
        "created_at",
        "started_at",
        "completed_at",
        "feedback_count",
        "included_feedback_ids",
        "feedback_decisions_appended",
        "github_mutation_count",
    )
    curator_runs = [
        {field: run[field] for field in allowed_fields if field in run}
        for run in runs
        if isinstance(run.get("profile"), str) and run["profile"].startswith("ykm-curator-")
    ]
    return {
        "availability": "available",
        "source": "sandbox_broker_operator_api",
        "runs": curator_runs[:5],
    }

payload = {
    "paths": {
        "intake_root": str(intake_root),
        "feedback": str(feedback_path),
        "decisions": str(decisions_path),
        "curator_status": str(status_path),
        "broker_runs_api": "http://127.0.0.1:8091/v1/runs",
    },
    "feedback": {
        "total": len(feedback_records),
        "decided": sum(1 for feedback_id in feedback_ids if feedback_id in latest_decisions),
        "undecided": len(undecided_ids),
        "recent_undecided_ids": undecided_ids[-20:],
    },
    "requested_feedback": requested,
    "last_curator_status": last_status,
    "recent_curator_runs": recent_curator_runs(),
}
print(json.dumps(payload, indent=2, sort_keys=True))
PY
