from __future__ import annotations

import os
import sys

from loguru import logger

from config import LOG_DIR


def configure_logger() -> None:
    level = os.getenv("CODEX_IMAGE_SERVER_LOG_LEVEL", "INFO").upper()

    logger.remove()
    logger.add(
        sys.stderr,
        level=level,
        enqueue=True,
        backtrace=False,
        diagnose=False,
    )
    logger.add(
        LOG_DIR / "server.log",
        level=level,
        enqueue=True,
        backtrace=False,
        diagnose=False,
        rotation="10 MB",
        retention="14 days",
        encoding="utf-8",
    )


def preview_text(text: str, limit: int = 80) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed

    return collapsed[: limit - 3] + "..."
