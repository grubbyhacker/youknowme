from __future__ import annotations

from curator.markers import render_action_markers
from curator.models import ProposedAction


MAX_BODY_CHARS = 4000


def draft_action_body(run_id: str, action: ProposedAction) -> str:
    body = "\n".join(
        [
            "# YouKnowMe Curator proposed action",
            "",
            f"- Action: `{action.action_type}`",
            f"- Classification: `{action.classification}`",
            f"- Evidence: {_evidence_summary(action)}",
            "",
            "This deterministic draft cites evidence identifiers only. It does not include private "
            "corpus, intake, upload, feedback, or log excerpts.",
            "",
            "## Curator Markers",
            "",
            render_action_markers(run_id, action).strip(),
            "",
        ]
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
