from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field


CURATOR_REPORT_SCHEMA_VERSION = "1"
DEFAULT_FORBIDDEN_ENV = (
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "OPENROUTER_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "YKM_GITHUB_PRIVATE_KEY_PATH",
    "YKM_CF_ACCESS_CLIENT_SECRET",
)
UPLOAD_QUEUE_STATES = ("pending", "claimed", "processed", "rejected", "archive", "deferred")


class CuratorProbe(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    status: Literal["pass", "fail", "skip"]
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class CuratorDryRunReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = CURATOR_REPORT_SCHEMA_VERSION
    run_id: str
    created_at: datetime
    status: Literal["pass", "fail"]
    task: dict[str, Any] | None = None
    intake_path: str
    logs_path: str | None = None
    output_path: str
    upload_queue_counts: dict[str, int]
    pending_uploads: list[str]
    feedback_count: int
    query_log_count: int
    probes: list[CuratorProbe]


@dataclass(frozen=True)
class CuratorDryRunConfig:
    run_id: str
    intake: Path
    output: Path
    logs: Path | None = None
    task: Path | None = None
    broker_url: str | None = None
    model_proxy_url: str | None = None
    model_proxy_token: str | None = None
    required_broker: bool = False
    required_model_proxy: bool = False


def run_curator_dry_run(config: CuratorDryRunConfig) -> CuratorDryRunReport:
    probes: list[CuratorProbe] = []
    task_payload = _read_task(config.task, probes)

    _check_directory(config.intake, "intake", probes, writable=False)
    _check_directory(config.output, "output", probes, writable=True)
    if config.logs is not None:
        _check_directory(config.logs, "logs", probes, writable=False)

    upload_counts, pending_uploads = _snapshot_uploads(config.intake, probes)
    feedback_count = _count_jsonl(config.intake / "feedback" / "feedback.jsonl", "feedback", probes)
    query_log_count = _count_query_logs(config.logs, probes) if config.logs is not None else 0

    _check_forbidden_env(probes)
    _probe_broker(config, probes)
    _probe_model_proxy(config, probes)

    status = "fail" if any(probe.status == "fail" for probe in probes) else "pass"
    report = CuratorDryRunReport(
        run_id=config.run_id,
        created_at=datetime.now(UTC),
        status=status,
        task=task_payload,
        intake_path=str(config.intake),
        logs_path=str(config.logs) if config.logs is not None else None,
        output_path=str(config.output),
        upload_queue_counts=upload_counts,
        pending_uploads=pending_uploads,
        feedback_count=feedback_count,
        query_log_count=query_log_count,
        probes=probes,
    )
    write_curator_reports(report, config.output)
    return report


def write_curator_reports(report: CuratorDryRunReport, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / "run-report.json").write_text(
        report.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    (output / "run-report.md").write_text(_report_markdown(report), encoding="utf-8")


def _read_task(path: Path | None, probes: list[CuratorProbe]) -> dict[str, Any] | None:
    if path is None:
        probes.append(CuratorProbe(name="task", status="skip", message="no task path configured"))
        return None
    if not path.exists():
        probes.append(CuratorProbe(name="task", status="fail", message=f"task file missing: {path}"))
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        probes.append(CuratorProbe(name="task", status="fail", message=f"task file unreadable: {exc}"))
        return None
    if not isinstance(payload, dict):
        probes.append(CuratorProbe(name="task", status="fail", message="task JSON must be an object"))
        return None
    probes.append(CuratorProbe(name="task", status="pass", message="task contract loaded"))
    return payload


def _check_directory(path: Path, name: str, probes: list[CuratorProbe], *, writable: bool) -> None:
    if writable:
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            probes.append(CuratorProbe(name=name, status="fail", message=f"path cannot be created: {exc}"))
            return
    if not path.exists():
        probes.append(CuratorProbe(name=name, status="fail", message=f"path does not exist: {path}"))
        return
    if not path.is_dir():
        probes.append(CuratorProbe(name=name, status="fail", message=f"path is not a directory: {path}"))
        return
    if writable:
        try:
            marker = path / ".ykm-curator-dry-run-write-test"
            marker.write_text("ok\n", encoding="utf-8")
            marker.unlink()
        except OSError as exc:
            probes.append(CuratorProbe(name=name, status="fail", message=f"path is not writable: {exc}"))
            return
    probes.append(CuratorProbe(name=name, status="pass", message=f"path is available: {path}"))


def _snapshot_uploads(intake: Path, probes: list[CuratorProbe]) -> tuple[dict[str, int], list[str]]:
    uploads = intake / "uploads"
    counts: dict[str, int] = {}
    pending_uploads: list[str] = []
    if not uploads.exists():
        probes.append(CuratorProbe(name="uploads", status="skip", message="uploads directory absent"))
        return {state: 0 for state in UPLOAD_QUEUE_STATES}, pending_uploads

    for state in UPLOAD_QUEUE_STATES:
        state_dir = uploads / state
        if not state_dir.exists():
            counts[state] = 0
            continue
        bundles = sorted(path.name for path in state_dir.iterdir() if path.is_dir())
        counts[state] = len(bundles)
        if state == "pending":
            pending_uploads = bundles[:50]

    probes.append(
        CuratorProbe(
            name="uploads",
            status="pass",
            message="upload queue snapshot captured",
            details={"counts": counts},
        )
    )
    return counts, pending_uploads


def _count_jsonl(path: Path, name: str, probes: list[CuratorProbe]) -> int:
    if not path.exists():
        probes.append(CuratorProbe(name=name, status="skip", message=f"JSONL file absent: {path}"))
        return 0
    count = 0
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line_no, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                json.loads(line)
                count += 1
    except (OSError, json.JSONDecodeError) as exc:
        probes.append(CuratorProbe(name=name, status="fail", message=f"JSONL read failed: {exc}"))
        return count
    probes.append(CuratorProbe(name=name, status="pass", message=f"{count} JSONL records readable"))
    return count


def _count_query_logs(logs: Path | None, probes: list[CuratorProbe]) -> int:
    if logs is None:
        return 0
    return _count_jsonl(logs / "query-log.jsonl", "query-log", probes)


def _check_forbidden_env(probes: list[CuratorProbe]) -> None:
    present = [name for name in DEFAULT_FORBIDDEN_ENV if os.getenv(name)]
    if present:
        probes.append(
            CuratorProbe(
                name="forbidden-env",
                status="fail",
                message="forbidden secret environment variables are present",
                details={"names": present},
            )
        )
    else:
        probes.append(
            CuratorProbe(
                name="forbidden-env",
                status="pass",
                message="no forbidden provider/GitHub/VPS secret environment variables found",
            )
        )


def _probe_broker(config: CuratorDryRunConfig, probes: list[CuratorProbe]) -> None:
    missing = [
        name
        for name, value in {
            "BROKER_URL": config.broker_url,
            "BROKER_AGENT_ID": os.getenv("BROKER_AGENT_ID"),
            "BROKER_AGENT_SECRET": os.getenv("BROKER_AGENT_SECRET"),
        }.items()
        if not value
    ]
    if missing:
        probes.append(
            CuratorProbe(
                name="broker",
                status="fail" if config.required_broker else "skip",
                message="broker probe not configured",
                details={"missing": missing},
            )
        )
        return
    assert config.broker_url is not None
    try:
        response = httpx.get(f"{config.broker_url.rstrip('/')}/healthz", timeout=5)
        ok = response.status_code < 500
    except httpx.HTTPError as exc:
        probes.append(CuratorProbe(name="broker", status="fail", message=f"broker unreachable: {exc}"))
        return
    probes.append(
        CuratorProbe(
            name="broker",
            status="pass" if ok else "fail",
            message=f"broker health responded with HTTP {response.status_code}",
        )
    )


def _probe_model_proxy(config: CuratorDryRunConfig, probes: list[CuratorProbe]) -> None:
    if not config.model_proxy_url:
        probes.append(
            CuratorProbe(
                name="model-proxy",
                status="fail" if config.required_model_proxy else "skip",
                message="model proxy URL not configured",
            )
        )
        return
    if not config.model_proxy_token:
        probes.append(
            CuratorProbe(
                name="model-proxy",
                status="fail" if config.required_model_proxy else "skip",
                message="model proxy token not configured",
            )
        )
        return
    try:
        response = httpx.get(f"{config.model_proxy_url.rstrip('/')}/healthz", timeout=5)
    except httpx.HTTPError as exc:
        probes.append(
            CuratorProbe(name="model-proxy", status="fail", message=f"model proxy unreachable: {exc}")
        )
        return
    probes.append(
        CuratorProbe(
            name="model-proxy",
            status="pass" if response.status_code == 200 else "fail",
            message=f"model proxy health responded with HTTP {response.status_code}",
        )
    )


def _report_markdown(report: CuratorDryRunReport) -> str:
    lines = [
        "# YouKnowMe Curator Dry-Run Report",
        "",
        f"- Run ID: `{report.run_id}`",
        f"- Status: `{report.status}`",
        f"- Created: `{report.created_at.isoformat()}`",
        f"- Intake: `{report.intake_path}`",
        f"- Logs: `{report.logs_path or 'not configured'}`",
        f"- Output: `{report.output_path}`",
        f"- Feedback records: `{report.feedback_count}`",
        f"- Query log records: `{report.query_log_count}`",
        "",
        "## Upload Queues",
        "",
    ]
    for state, count in sorted(report.upload_queue_counts.items()):
        lines.append(f"- `{state}`: `{count}`")
    lines.extend(["", "## Probes", ""])
    for probe in report.probes:
        lines.append(f"- `{probe.name}`: `{probe.status}` - {probe.message}")
    return "\n".join(lines) + "\n"
