from __future__ import annotations

import asyncio
import base64
import json
import shutil
import threading
import time
from pathlib import Path
from typing import Any

import httpx
from loguru import logger

from config import AUTHEN_DIR
from config import AUTH_FILE_PATTERN
from config import CLIENT_ID
from config import DEFAULT_AUTH_FILE
from config import HOME_AUTH_PATH
from config import ORIGINATOR
from config import REFRESH_TOKEN_URL
from cooldowns import auth_cooldown_key
from cooldowns import get_active_auth_cooldowns
from exceptions import RequestError

_auth_file_index = 0
_auth_file_lock = threading.Lock()


def jwt_claim(jwt_value: str | None, claim_name: str) -> Any:
    if not jwt_value:
        return None

    parts = jwt_value.split(".")
    if len(parts) != 3:
        return None

    payload = parts[1]
    padding = "=" * ((4 - len(payload) % 4) % 4)

    try:
        decoded = base64.urlsafe_b64decode(payload + padding)
        body = json.loads(decoded)
    except Exception:
        return None

    auth_claims = body.get("https://api.openai.com/auth") or {}
    if claim_name in auth_claims:
        return auth_claims[claim_name]

    return body.get(claim_name)


def discover_auth_files() -> list[Path]:
    return sorted(path for path in AUTHEN_DIR.glob(AUTH_FILE_PATTERN) if path.is_file())


async def ensure_auth_files() -> list[Path]:
    auth_files = discover_auth_files()
    if auth_files:
        return auth_files

    if not HOME_AUTH_PATH.exists():
        raise FileNotFoundError(
            f"no auth JSON files found in {AUTHEN_DIR} and auth file not found: {HOME_AUTH_PATH}"
        )

    await asyncio.to_thread(shutil.copy2, HOME_AUTH_PATH, DEFAULT_AUTH_FILE)
    logger.debug("copied auth file from {} to {}", HOME_AUTH_PATH, DEFAULT_AUTH_FILE)
    return discover_auth_files()


async def auth_file_count() -> int:
    return len(await ensure_auth_files())


async def next_auth_file() -> Path:
    global _auth_file_index

    auth_files = await ensure_auth_files()
    if not auth_files:
        raise FileNotFoundError(f"no auth JSON files found in {AUTHEN_DIR}")

    now = time.time()
    cooldowns = await asyncio.to_thread(get_active_auth_cooldowns, auth_files, now)

    with _auth_file_lock:
        for offset in range(len(auth_files)):
            index = (_auth_file_index + offset) % len(auth_files)
            auth_path = auth_files[index]
            cooldown_until = cooldowns.get(auth_cooldown_key(auth_path))
            if cooldown_until is None:
                _auth_file_index = index + 1
                return auth_path

        auth_path = min(auth_files, key=lambda path: cooldowns[auth_cooldown_key(path)])
        cooldown_remaining = max(cooldowns[auth_cooldown_key(auth_path)] - now, 0)

    logger.warning(
        "all auth files are cooling down next_auth_path={} cooldown_remaining_ms={:.2f}",
        auth_path,
        cooldown_remaining * 1000,
    )
    raise RequestError(
        f"all auth files are cooling down; next account is available in {cooldown_remaining:.3f}s"
    )


async def load_auth() -> dict[str, Any]:
    auth_path = await next_auth_file()
    raw_text = await asyncio.to_thread(auth_path.read_text)
    try:
        raw_data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise RequestError(f"{auth_path} contains invalid JSON: {exc}") from exc

    tokens = raw_data.get("tokens") or {}
    access_token = tokens.get("access_token")

    if not access_token:
        raise RequestError(f"{auth_path} does not contain tokens.access_token")

    id_token = tokens.get("id_token")
    account_id = tokens.get("account_id") or jwt_claim(id_token, "chatgpt_account_id")

    return {
        "auth_path": auth_path,
        "raw_data": raw_data,
        "auth_mode": raw_data.get("auth_mode"),
        "access_token": access_token,
        "refresh_token": tokens.get("refresh_token"),
        "account_id": account_id,
        "id_token": id_token,
        "is_fedramp_account": bool(jwt_claim(id_token, "chatgpt_account_is_fedramp")),
    }


async def save_auth(auth: dict[str, Any]) -> None:
    payload = json.dumps(auth["raw_data"], indent=2) + "\n"
    await asyncio.to_thread(auth["auth_path"].write_text, payload)


async def refresh_access_token(
    client: httpx.AsyncClient,
    auth: dict[str, Any],
    request_id: str | None = None,
) -> None:
    refresh_token = auth.get("refresh_token")
    if not refresh_token:
        raise RequestError(
            f"received 401 but no refresh token is available in {auth['auth_path']}"
        )

    logger.warning(
        "refreshing access token request_id={} auth_path={}",
        request_id or "-",
        auth["auth_path"],
    )

    response = await client.post(
        REFRESH_TOKEN_URL,
        headers={
            "Content-Type": "application/json",
            "originator": ORIGINATOR,
            "User-Agent": f"{ORIGINATOR}/codex-image-server",
        },
        json={
            "client_id": CLIENT_ID,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
    )

    if response.status_code >= 400:
        raise RequestError(
            f"token refresh failed with HTTP {response.status_code}: {response.text[:500]}"
        )

    data = response.json()
    tokens = auth["raw_data"].setdefault("tokens", {})

    if data.get("access_token"):
        tokens["access_token"] = data["access_token"]
        auth["access_token"] = data["access_token"]

    if data.get("refresh_token"):
        tokens["refresh_token"] = data["refresh_token"]
        auth["refresh_token"] = data["refresh_token"]

    if data.get("id_token"):
        tokens["id_token"] = data["id_token"]
        auth["id_token"] = data["id_token"]
        auth["account_id"] = tokens.get("account_id") or jwt_claim(
            data["id_token"], "chatgpt_account_id"
        )
        auth["is_fedramp_account"] = bool(
            jwt_claim(data["id_token"], "chatgpt_account_is_fedramp")
        )

    await save_auth(auth)
    logger.debug(
        "access token refreshed request_id={} auth_path={} has_refresh_token={} has_account_id={}",
        request_id or "-",
        auth["auth_path"],
        bool(auth.get("refresh_token")),
        bool(auth.get("account_id")),
    )
