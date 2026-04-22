from __future__ import annotations

import asyncio
import base64
import json
import shutil
from pathlib import Path
from typing import Any

import httpx

from config import AUTH_FILE
from config import CLIENT_ID
from config import HOME_AUTH_PATH
from config import ORIGINATOR
from config import REFRESH_TOKEN_URL
from exceptions import RequestError


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


async def ensure_auth_file() -> Path:
    if AUTH_FILE.exists():
        return AUTH_FILE

    if not HOME_AUTH_PATH.exists():
        raise FileNotFoundError(f"auth file not found: {HOME_AUTH_PATH}")

    await asyncio.to_thread(shutil.copy2, HOME_AUTH_PATH, AUTH_FILE)
    return AUTH_FILE


async def load_auth() -> dict[str, Any]:
    auth_path = await ensure_auth_file()
    raw_text = await asyncio.to_thread(auth_path.read_text)
    raw_data = json.loads(raw_text)
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
) -> None:
    refresh_token = auth.get("refresh_token")
    if not refresh_token:
        raise RequestError(
            "received 401 but no refresh token is available in authens/auth_state.json"
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
