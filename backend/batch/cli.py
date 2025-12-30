# README: Run embedding jobs via `python -m backend.batch.cli embed --manifest <manifest.jsonl> [options]`

"""Batch CLI entrypoints for embedding operations.

Usage examples:

    python -m backend.batch.cli embed --manifest path/to/manifest.jsonl
    python -m backend.batch.cli embed --manifest m.jsonl --profile standard_profile --update-alias --dry-run
    python -m backend.batch.cli embed --manifest m.jsonl --evaluate backend/ingest/golden_queries.yaml
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Dict, Any, List

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
    embed_parser.add_argument(
        "--domain-key",
        dest="domain_key",
        help="Override embedding target (index + alias) via embeddings.domains.<key>",
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
    from backend.app.deps import settings
    from backend.ingest.manifests.spec import validate_and_expand_manifest
    from backend.ingest.router import route_and_load
    from backend.ingest.normalizer import normalize_metadata
    from backend.ingest.chunking.char_chunker import chunk_text
    from backend.ingest.chunking.token_chunker import chunk_text_by_tokens

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
    if args.dry_run:
        # Preflight: expand, load, normalize, chunk; print content_type summary
        app_settings = settings.app
        embeddings_cfg = app_settings.get("embeddings", {}) or {}
        profile_name = args.profile or embeddings_cfg.get("active_profile")
        if not profile_name:
            raise ValueError("No embedding profile specified")
        profiles = embeddings_cfg.get("profiles", {}) or {}
        profile_cfg = profiles.get(profile_name) or {}
        chunker_cfg = profile_cfg.get("chunker", {}) or {}

        files = validate_and_expand_manifest(args.manifest)
        print(f"[dry-run] Files: {len(files)}")

        item_counts: Dict[str, int] = {k: 0 for k in ("pdf", "docx", "pptx", "xlsx", "html", "txt")}
        chunk_counts: Dict[str, int] = {k: 0 for k in ("pdf", "docx", "pptx", "xlsx", "html", "txt")}

        for f in files:
            try:
                items = route_and_load(f)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to load %s: %s", f, exc)
                continue
            for it in items:
                try:
                    norm = normalize_metadata(it)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Bad metadata %s: %s", f, exc)
                    continue
                ctype = str(norm["metadata"].get("content_type", "")).lower()
                # Map to simple keys
                key = (
                    "pdf" if "pdf" in ctype else
                    "pptx" if ("presentation" in ctype or "ppt" in ctype) else
                    "xlsx" if ("spreadsheet" in ctype or "xlsx" in ctype) else
                    "html" if "html" in ctype else
                    "docx" if ("wordprocessingml" in ctype or "docx" in ctype) else
                    "txt"
                )
                item_counts[key] += 1
                text = (norm.get("text") or "").strip()
                if not text:
                    continue
                if (chunker_cfg.get("type") or "char").lower() == "tokens":
                    max_tokens = int(chunker_cfg.get("size", 900) or 900)
                    ov = float(chunker_cfg.get("overlap", 0.15) or 0.0)
                    chunks = chunk_text_by_tokens(text, max_tokens=max_tokens, overlap=ov)
                else:
                    size = int(chunker_cfg.get("size", 2000) or 2000)
                    ov = int(chunker_cfg.get("overlap", 100) or 0)
                    chunks = chunk_text(text, size=size, overlap=ov)
                chunk_counts[key] += len(chunks)

        # Print summary table
        print("\n[dry-run] Content-Type Summary")
        print("TYPE    ITEMS  CHUNKS")
        for k in ("pdf", "docx", "pptx", "xlsx", "html", "txt"):
            ic = item_counts.get(k, 0)
            cc = chunk_counts.get(k, 0)
            if ic == 0 and cc == 0:
                continue
            print(f"{k:<6} {ic:>6} {cc:>7}")
        return

    # Normal (non dry-run) path executes embeddings/upsert
    try:
        summary = run_embed_job(
            manifest_path=args.manifest,
            profile_name=args.profile,
            domain_key=args.domain_key,
            dry_run=args.dry_run,
            update_alias=args.update_alias,
            batch_size_override=args.batch_size,
            max_workers=args.workers,
            evaluate_path=args.evaluate_path,
        )
    except Exception:
        logger.exception("Embed CLI failed unexpectedly")
        print(json.dumps({"ok": False, "error": "embed_failed_unexpectedly"}, indent=2))
        sys.exit(2)
    logger.info("Embed job complete: %s", format_summary(summary))
    payload = asdict(summary)
    payload["ok"] = True
    print(json.dumps(payload, indent=2, default=str))


def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)
    command_handler = getattr(args, "command_handler", None)
    if not command_handler:
        parser.error("No command provided")
    command_handler(args)


if __name__ == "__main__":
    main()
