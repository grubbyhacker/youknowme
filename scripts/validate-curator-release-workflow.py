#!/usr/bin/env python3
"""Offline validator for the Curator release publish/promote workflow.

Stage 3 (youknowme side). Encodes the safety invariants of
`.github/workflows/curator-release.yml` so a regression is caught in a pull
request rather than by a real broker call. This check is offline and
dependency-free: it reads the workflow text and reasons about it structurally.
It never runs the workflow, joins a network, or reaches the broker.

Invariants asserted:

  1. Manual-only trigger: the `on:` block declares `workflow_dispatch` and no
     `push`, `workflow_run`, `schedule`, `pull_request`, `release`, or
     `repository_dispatch`. Merging must not be able to call the broker.
  2. No caller image/generation selection: no `workflow_dispatch` input names an
     image, digest, generation, ref, tag, or sha.
  3. Pinned cross-repo commit: `BROKER_COMMIT` is an exact 40-hex commit and the
     gh-agent-broker checkout pins its ref to it, not a moving branch/tag.
  4. Publisher/promoter separation: the publish step carries the publisher
     operator token and the promote step the promoter operator token; neither
     carries the other's, and the two secrets are distinct.
  5. Tailscale-before-call: the tailscale join step appears before the first
     broker call (`release-publish` / `release-promote`).
  6. Archive/provenance binding: the publish step passes the built single-image
     Docker archive and the provenance flags.
  7. Generation-only promotion: the promote step passes the broker-assigned
     generation from publish and never an image reference/digest/tag.
  8. Release endpoint on the Tailnet AgentRelease port: the BROKER_URL default
     targets the sandbox-broker private AgentRelease API on port 8091, never the
     broker's 8080 deploy/dispatch listener (which returns 404 for the release
     API — proven by run 35699348779).
  9. Summary is option-safe: every `printf` whose format string begins with '-'
     uses `printf --`, so the always-run summary can never fail with
     "printf: -: invalid option".
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "curator-release.yml"

PUBLISHER_TOKEN = "VPS_OPS_GH_BROKER_RELEASE_PUBLISHER_OPERATOR_TOKEN"
PROMOTER_TOKEN = "VPS_OPS_GH_BROKER_RELEASE_PROMOTER_OPERATOR_TOKEN"
# The real CLI invocations (not the prose mentions in the header comment).
PUBLISH_INVOCATION = 'gh-agent-broker" release-publish'
PROMOTE_INVOCATION = 'gh-agent-broker" release-promote'
FORBIDDEN_TRIGGERS = (
    "push",
    "workflow_run",
    "schedule",
    "pull_request",
    "release",
    "repository_dispatch",
)


def _on_block(text: str) -> str:
    """Return the text of the top-level `on:` block (up to the next top-level key)."""
    match = re.search(r"(?ms)^on:\s*\n(.*?)^\S", text + "\n\uffff:\n")
    return match.group(1) if match else ""


def _publish_span(text: str) -> str:
    return _step_span(text, PUBLISH_INVOCATION)


def _promote_span(text: str) -> str:
    return _step_span(text, PROMOTE_INVOCATION)


def _step_span(text: str, needle: str) -> str:
    """Return the text of the step (`- name:` block) that contains `needle`.

    Only real step blocks are considered (they begin with `name:`), so the
    workflow's header comment — which mentions the CLI verbs in prose — is never
    mistaken for a step.
    """
    steps = re.split(r"(?m)^      - ", text)
    for step in steps:
        if step.lstrip().startswith("name:") and needle in step:
            return step
    return ""


def check_manual_only(text: str, errors: list[str]) -> None:
    on = _on_block(text)
    if "workflow_dispatch:" not in on:
        errors.append("workflow must declare a workflow_dispatch trigger.")
    for trigger in FORBIDDEN_TRIGGERS:
        if re.search(rf"(?m)^\s{{2}}{trigger}:", on):
            errors.append(f"workflow must be manual-only; forbidden trigger present: {trigger}.")


def check_no_caller_image_or_generation(text: str, errors: list[str]) -> None:
    on = _on_block(text)
    inputs_match = re.search(r"(?ms)^\s{4}inputs:\s*\n(.*?)(?=^\S|^\s{0,2}\S|\Z)", on)
    if not inputs_match:
        return
    banned = re.compile(r"^\s{6}(\w*(?:image|digest|generation|ref|tag|sha)\w*):", re.IGNORECASE | re.MULTILINE)
    for match in banned.finditer(inputs_match.group(1)):
        errors.append(
            f"workflow_dispatch input {match.group(1)!r} lets the caller select an "
            "image/generation/ref; that is forbidden."
        )


def check_pinned_broker_commit(text: str, errors: list[str]) -> None:
    commit = re.search(r"BROKER_COMMIT:\s*([0-9a-f]{40})\b", text)
    if not commit:
        errors.append("workflow must pin BROKER_COMMIT to an exact 40-hex commit.")
    # Find specifically the broker checkout block.
    broker_block = None
    for block in re.split(r"(?m)^      - ", text):
        if "actions/checkout" in block and "gh-agent-broker" in block and "repository:" in block:
            broker_block = block
            break
    if broker_block is None:
        errors.append("workflow must check out grubbyhacker/gh-agent-broker.")
        return
    ref_match = re.search(r"ref:\s*(.+)", broker_block)
    ref = ref_match.group(1).strip() if ref_match else ""
    if "BROKER_COMMIT" not in ref:
        errors.append("the gh-agent-broker checkout must pin ref to env.BROKER_COMMIT.")
    if re.search(r"\b(main|master|latest)\b", ref) or re.search(r"@v\d", ref):
        errors.append("the gh-agent-broker checkout ref must not be a moving branch/tag.")


def check_publisher_promoter_separation(text: str, errors: list[str]) -> None:
    publish = _publish_span(text)
    promote = _promote_span(text)
    if not publish:
        errors.append("workflow must invoke `release-publish`.")
    if not promote:
        errors.append("workflow must invoke `release-promote`.")
    if not publish or not promote:
        return
    if PUBLISHER_TOKEN not in publish:
        errors.append(f"publish step must use {PUBLISHER_TOKEN}.")
    if PROMOTER_TOKEN not in promote:
        errors.append(f"promote step must use {PROMOTER_TOKEN}.")
    if PROMOTER_TOKEN in publish:
        errors.append("publish step must NOT carry the promoter token.")
    if PUBLISHER_TOKEN in promote:
        errors.append("promote step must NOT carry the publisher token.")
    if PUBLISHER_TOKEN == PROMOTER_TOKEN:
        errors.append("publisher and promoter tokens must be distinct secrets.")


def check_tailscale_before_call(text: str, errors: list[str]) -> None:
    ts = text.find("tailscale/github-action")
    publish = text.find(PUBLISH_INVOCATION)
    promote = text.find(PROMOTE_INVOCATION)
    calls = [pos for pos in (publish, promote) if pos != -1]
    if ts == -1:
        errors.append("workflow must join the tailnet via tailscale/github-action.")
        return
    if not calls:
        errors.append("workflow must call the broker (release-publish/promote).")
        return
    if ts >= min(calls):
        errors.append("the tailnet must be joined BEFORE the first broker call.")


def check_archive_and_provenance_binding(text: str, errors: list[str]) -> None:
    publish = _publish_span(text)
    if not publish:
        return
    for flag in ("-archive", "-source-revision", "-platform", "-dependency-manifest-sha256"):
        if flag not in publish:
            errors.append(f"publish step must pass {flag} to bind the archive/provenance.")
    if "curator-image.tar" not in publish:
        errors.append("publish step must pass the built single-image Docker archive.")


def check_generation_only_promotion(text: str, errors: list[str]) -> None:
    promote = _promote_span(text)
    if not promote:
        return
    if "-generation" not in promote:
        errors.append("promote step must pass -generation.")
    if "steps.publish.outputs.generation" not in promote:
        errors.append("promote step must use the broker-assigned generation from publish.")
    if re.search(r"-(image|digest|reference|tag)\b", promote):
        errors.append("promote step must NOT pass an image reference; generation only.")


# The release publish/promote API is served on the Tailnet AgentRelease port
# 8091. Port 8080 is the broker's deploy/dispatch listener and returns 404 for
# the release API (proven by run 35699348779).
RELEASE_API_PORT = "8091"
FORBIDDEN_ENDPOINT_PORT = "8080"


def check_release_endpoint_port(text: str, errors: list[str]) -> None:
    default = re.search(
        r"BROKER_URL:\s*\$\{\{[^}]*\|\|\s*'([^']+)'\s*\}\}",
        text,
    )
    if not default:
        errors.append("workflow must define a default BROKER_URL endpoint.")
        return
    url = default.group(1)
    port = re.search(r":(\d+)(?:/|$)", url)
    if port is None:
        errors.append(f"BROKER_URL default {url!r} must name an explicit port.")
        return
    if port.group(1) == FORBIDDEN_ENDPOINT_PORT:
        errors.append(
            f"BROKER_URL default targets the broker deploy/dispatch port "
            f"{FORBIDDEN_ENDPOINT_PORT}, which does not serve the release API "
            f"(404). The release endpoint must be Tailnet port {RELEASE_API_PORT}."
        )
    elif port.group(1) != RELEASE_API_PORT:
        errors.append(
            f"BROKER_URL default port {port.group(1)} is not the release API "
            f"port {RELEASE_API_PORT}."
        )


def check_summary_printf_is_option_safe(text: str, errors: list[str]) -> None:
    """Any printf whose format string starts with '-' must use `printf --`.

    Otherwise the leading '-' is parsed as an option and the always-run summary
    step aborts with "printf: -: invalid option".
    """
    for match in re.finditer(r"printf(\s+--)?\s+'(-[^']*)'", text):
        if match.group(1) is None:
            fmt = match.group(2)
            errors.append(
                f"printf format {fmt!r} begins with '-' but does not use "
                "`printf --`; the summary step could fail on an option-like format."
            )


def main() -> int:
    errors: list[str] = []
    if not WORKFLOW.is_file():
        print(f"missing workflow: {WORKFLOW}", file=sys.stderr)
        return 1
    text = WORKFLOW.read_text(encoding="utf-8")

    check_manual_only(text, errors)
    check_no_caller_image_or_generation(text, errors)
    check_pinned_broker_commit(text, errors)
    check_publisher_promoter_separation(text, errors)
    check_tailscale_before_call(text, errors)
    check_archive_and_provenance_binding(text, errors)
    check_generation_only_promotion(text, errors)
    check_release_endpoint_port(text, errors)
    check_summary_printf_is_option_safe(text, errors)

    if errors:
        print("Curator release workflow check failed:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1
    print(
        "Curator release workflow check passed (manual-only, token separation, "
        "tailscale-before-call, archive/provenance binding, generation-only "
        "promotion, release endpoint on Tailnet port 8091, option-safe summary)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
