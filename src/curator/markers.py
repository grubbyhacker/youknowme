from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from curator.models import ProposedAction


MARKER_PREFIX = "YKM-Curator-"
ACTION_SCOPES = {"upload", "feedback", "maintenance"}


class CuratorMarkers(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str | None = None
    action_id: str | None = None
    action_scope: str | None = None
    action_type: str | None = None
    idempotency_key: str | None = None
    upload_ids: list[str] = Field(default_factory=list)
    feedback_ids: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    section_ids: list[str] = Field(default_factory=list)
    result_ids: list[str] = Field(default_factory=list)


def render_action_markers(run_id: str, action: ProposedAction) -> str:
    lines = [
        f"YKM-Curator-Run: {run_id}",
        f"YKM-Curator-Action: {_action_scope(action)}",
        f"YKM-Curator-Action-Type: {action.action_type}",
        f"YKM-Curator-Action-ID: {action.action_id}",
        f"YKM-Curator-Idempotency-Key: {action.idempotency_key}",
    ]
    lines.extend(_marker_lines("YKM-Curator-Upload", action.evidence.upload_ids))
    lines.extend(_marker_lines("YKM-Curator-Feedback", action.evidence.feedback_ids))
    lines.extend(_marker_lines("YKM-Curator-Source", action.evidence.source_ids))
    lines.extend(_marker_lines("YKM-Curator-Section", action.evidence.section_ids))
    lines.extend(_marker_lines("YKM-Curator-Result", action.evidence.result_ids))
    return "\n".join(lines) + "\n"


def parse_curator_markers(text: str) -> CuratorMarkers:
    values: dict[str, list[str]] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line.startswith(MARKER_PREFIX) or ":" not in line:
            continue
        key, raw_value = line.split(":", 1)
        value = raw_value.strip()
        if not value:
            continue
        values.setdefault(key, []).append(value)
    action_marker = _last(values, "YKM-Curator-Action")
    action_type = _last(values, "YKM-Curator-Action-Type")
    action_scope = action_marker if action_marker in ACTION_SCOPES else None
    if action_type is None and action_marker not in ACTION_SCOPES:
        action_type = action_marker
    return CuratorMarkers(
        run_id=_last(values, "YKM-Curator-Run"),
        action_id=_last(values, "YKM-Curator-Action-ID"),
        action_scope=action_scope,
        action_type=action_type,
        idempotency_key=_last(values, "YKM-Curator-Idempotency-Key"),
        upload_ids=sorted(set(values.get("YKM-Curator-Upload", []))),
        feedback_ids=sorted(set(values.get("YKM-Curator-Feedback", []))),
        source_ids=sorted(set(values.get("YKM-Curator-Source", []))),
        section_ids=sorted(set(values.get("YKM-Curator-Section", []))),
        result_ids=sorted(set(values.get("YKM-Curator-Result", []))),
    )


def _marker_lines(name: str, values: list[str]) -> list[str]:
    return [f"{name}: {value}" for value in sorted(set(values))]


def _action_scope(action: ProposedAction) -> str:
    if action.evidence.feedback_ids:
        return "feedback"
    if action.evidence.upload_ids:
        return "upload"
    return "maintenance"


def _last(values: dict[str, list[str]], key: str) -> str | None:
    items = values.get(key)
    if not items:
        return None
    return items[-1]
