from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

LogCallback = Callable[[str], None]


def _build_embed_command(
    manifest_path: Path,
    profile: str,
    update_alias: bool,
    evaluate: bool,
) -> list[str]:
    cmd = [sys.executable, "-m", "backend.batch.cli", "embed", "--manifest", str(manifest_path)]
    if profile:
        cmd.extend(["--profile", profile])
    if update_alias:
        cmd.append("--update-alias")
    if evaluate:
        evaluate_path = os.getenv("INGEST_EVALUATE_PATH")
        if evaluate_path:
            cmd.extend(["--evaluate", evaluate_path])
        else:
            logger.warning("Evaluate flag requested but INGEST_EVALUATE_PATH is not set; skipping evaluation")
    return cmd


def run_embed_job_via_cli(
    manifest_path: Path,
    profile: str,
    update_alias: bool,
    evaluate: bool,
    log_callback: Optional[LogCallback] = None,
) -> int:
    """
    Execute the existing embedding pipeline via its CLI entrypoint.

    Returns the subprocess exit code.
    """
    manifest_path = manifest_path.resolve()
    command = _build_embed_command(manifest_path, profile, update_alias, evaluate)
    logger.info("Launching embed job via CLI: %s", " ".join(command))

    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    assert process.stdout is not None  # for type checkers
    try:
        for line in iter(process.stdout.readline, ""):
            stripped = line.rstrip("\n")
            if log_callback:
                try:
                    log_callback(stripped)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Log callback failed: %s", exc)
            else:
                logger.info("[embed-cli] %s", stripped)
    finally:
        process.stdout.close()

    return_code = process.wait()
    if return_code != 0:
        logger.error("Embed CLI exited with code %s", return_code)
    else:
        logger.info("Embed CLI completed successfully")
    return return_code
