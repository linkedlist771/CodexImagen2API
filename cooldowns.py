from __future__ import annotations

import threading
import time
from pathlib import Path

from sqlalchemy import Column
from sqlalchemy import Float
from sqlalchemy import MetaData
from sqlalchemy import String
from sqlalchemy import Table
from sqlalchemy import create_engine
from sqlalchemy import delete
from sqlalchemy import func
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.engine import URL

from config import AUTH_COOLDOWN_DB_PATH

metadata = MetaData()

auth_cooldowns = Table(
    "auth_cooldowns",
    metadata,
    Column("auth_path", String, primary_key=True),
    Column("cooldown_until", Float, nullable=False, index=True),
    Column("updated_at", Float, nullable=False),
    Column("reason", String, nullable=True),
)

_engine = create_engine(
    URL.create("sqlite+pysqlite", database=str(AUTH_COOLDOWN_DB_PATH)),
    future=True,
)
_initialized = False
_init_lock = threading.Lock()


def init_cooldown_db() -> None:
    global _initialized

    if _initialized:
        return

    with _init_lock:
        if _initialized:
            return
        metadata.create_all(_engine)
        _initialized = True


def auth_cooldown_key(auth_path: Path) -> str:
    return str(auth_path.resolve())


def get_active_auth_cooldowns(
    auth_paths: list[Path],
    now: float | None = None,
) -> dict[str, float]:
    if not auth_paths:
        return {}

    init_cooldown_db()
    checked_at = now if now is not None else time.time()
    auth_keys = [auth_cooldown_key(path) for path in auth_paths]

    with _engine.begin() as connection:
        connection.execute(delete(auth_cooldowns).where(auth_cooldowns.c.cooldown_until <= checked_at))
        rows = connection.execute(
            select(auth_cooldowns.c.auth_path, auth_cooldowns.c.cooldown_until).where(
                auth_cooldowns.c.auth_path.in_(auth_keys),
                auth_cooldowns.c.cooldown_until > checked_at,
            )
        ).all()

    return {row.auth_path: row.cooldown_until for row in rows}


def set_auth_cooldown(
    auth_path: Path,
    cooldown_seconds: float,
    reason: str,
    now: float | None = None,
) -> float:
    init_cooldown_db()
    updated_at = now if now is not None else time.time()
    cooldown_until = updated_at + max(cooldown_seconds, 0)
    stmt = insert(auth_cooldowns).values(
        auth_path=auth_cooldown_key(auth_path),
        cooldown_until=cooldown_until,
        updated_at=updated_at,
        reason=reason,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[auth_cooldowns.c.auth_path],
        set_={
            "cooldown_until": func.max(auth_cooldowns.c.cooldown_until, cooldown_until),
            "updated_at": updated_at,
            "reason": reason,
        },
    )

    with _engine.begin() as connection:
        connection.execute(stmt)
        stored_until = connection.execute(
            select(auth_cooldowns.c.cooldown_until).where(
                auth_cooldowns.c.auth_path == auth_cooldown_key(auth_path)
            )
        ).scalar_one()

    return float(stored_until)
