#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from curator.adapters import HttpModelProxyAdapter
from curator.feedback_model_eval import (
    DEFAULT_SCENARIO_FIXTURE,
    FeedbackScenarioCase,
    build_feedback_scenario_request,
    load_feedback_scenario_cases,
    score_feedback_scenario_output,
)
from curator.model_tasks import FeedbackPlanningModelOutput, validate_model_response_output


DEFAULT_MODELS = [
    "deepseek/deepseek-v4-flash",
    "google/gemini-3.1-flash-lite",
    "anthropic/claude-haiku-4.5",
    "nvidia/nemotron-3-super-120b-a12b",
    "anthropic/claude-sonnet-4.6",
]


def main() -> int:
    args = _parse_args()
    token = args.model_proxy_token or os.getenv(args.model_proxy_token_env)
    adapter = HttpModelProxyAdapter(
        args.model_proxy_url or os.getenv("CURATOR_MODEL_PROXY_URL", ""),
        token=token,
        timeout_seconds=args.timeout_seconds,
    )
    names = set(args.case) if args.case else None
    cases = load_feedback_scenario_cases(args.fixture, names=names)
    models = args.model or DEFAULT_MODELS

    report: dict[str, Any] = {
        "schema_version": "1",
        "fixture": str(args.fixture),
        "models": [],
    }
    for model in models:
        report["models"].append(_evaluate_model(adapter, model, cases, args.max_tokens))

    text = json.dumps(report, indent=2, sort_keys=True)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)

    if args.allow_failures:
        return 0
    return 0 if _all_passed(report) else 1


def _evaluate_model(
    adapter: HttpModelProxyAdapter,
    model: str,
    cases: list[FeedbackScenarioCase],
    max_tokens: int,
) -> dict[str, Any]:
    model_report: dict[str, Any] = {
        "model": model,
        "cases": [],
        "passed": True,
        "input_tokens": 0,
        "output_tokens": 0,
    }
    for case in cases:
        case_report: dict[str, Any] = {"case": case.name, "passed": False}
        try:
            base_plan, request = build_feedback_scenario_request(
                case=case,
                model=model,
                max_tokens=max_tokens,
                run_id_prefix=f"eval-{_run_id_slug(model)}",
            )
            response = adapter.call(request)
            output = validate_model_response_output(
                response,
                FeedbackPlanningModelOutput,
                expected_task_name="feedback_plan",
            )
            result = score_feedback_scenario_output(case, base_plan, output)
            case_report.update(result.model_dump(mode="json"))
            case_report["status"] = "pass" if result.passed else "quality_fail"
            case_report["input_tokens"] = response.usage.input_tokens
            case_report["output_tokens"] = response.usage.output_tokens
            model_report["input_tokens"] += response.usage.input_tokens
            model_report["output_tokens"] += response.usage.output_tokens
        except (RuntimeError, ValueError, ValidationError) as exc:
            case_report["status"] = "schema_or_call_fail"
            case_report["error"] = str(exc)[:1000]
            model_report["passed"] = False
        else:
            if not case_report["passed"]:
                model_report["passed"] = False
        model_report["cases"].append(case_report)
    return model_report


def _all_passed(report: dict[str, Any]) -> bool:
    return all(bool(model["passed"]) for model in report["models"])


def _run_id_slug(value: str) -> str:
    return "".join(char if char.isalnum() else "-" for char in value.lower()).strip("-")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate live models against Curator feedback-planning scenario fixtures."
    )
    parser.add_argument(
        "--fixture",
        type=Path,
        default=DEFAULT_SCENARIO_FIXTURE,
        help="Scenario fixture path.",
    )
    parser.add_argument(
        "--model",
        action="append",
        help="Model alias to evaluate. Repeat to evaluate multiple models.",
    )
    parser.add_argument(
        "--case",
        action="append",
        help="Scenario case name to run. Repeat to run multiple cases.",
    )
    parser.add_argument(
        "--model-proxy-url",
        default=os.getenv("CURATOR_MODEL_PROXY_URL", ""),
        help="Model proxy base URL. Defaults to CURATOR_MODEL_PROXY_URL.",
    )
    parser.add_argument(
        "--model-proxy-token-env",
        default="CURATOR_MODEL_PROXY_TOKEN",
        help="Environment variable containing the model proxy bearer token.",
    )
    parser.add_argument(
        "--model-proxy-token",
        default=None,
        help="Model proxy bearer token. Prefer --model-proxy-token-env for normal use.",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=8000,
        help="Per-case maximum output tokens passed to the proxy.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=180,
        help="HTTP timeout per model call.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional JSON report path.",
    )
    parser.add_argument(
        "--allow-failures",
        action="store_true",
        help="Exit 0 even when one or more model cases fail.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(main())
