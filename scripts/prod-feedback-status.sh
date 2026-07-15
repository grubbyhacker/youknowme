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
import sys
from pathlib import Path


requested_feedback_id = None if sys.argv[1] == "-" else sys.argv[1]

intake_root = Path("/docker/youknowme/data/intake")
feedback_path = intake_root / "feedback" / "feedback.jsonl"
decisions_path = intake_root / "feedback" / "curator-decisions.jsonl"
status_path = intake_root / "curator-status.json"
runs_root = Path("/srv/sandbox-broker/state/runs")


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

recent_runs = []
if runs_root.exists():
    reports = sorted(
        runs_root.glob("*/output/run-report.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for report_path in reports[:5]:
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            report = {"_invalid_json": str(exc)}
        recent_runs.append(
            {
                "run_id": report_path.parent.parent.name,
                "path": str(report_path),
                "mtime": report_path.stat().st_mtime,
                "status": report.get("status"),
                "mode": report.get("mode"),
                "feedback_count": report.get("feedback_count"),
                "included_feedback_ids": report.get("included_feedback_ids"),
                "feedback_decisions_appended": report.get("feedback_decisions_appended"),
                "github_mutation_count": report.get("github_mutation_count"),
            }
        )

payload = {
    "paths": {
        "intake_root": str(intake_root),
        "feedback": str(feedback_path),
        "decisions": str(decisions_path),
        "curator_status": str(status_path),
        "broker_runs": str(runs_root),
    },
    "feedback": {
        "total": len(feedback_records),
        "decided": sum(1 for feedback_id in feedback_ids if feedback_id in latest_decisions),
        "undecided": len(undecided_ids),
        "recent_undecided_ids": undecided_ids[-20:],
    },
    "requested_feedback": requested,
    "last_curator_status": last_status,
    "recent_curator_runs": recent_runs,
}
print(json.dumps(payload, indent=2, sort_keys=True))
PY
