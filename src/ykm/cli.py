from __future__ import annotations

import argparse
import json
from pathlib import Path

import uvicorn
from dotenv import load_dotenv

from ykm.artifact import ArtifactError, package_index, validate_index
from ykm.build import build_index
from ykm.contracts import QueryRequest, RetrieveRequest
from ykm.embeddings import provider_from_env
from ykm.eval import load_eval_suite, run_eval
from ykm.index import YkmIndex
from ykm.server import create_app


def main() -> None:
    load_dotenv()

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

    args = parser.parse_args()
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


if __name__ == "__main__":
    main()
