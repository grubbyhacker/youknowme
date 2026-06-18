from __future__ import annotations

from typing import Any

from curator.markers import render_action_markers
from curator.models import ProposedAction


MAX_BODY_CHARS = 4000
MAX_FEEDBACK_EXCERPT_CHARS = 1200
MAX_FEEDBACK_EXCERPTS = 3


def draft_action_body(
    run_id: str,
    action: ProposedAction,
    *,
    feedback_records: list[dict[str, Any]] | None = None,
) -> str:
    feedback_section = _feedback_section(feedback_records or [])
    context_note = (
        "This deterministic draft includes bounded corpus change excerpts because the target is a "
        "private corpus workflow. It does not include corpus, upload, or log excerpts."
        if feedback_section
        else (
            "This deterministic draft cites evidence identifiers only. It does not include private "
            "corpus, intake, upload, feedback, or log excerpts."
        )
    )
    body_parts = [
        "# YouKnowMe Curator proposed action",
        "",
        f"- Action: `{action.action_type}`",
        f"- Classification: `{action.classification}`",
        f"- Evidence: {_evidence_summary(action)}",
        "",
        context_note,
        "",
    ]
    if feedback_section:
        body_parts.extend([feedback_section, ""])
    body_parts.extend(
        [
            "## Curator Markers",
            "",
            render_action_markers(run_id, action).strip(),
            "",
        ]
    )
    body = "\n".join(
        body_parts
    )
    if len(body) <= MAX_BODY_CHARS:
        return body
    marker_block = render_action_markers(run_id, action)
    available = MAX_BODY_CHARS - len(marker_block) - 32
    return body[:available].rstrip() + "\n\n" + marker_block


def _evidence_summary(action: ProposedAction) -> str:
    parts: list[str] = []
    for label, values in (
        ("feedback", action.evidence.feedback_ids),
        ("uploads", action.evidence.upload_ids),
        ("sources", action.evidence.source_ids),
        ("sections", action.evidence.section_ids),
        ("results", action.evidence.result_ids),
    ):
        if values:
            rendered = ", ".join(f"`{value}`" for value in sorted(set(values))[:10])
            suffix = " ..." if len(set(values)) > 10 else ""
            parts.append(f"{label}: {rendered}{suffix}")
    return "; ".join(parts) or "none"


def _feedback_section(records: list[dict[str, Any]]) -> str:
    rendered: list[str] = []
    for record in records[:MAX_FEEDBACK_EXCERPTS]:
        feedback_id = _str_value(record.get("feedback_id")) or "unknown"
        intent = _str_value(record.get("intent"))
        instruction = _str_value(record.get("instruction")) or _str_value(record.get("comment"))
        lines = [f"### `{feedback_id}`"]
        metadata = []
        if intent:
            metadata.append(f"intent: `{intent}`")
        source_id = _str_value(record.get("source_id"))
        if source_id:
            metadata.append(f"source: `{source_id}`")
        section_id = _str_value(record.get("section_id"))
        if section_id:
            metadata.append(f"section: `{section_id}`")
        upload_id = _str_value(record.get("upload_id"))
        if upload_id:
            metadata.append(f"upload: `{upload_id}`")
        if metadata:
            lines.append("- " + "; ".join(metadata))
        if instruction:
            lines.extend(["", _bounded_quote(instruction)])
        else:
            lines.append("")
            lines.append("_No corpus change instruction text was captured._")
        rendered.append("\n".join(lines))
    if not rendered:
        return ""
    suffix = ""
    if len(records) > MAX_FEEDBACK_EXCERPTS:
        suffix = f"\n\n_Additional feedback records omitted: {len(records) - MAX_FEEDBACK_EXCERPTS}._"
    return "## Corpus Change Requests\n\n" + "\n\n".join(rendered) + suffix


def _bounded_quote(value: str) -> str:
    normalized = " ".join(value.split())
    if len(normalized) > MAX_FEEDBACK_EXCERPT_CHARS:
        normalized = normalized[: MAX_FEEDBACK_EXCERPT_CHARS - 1].rstrip() + "..."
    return "> " + normalized.replace("\n", "\n> ")


def _str_value(value: Any) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None
