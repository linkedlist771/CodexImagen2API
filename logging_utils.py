from __future__ import annotations

import os
import sys

from loguru import logger


def configure_logger() -> None:
    level = os.getenv("CODEX_IMAGE_SERVER_LOG_LEVEL", "INFO").upper()

    logger.remove()
    logger.add(
        sys.stderr,
        level=level,
        enqueue=True,
        backtrace=False,
        diagnose=False,
        format=(
            "{time:YYYY-MM-DD HH:mm:ss.SSS} | "
            "{level:<8} | "
            "{message}"
        ),
    )


def preview_text(text: str, limit: int = 80) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed

    return collapsed[: limit - 3] + "..."
