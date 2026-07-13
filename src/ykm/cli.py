from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import uvicorn
from dotenv import load_dotenv

from ykm.artifact import ArtifactError, package_index, validate_index
from ykm.build import build_index
from ykm.contracts import QueryRequest, RetrieveRequest
from ykm.curator import CuratorDryRunConfig, run_curator_dry_run
from ykm.embeddings import provider_from_env
from ykm.eval import load_eval_suite, run_eval
from ykm.index import YkmIndex
from ykm.live import LiveMcpError, call_live_tool, list_live_tools, live_config_from_env
from ykm.server import create_app


CORPUS_CHANGE_INTENTS = ["add_to_existing", "update_existing", "remove_from_existing"]
DEFAULT_LIVE_CONFIG_PATH = Path.home() / ".config" / "vps-ops" / "youknowme-cli.env"


def main() -> None:
    parser = argparse.ArgumentParser(prog="ykm")
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build")
    build.add_argument("--corpus", required=True, type=Path)
    build.add_argument("--out", required=True, type=Path)
    build.add_argument(
        "--include-root",
        action="append",
        default=[],
        help="Relative corpus directory or markdown file to include; may be passed more than once.",
    )

    validate_index_parser = subparsers.add_parser("validate-index")
    validate_index_parser.add_argument("--index", required=True, type=Path)

    package_index_parser = subparsers.add_parser("package-index")
    package_index_parser.add_argument("--index", required=True, type=Path)
    package_index_parser.add_argument("--out", required=True, type=Path)
    package_index_parser.add_argument("--name")

    query = subparsers.add_parser("query")
    query.add_argument("text")
    query.add_argument("--index", required=True, type=Path)
    query.add_argument("--type")
    query.add_argument("--tag", action="append", default=[])
    query.add_argument("--tag-any", action="append", default=[])
    query.add_argument("--source")
    query.add_argument("--limit", type=int, default=5)

    retrieve = subparsers.add_parser("retrieve")
    retrieve.add_argument("locator")
    retrieve.add_argument("--index", required=True, type=Path)
    retrieve.add_argument("--kind", choices=["source_id", "section_id", "path"], default="source_id")

    inspect = subparsers.add_parser("inspect-result")
    inspect.add_argument("result_id")
    inspect.add_argument("--index", required=True, type=Path)

    eval_parser = subparsers.add_parser("eval")
    eval_parser.add_argument("--index", required=True, type=Path)
    eval_parser.add_argument("--cases", required=True, type=Path)
    eval_parser.add_argument("--out", type=Path)

    serve = subparsers.add_parser("serve")
    serve.add_argument("--index", required=True, type=Path)
    serve.add_argument("--mode", choices=["local", "public"], default="local")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)

    _add_live_parser(subparsers)

    curator = subparsers.add_parser("curator-dry-run")
    curator.add_argument("--run-id", default=os.getenv("SANDBOX_RUN_ID", "local-curator-dry-run"))
    curator.add_argument("--intake", type=Path, default=Path(os.getenv("YKM_INTAKE_PATH", "/data/intake")))
    curator.add_argument("--logs", type=Path, default=Path(os.getenv("YKM_LOG_DIR", "/data/logs")))
    curator.add_argument("--output", type=Path, default=Path(os.getenv("YKM_CURATOR_OUTPUT", "/output")))
    curator.add_argument("--task", type=Path, default=Path("/input/task.json"))
    curator.add_argument("--no-task", action="store_true")
    curator.add_argument("--broker-url", default=os.getenv("BROKER_URL"))
    curator.add_argument("--model-proxy-url", default=os.getenv("GH_AGENT_PROXY_URL"))
    curator.add_argument("--model-proxy-token", default=os.getenv("GH_AGENT_PROXY_TOKEN"))
    curator.add_argument("--broker-fixture", type=Path)
    curator.add_argument("--model-proxy-fixture", type=Path)
    curator.add_argument("--require-broker", action="store_true")
    curator.add_argument("--require-model-proxy", action="store_true")
    curator.add_argument("--lock-path", type=Path)
    curator.add_argument("--recover-stale-lock", action="store_true")
    curator.add_argument("--simulate-execution", action="store_true")

    args = parser.parse_args()
    if args.command == "live":
        _load_live_operator_config(args.config)

    if args.command == "build":
        output = build_index(
            args.corpus,
            args.out,
            provider_from_env(),
            include_roots=args.include_root or None,
        )
        print(output.model_dump_json(indent=2))
    elif args.command == "validate-index":
        try:
            print(json.dumps(validate_index(args.index), indent=2, sort_keys=True))
        except ArtifactError as exc:
            raise SystemExit(str(exc)) from exc
    elif args.command == "package-index":
        try:
            print(
                json.dumps(
                    package_index(args.index, args.out, artifact_name=args.name),
                    indent=2,
                    sort_keys=True,
                )
            )
        except ArtifactError as exc:
            raise SystemExit(str(exc)) from exc
    elif args.command == "query":
        index = YkmIndex(args.index, provider_from_env())
        response = index.query(
            QueryRequest(
                query=args.text,
                type=args.type,
                tags=args.tag,
                tags_any=args.tag_any,
                source=args.source,
                limit=args.limit,
            )
        )
        print(response.model_dump_json(indent=2))
    elif args.command == "retrieve":
        index = YkmIndex(args.index, provider_from_env())
        response = index.retrieve(RetrieveRequest(locator=args.locator, kind=args.kind))
        print(response.model_dump_json(indent=2))
    elif args.command == "inspect-result":
        index = YkmIndex(args.index, provider_from_env())
        print(json.dumps(index.explain(args.result_id), indent=2, sort_keys=True))
    elif args.command == "eval":
        index = YkmIndex(args.index, provider_from_env())
        summary = run_eval(index, load_eval_suite(args.cases))
        summary_json = summary.model_dump_json(indent=2)
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(f"{summary_json}\n", encoding="utf-8")
        print(summary_json)
        if not summary.passed:
            raise SystemExit(1)
    elif args.command == "serve":
        uvicorn.run(create_app(args.index, args.mode), host=args.host, port=args.port)
    elif args.command == "live":
        _run_live(args)
    elif args.command == "curator-dry-run":
        report = run_curator_dry_run(
            CuratorDryRunConfig(
                run_id=args.run_id,
                intake=args.intake,
                logs=args.logs,
                output=args.output,
                task=None if args.no_task else args.task,
                broker_url=args.broker_url,
                model_proxy_url=args.model_proxy_url,
                model_proxy_token=args.model_proxy_token,
                broker_fixture=args.broker_fixture,
                model_proxy_fixture=args.model_proxy_fixture,
                required_broker=args.require_broker,
                required_model_proxy=args.require_model_proxy,
                lock_path=args.lock_path,
                recover_stale_lock=args.recover_stale_lock,
                simulate_execution=args.simulate_execution,
            )
        )
        print(report.model_dump_json(indent=2))
        if report.status != "pass":
            raise SystemExit(1)


def _add_live_parser(subparsers: argparse._SubParsersAction) -> None:
    live = subparsers.add_parser("live", help="Call the live YouKnowMe MCP over streamable HTTP")
    live.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_LIVE_CONFIG_PATH,
        help=f"Operator env file. Defaults to {DEFAULT_LIVE_CONFIG_PATH}.",
    )
    live.add_argument("--url", help="MCP URL. Defaults to YKM_LIVE_MCP_URL or production.")
    live.add_argument(
        "--timeout",
        type=float,
        help="Request timeout in seconds. Defaults to YKM_LIVE_TIMEOUT_SECONDS or 30.",
    )
    output_parent = argparse.ArgumentParser(add_help=False)
    output_parent.add_argument("--pretty", action="store_true", help="Print indented JSON.")
    output_parent.add_argument("--summary", action="store_true", help="Print a concise human summary.")
    live_subparsers = live.add_subparsers(dest="live_command", required=True)

    live_subparsers.add_parser("tools", parents=[output_parent], help="List MCP tools")
    live_subparsers.add_parser("health", parents=[output_parent], help="Call the health tool")

    query = live_subparsers.add_parser(
        "query", parents=[output_parent], help="Run the native query tool"
    )
    query.add_argument("text")
    query.add_argument("--type")
    query.add_argument("--tag", action="append", default=[])
    query.add_argument("--tag-any", action="append", default=[])
    query.add_argument("--source")
    query.add_argument("--limit", type=int, default=5)

    retrieve = live_subparsers.add_parser(
        "retrieve", parents=[output_parent], help="Retrieve an exact source or section"
    )
    retrieve.add_argument("locator")
    retrieve.add_argument("--kind", choices=["source_id", "section_id", "path"], default="source_id")

    search = live_subparsers.add_parser(
        "search", parents=[output_parent], help="Run the compatibility search tool"
    )
    search.add_argument("text")

    fetch = live_subparsers.add_parser(
        "fetch", parents=[output_parent], help="Run the compatibility fetch tool"
    )
    fetch.add_argument("source_id")

    upload = live_subparsers.add_parser(
        "upload", parents=[output_parent], help="Stage markdown through the upload tool"
    )
    upload.add_argument("--file", type=Path, action="append", required=True)
    upload.add_argument("--purpose")
    upload.add_argument("--suggested-type")
    upload.add_argument("--suggested-tag", action="append", default=[])
    upload.add_argument("--suggested-related", action="append", default=[])
    upload.add_argument("--dry-run", action="store_true")
    upload.add_argument("--yes", action="store_true", help="Actually call the live upload tool.")

    corpus_change = live_subparsers.add_parser(
        "corpus-change",
        parents=[output_parent],
        help="Request a bounded change to existing corpus content",
    )
    corpus_change.add_argument("--intent", choices=CORPUS_CHANGE_INTENTS, required=True)
    corpus_change.add_argument("--instruction", required=True)
    corpus_change.add_argument("--source-id")
    corpus_change.add_argument("--section-id")
    corpus_change.add_argument("--result-id", action="append", default=[])
    corpus_change.add_argument("--upload-id")
    corpus_change.add_argument("--dry-run", action="store_true")
    corpus_change.add_argument(
        "--yes", action="store_true", help="Actually call the live corpus_change tool."
    )


def _load_live_operator_config(path: Path) -> None:
    if not path.exists():
        return
    if not path.is_file():
        raise SystemExit(f"live CLI config is not a regular file: {path}")
    if path.stat().st_mode & 0o077:
        raise SystemExit(f"live CLI config must not be accessible by group or others: {path}")
    load_dotenv(path, override=False)


def _run_live(args: argparse.Namespace) -> None:
    try:
        if args.live_command == "upload":
            _run_live_upload(args)
            return
        if args.live_command == "corpus-change":
            _run_live_corpus_change(args)
            return

        config = live_config_from_env(url=args.url, timeout_seconds=args.timeout)
        if args.live_command == "tools":
            payload = list_live_tools(config)
        else:
            tool_name, arguments = _live_read_tool_call(args)
            payload = call_live_tool(config, tool_name, arguments)
        _print_live_payload(payload, args)
    except LiveMcpError as exc:
        raise SystemExit(str(exc)) from exc


def _run_live_upload(args: argparse.Namespace) -> None:
    payload = _live_upload_arguments(args)
    if _emit_write_preview_or_refusal(args, "upload", payload):
        return
    config = live_config_from_env(url=args.url, timeout_seconds=args.timeout)
    _print_live_payload(call_live_tool(config, "upload", payload), args)


def _run_live_corpus_change(args: argparse.Namespace) -> None:
    payload = _live_corpus_change_arguments(args)
    if _emit_write_preview_or_refusal(args, "corpus_change", payload):
        return
    config = live_config_from_env(url=args.url, timeout_seconds=args.timeout)
    _print_live_payload(call_live_tool(config, "corpus_change", payload), args)


def _live_read_tool_call(args: argparse.Namespace) -> tuple[str, dict[str, Any]]:
    if args.live_command == "health":
        return "health", {}
    if args.live_command == "query":
        return (
            "query",
            {
                "query": args.text,
                "type": args.type,
                "tags": args.tag,
                "tags_any": args.tag_any,
                "source": args.source,
                "limit": args.limit,
            },
        )
    if args.live_command == "retrieve":
        return "retrieve", {"locator": args.locator, "kind": args.kind}
    if args.live_command == "search":
        return "search", {"query": args.text}
    if args.live_command == "fetch":
        return "fetch", {"id": args.source_id}
    raise LiveMcpError(f"unsupported live command: {args.live_command}")


def _live_upload_arguments(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "files": [
            {"filename": path.name, "content": path.read_text(encoding="utf-8")}
            for path in args.file
        ],
        "purpose": args.purpose,
        "suggested_type": args.suggested_type,
        "suggested_tags": args.suggested_tag,
        "suggested_related": args.suggested_related,
    }


def _live_corpus_change_arguments(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "intent": args.intent,
        "instruction": args.instruction,
        "source_id": args.source_id,
        "section_id": args.section_id,
        "result_ids": args.result_id,
        "upload_id": args.upload_id,
    }


def _emit_write_preview_or_refusal(
    args: argparse.Namespace, tool_name: str, arguments: dict[str, Any]
) -> bool:
    if args.dry_run:
        _print_live_payload(
            {
                "dry_run": True,
                "tool": tool_name,
                "arguments": arguments,
            },
            args,
        )
        return True
    if args.yes:
        return False

    _print_live_payload(
        {
            "error": "confirmation_required",
            "tool": tool_name,
            "arguments": arguments,
        },
        args,
    )
    print(f"Refusing live {tool_name}; rerun with --yes to call the MCP tool.", file=sys.stderr)
    raise SystemExit(2)


def _print_live_payload(payload: Any, args: argparse.Namespace) -> None:
    if args.summary:
        print(_live_summary(payload, args.live_command))
        return
    print(json.dumps(payload, indent=2 if args.pretty else None, sort_keys=True))


def _live_summary(payload: Any, command: str) -> str:
    if command == "health" and isinstance(payload, dict):
        return (
            f"{payload.get('service', 'YouKnowMe')} {payload.get('status', 'unknown')} "
            f"build={payload.get('build_id')} commit={payload.get('source_commit')}"
        )
    if command == "query" and isinstance(payload, dict):
        results = payload.get("results") or []
        return "\n".join(
            f"{index + 1}. {result.get('source_id')} #{result.get('section_id')} "
            f"score={result.get('score')}"
            for index, result in enumerate(results)
            if isinstance(result, dict)
        )
    if command == "search" and isinstance(payload, list):
        return "\n".join(
            f"{index + 1}. {result.get('id')} {result.get('url')}"
            for index, result in enumerate(payload)
            if isinstance(result, dict)
        )
    if command in {"upload", "corpus-change"} and isinstance(payload, dict):
        identifier = (
            payload.get("upload_id") or payload.get("corpus_change_id") or payload.get("error")
        )
        return f"{command}: {identifier}"
    return json.dumps(payload, indent=2, sort_keys=True)


if __name__ == "__main__":
    main()
