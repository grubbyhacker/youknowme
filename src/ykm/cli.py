from __future__ import annotations

import argparse
import json
from pathlib import Path

import uvicorn

from ykm.build import build_index
from ykm.contracts import QueryRequest, RetrieveRequest
from ykm.embeddings import provider_from_env
from ykm.index import YkmIndex
from ykm.server import create_app


def main() -> None:
    parser = argparse.ArgumentParser(prog="ykm")
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build")
    build.add_argument("--corpus", required=True, type=Path)
    build.add_argument("--out", required=True, type=Path)

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

    serve = subparsers.add_parser("serve")
    serve.add_argument("--index", required=True, type=Path)
    serve.add_argument("--mode", choices=["local", "public"], default="local")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)

    args = parser.parse_args()
    if args.command == "build":
        output = build_index(args.corpus, args.out, provider_from_env())
        print(output.model_dump_json(indent=2))
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
    elif args.command == "serve":
        uvicorn.run(create_app(args.index, args.mode), host=args.host, port=args.port)


if __name__ == "__main__":
    main()

