# README: Run embedding jobs via `python -m backend.batch.cli embed --manifest <manifest.jsonl> [options]`

"""Batch CLI entrypoints for embedding operations.

Usage examples:

    python -m backend.batch.cli embed --manifest path/to/manifest.jsonl
    python -m backend.batch.cli embed --manifest m.jsonl --profile standard_profile --update-alias --dry-run
    python -m backend.batch.cli embed --manifest m.jsonl --evaluate backend/ingest/golden_queries.yaml
"""
from __future__ import annotations

import argparse
import logging

logger = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Embedding batch toolkit",
        epilog="DB writes require Oracle deps. Use --dry-run to preview without DB.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    embed_parser = subparsers.add_parser("embed", help="Run an embedding job")
    embed_parser.add_argument("--manifest", required=True, help="Path to JSONL manifest")
    embed_parser.add_argument("--profile", help="Embedding profile override")
    embed_parser.add_argument(
        "--update-alias",
        action="store_true",
        help="Refresh alias to point to active index",
    )
    embed_parser.add_argument("--dry-run", action="store_true", help="Process without DB writes")
    embed_parser.add_argument("--batch-size", type=int, help="Override batch size")
    embed_parser.add_argument("--workers", type=int, help="Override worker count")
    embed_parser.add_argument(
        "--evaluate",
        dest="evaluate_path",
        help="Run golden query evaluation after ingestion",
    )
    embed_parser.set_defaults(command_handler=_handle_embed)

    return parser


def _handle_embed(args: argparse.Namespace) -> None:
    from backend.batch.embed_job import format_summary, run_embed_job

    logger.info(
        "Starting embed job: manifest=%s profile=%s dry_run=%s update_alias=%s batch_size=%s workers=%s evaluate=%s",
        args.manifest,
        args.profile,
        args.dry_run,
        args.update_alias,
        args.batch_size,
        args.workers,
        args.evaluate_path,
    )
    summary = run_embed_job(
        manifest_path=args.manifest,
        profile_name=args.profile,
        dry_run=args.dry_run,
        update_alias=args.update_alias,
        batch_size_override=args.batch_size,
        max_workers=args.workers,
        evaluate_path=args.evaluate_path,
    )
    print(f"Embed job complete: {format_summary(summary)}")


def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)
    command_handler = getattr(args, "command_handler", None)
    if not command_handler:
        parser.error("No command provided")
    command_handler(args)


if __name__ == "__main__":
    main()
