from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from .config import Settings
from .database import OperationalDatabase
from .evaluation import DomainEvaluator
from .models import QueryRequest
from .service import NL2SQLService


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="nl2sql", description="Persian Schema-RAG NL2SQL backend")
    subcommands = parser.add_subparsers(dest="command", required=True)

    ingest = subcommands.add_parser("ingest", help="Build the local hybrid knowledge index")
    ingest.add_argument("--docs", type=Path, required=True)
    ingest.add_argument("--index", type=Path, default=Path("data/index"))
    ingest.add_argument(
        "--embedding-provider",
        choices=("sentence_transformer", "feature_hash"),
        default=None,
        help="feature_hash is only for offline smoke tests",
    )
    ingest.add_argument("--embedding-model", default=None)

    query = subcommands.add_parser("query", help="Generate and optionally execute SQL")
    query.add_argument("question")
    query.add_argument("--execute", action="store_true")
    query.add_argument("--include-context", action="store_true")

    subcommands.add_parser("health", help="Show local service readiness")
    subcommands.add_parser("db-status", help="Show operational database and migration status")
    subcommands.add_parser("knowledge-status", help="Show index and distributed store status")
    evaluate = subcommands.add_parser(
        "evaluate",
        help="Run the packaged Commission 100 gold suite",
    )
    evaluate.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optionally persist the full JSON evidence report",
    )
    subcommands.add_parser("production-check", help="Run final production acceptance checks")
    return parser


async def _run(args: argparse.Namespace) -> int:
    settings = Settings()
    if args.command == "db-status":
        database = OperationalDatabase(settings.resolved_operational_database_url)
        try:
            print(json.dumps(database.status(), ensure_ascii=False, indent=2))
        finally:
            database.dispose()
        return 0

    if args.command == "ingest":
        updates = {"docs_root": args.docs, "index_dir": args.index}
        if args.embedding_provider:
            updates["embedding_provider"] = args.embedding_provider
        if args.embedding_model:
            updates["embedding_model"] = args.embedding_model
        settings = settings.model_copy(update=updates)
        service = NL2SQLService.from_settings(settings)
        try:
            response = await service.ingest(args.docs)
            print(json.dumps(response.model_dump(mode="json"), ensure_ascii=False, indent=2))
        finally:
            await service.close()
        return 0

    service = NL2SQLService.from_settings(settings)
    try:
        if args.command == "health":
            print(json.dumps(service.health(), ensure_ascii=False, indent=2))
            return 0
        if args.command == "knowledge-status":
            print(
                json.dumps(
                    service.index_status().model_dump(mode="json"),
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        if args.command == "evaluate":
            report = await DomainEvaluator(service).run()
            payload = json.dumps(
                report.model_dump(mode="json"),
                ensure_ascii=False,
                indent=2,
            )
            print(payload)
            if args.output is not None:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(payload + "\n", encoding="utf-8")
            return 0 if report.pass_rate == 1.0 else 1
        if args.command == "production-check":
            report = await service.production_check()
            print(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2))
            return 0 if report.ready_for_production else 1
        response = await service.query(
            QueryRequest(
                question=args.question,
                execute=args.execute,
                include_context=args.include_context,
            )
        )
        print(json.dumps(response.model_dump(mode="json"), ensure_ascii=False, indent=2))
        if response.status == "needs_clarification":
            return 2
        return 0 if response.status in {"sql_generated", "executed"} else 1
    finally:
        await service.close()


def main() -> None:
    raise SystemExit(asyncio.run(_run(_parser().parse_args())))


if __name__ == "__main__":
    main()
