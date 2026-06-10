#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from curator.adapters import HttpModelProxyAdapter
from curator.model_tasks import UploadReviewModelOutput, validate_model_response_output
from curator.upload_model_eval import (
    DEFAULT_UPLOAD_SCENARIO_FIXTURE,
    UploadReviewScenarioCase,
    build_upload_review_scenario_request,
    load_upload_review_scenario_cases,
    score_upload_review_scenario_output,
)


DEFAULT_MODELS = [
    "google/gemini-3.1-flash-lite",
    "anthropic/claude-haiku-4.5",
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
    cases = load_upload_review_scenario_cases(args.fixture, names=names)
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
    return 0 if args.allow_failures or _all_passed(report) else 1


def _evaluate_model(
    adapter: HttpModelProxyAdapter,
    model: str,
    cases: list[UploadReviewScenarioCase],
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
            request = build_upload_review_scenario_request(
                case=case,
                model=model,
                max_tokens=max_tokens,
                run_id_prefix=f"eval-upload-{_run_id_slug(model)}",
            )
            response = adapter.call(request)
            output = validate_model_response_output(
                response,
                UploadReviewModelOutput,
                expected_task_name="upload_review",
            )
            result = score_upload_review_scenario_output(case, output)
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
        description="Evaluate live models against Curator upload-review scenario fixtures."
    )
    parser.add_argument("--fixture", type=Path, default=DEFAULT_UPLOAD_SCENARIO_FIXTURE)
    parser.add_argument("--model", action="append")
    parser.add_argument("--case", action="append")
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
    parser.add_argument("--model-proxy-token", default=None)
    parser.add_argument("--max-tokens", type=int, default=12000)
    parser.add_argument("--timeout-seconds", type=float, default=180)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--allow-failures", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(main())
