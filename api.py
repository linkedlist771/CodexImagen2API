from __future__ import annotations

import asyncio
import base64
import json
import re
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from loguru import logger

from auth import load_auth
from auth import refresh_access_token
from config import CONFIG_PATH
from config import DEFAULT_API_BASE_URL
from config import DEFAULT_CHATGPT_BASE_URL
from config import DEFAULT_INSTRUCTIONS
from config import DEFAULT_MODEL
from config import DEFAULT_RATE_LIMIT_COOLDOWN_SECONDS
from config import DEFAULT_REASONING
from config import HTTP_TIMEOUT
from config import ORIGINATOR
from config import REQUEST_AUTH_RETRY_COUNT
from cooldowns import set_auth_cooldown
from exceptions import RateLimitError
from exceptions import RequestError
from utils import read_config_value
from utils import save_generated_image


def default_base_url(auth_mode: str | None) -> str:
    if auth_mode in {"chatgpt", "chatgpt_auth_tokens", "agent_identity"}:
        return DEFAULT_CHATGPT_BASE_URL
    return DEFAULT_API_BASE_URL


def responses_url(base_url: str) -> str:
    return base_url.rstrip("/") + "/responses"


def parse_rate_limit_retry_after(message: str) -> float | None:
    match = re.search(r"Please try again in (\d+(?:\.\d+)?)\s*(ms|s|sec|second|seconds)\b", message)
    if not match:
        return None

    value = float(match.group(1))
    unit = match.group(2)
    if unit == "ms":
        return value / 1000
    return value


def rate_limit_error_from_payload(payload: dict[str, Any]) -> RateLimitError | None:
    error = payload.get("error") or (payload.get("response") or {}).get("error") or {}
    if error.get("code") != "rate_limit_exceeded":
        return None

    message = error.get("message") or json.dumps(payload, ensure_ascii=False)
    return RateLimitError(message, parse_rate_limit_retry_after(message))


def rate_limit_error_from_response(status_code: int, body: str) -> RateLimitError | None:
    if status_code != 429 and "rate_limit_exceeded" not in body:
        return None

    message = body
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        payload = None

    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            message = error.get("message") or body
        elif payload.get("code") == "rate_limit_exceeded":
            message = payload.get("message") or body
        elif status_code != 429:
            return None

    return RateLimitError(message[:1000], parse_rate_limit_retry_after(message))


async def mark_auth_rate_limited(
    auth_path: Path,
    cooldown_seconds: float,
) -> float:
    return await asyncio.to_thread(
        set_auth_cooldown,
        auth_path,
        cooldown_seconds,
        "rate_limit_exceeded",
    )


def build_headers(
    auth: dict[str, Any],
    conversation_id: str,
    installation_id: str,
    window_id: str,
) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {auth['access_token']}",
        "Accept": "text/event-stream",
        "originator": ORIGINATOR,
        "User-Agent": f"{ORIGINATOR}/codex-image-server",
        "x-client-request-id": conversation_id,
        "session_id": conversation_id,
        "x-codex-installation-id": installation_id,
        "x-codex-window-id": window_id,
    }

    if auth.get("account_id"):
        headers["ChatGPT-Account-ID"] = auth["account_id"]

    if auth.get("is_fedramp_account"):
        headers["X-OpenAI-Fedramp"] = "true"

    return headers


def text_to_image_content(prompt: str) -> list[dict[str, Any]]:
    return [{"type": "input_text", "text": prompt}]


def image_edit_content(prompt: str, image_urls: list[str]) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = []

    for image_url in image_urls:
        content.extend(
            [
                {"type": "input_text", "text": "<image>"},
                {"type": "input_image", "image_url": image_url, "detail": "high"},
                {"type": "input_text", "text": "</image>"},
            ]
        )

    if prompt:
        content.append({"type": "input_text", "text": prompt})

    return content


def build_request_payload(
    model: str,
    conversation_id: str,
    installation_id: str,
    content: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "model": model,
        "instructions": DEFAULT_INSTRUCTIONS,
        "input": [
            {
                "type": "message",
                "role": "user",
                "content": content,
            }
        ],
        "tools": [{"type": "image_generation", "output_format": "png", "quality": "high", "background": "auto"}],
        "tool_choice": "required",
        "parallel_tool_calls": False,
        "reasoning": DEFAULT_REASONING,
        "store": False,
        "stream": True,
        "include": ["reasoning.encrypted_content"],
        "prompt_cache_key": conversation_id,
        "client_metadata": {
            "x-codex-installation-id": installation_id,
        },
    }


def handle_sse_payload(
    payload: dict[str, Any],
    image_item: dict[str, Any] | None,
    assistant_text: list[str],
) -> dict[str, Any] | None:
    if payload.get("type") == "response.failed":
        rate_limit_error = rate_limit_error_from_payload(payload)
        if rate_limit_error:
            raise rate_limit_error
        raise RequestError(json.dumps(payload, ensure_ascii=False))

    if payload.get("type") != "response.output_item.done":
        return image_item

    item = payload.get("item") or {}
    if item.get("type") == "image_generation_call":
        return item

    if item.get("type") == "message":
        for content_item in item.get("content") or []:
            if content_item.get("type") == "output_text":
                assistant_text.append(content_item.get("text", ""))

    return image_item


async def parse_sse_stream(
    response: httpx.Response,
) -> tuple[dict[str, Any] | None, list[str]]:
    image_item = None
    assistant_text: list[str] = []
    data_lines: list[str] = []

    async for line in response.aiter_lines():
        if line.startswith(":"):
            continue

        if line.startswith("event:"):
            continue

        if line.startswith("data:"):
            data_lines.append(line.split(":", 1)[1].lstrip())
            continue

        if line != "":
            continue

        if data_lines:
            payload = json.loads("\n".join(data_lines))
            image_item = handle_sse_payload(payload, image_item, assistant_text)

        data_lines = []

    if data_lines:
        payload = json.loads("\n".join(data_lines))
        image_item = handle_sse_payload(payload, image_item, assistant_text)

    return image_item, assistant_text


async def send_request(
    client: httpx.AsyncClient,
    auth: dict[str, Any],
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    request_id: str | None = None,
) -> tuple[dict[str, Any], list[str]]:
    for attempt in range(2):
        logger.debug(
            "responses request attempt={} url={} model={} request_id={}",
            attempt + 1,
            url,
            payload["model"],
            request_id or "-",
        )

        async with client.stream(
            "POST", url, headers=headers, json=payload
        ) as response:
            if response.status_code == 401 and attempt == 0:
                logger.warning(
                    "responses request unauthorized request_id={} attempt={} action=refresh_access_token",
                    request_id or "-",
                    attempt + 1,
                )
                await refresh_access_token(client, auth, request_id=request_id)
                headers["Authorization"] = f"Bearer {auth['access_token']}"
                if auth.get("account_id"):
                    headers["ChatGPT-Account-ID"] = auth["account_id"]
                continue

            if response.status_code >= 400:
                body = (await response.aread()).decode("utf-8", "replace")
                logger.error(
                    "responses request failed status={} request_id={} body={}",
                    response.status_code,
                    request_id or "-",
                    body[:1000],
                )
                rate_limit_error = rate_limit_error_from_response(response.status_code, body)
                if rate_limit_error:
                    raise rate_limit_error
                raise RequestError(
                    f"responses request failed with HTTP {response.status_code}: {body[:1000]}"
                )

            image_item, assistant_text = await parse_sse_stream(response)
            if image_item is None:
                raise RequestError(
                    "stream finished without an image_generation_call item; "
                    f"assistant text was: {' '.join(assistant_text).strip()!r}"
                )

            logger.debug(
                "responses stream complete request_id={} image_call_id={} output_status={} assistant_text_chars={}",
                request_id or "-",
                image_item.get("id"),
                image_item.get("status"),
                len("".join(assistant_text).strip()),
            )

            return image_item, assistant_text

    raise RequestError("request retry loop exhausted")


def resolve_backend_model() -> str:
    return read_config_value(CONFIG_PATH, "model") or DEFAULT_MODEL


async def prompt_to_image_result(
    prompt: str,
    images: list[str],
    requested_model: str | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    backend_model = resolve_backend_model()
    installation_id = str(uuid4())
    window_id = str(uuid4())
    conversation_id = str(uuid4())

    if images:
        content = image_edit_content(prompt, images)
    else:
        content = text_to_image_content(prompt)

    payload = build_request_payload(
        backend_model,
        conversation_id,
        installation_id,
        content,
    )

    async with httpx.AsyncClient(
        timeout=HTTP_TIMEOUT,
        follow_redirects=True,
    ) as client:
        for auth_attempt in range(REQUEST_AUTH_RETRY_COUNT):
            auth: dict[str, Any] | None = None
            try:
                auth = await load_auth()
                base_url = read_config_value(CONFIG_PATH, "base_url") or default_base_url(
                    auth.get("auth_mode")
                )
                url = responses_url(base_url)
                headers = build_headers(
                    auth,
                    conversation_id,
                    installation_id,
                    window_id,
                )

                logger.debug(
                    "prepare image request request_id={} model={} auth_attempt={}/{} auth_path={} auth_mode={} base_url={} prompt_chars={} images={}",
                    request_id or conversation_id,
                    backend_model,
                    auth_attempt + 1,
                    REQUEST_AUTH_RETRY_COUNT,
                    auth["auth_path"],
                    auth.get("auth_mode"),
                    base_url,
                    len(prompt),
                    len(images),
                )

                image_item, assistant_text = await send_request(
                    client,
                    auth,
                    url,
                    headers,
                    payload,
                    request_id=request_id or conversation_id,
                )
                break
            except RateLimitError as exc:
                if auth is None:
                    raise

                cooldown_seconds = exc.retry_after_seconds or DEFAULT_RATE_LIMIT_COOLDOWN_SECONDS
                auth_path = auth["auth_path"]
                cooldown_until = await mark_auth_rate_limited(auth_path, cooldown_seconds)
                logger.warning(
                    "image request rate limited request_id={} auth_attempt={}/{} auth_path={} cooldown_seconds={:.3f} cooldown_until={} error={}",
                    request_id or conversation_id,
                    auth_attempt + 1,
                    REQUEST_AUTH_RETRY_COUNT,
                    auth_path,
                    cooldown_seconds,
                    cooldown_until,
                    exc,
                )
                if auth_attempt + 1 >= REQUEST_AUTH_RETRY_COUNT:
                    raise RequestError(
                        f"image request failed after {REQUEST_AUTH_RETRY_COUNT} auth attempts: {exc}"
                    ) from exc
            except RequestError as exc:
                logger.warning(
                    "image request failed request_id={} auth_attempt={}/{} error={}",
                    request_id or conversation_id,
                    auth_attempt + 1,
                    REQUEST_AUTH_RETRY_COUNT,
                    exc,
                )
                if auth_attempt + 1 >= REQUEST_AUTH_RETRY_COUNT:
                    raise RequestError(
                        f"image request failed after {REQUEST_AUTH_RETRY_COUNT} auth attempts: {exc}"
                    ) from exc
        else:
            raise RequestError("image request auth retry loop exhausted")

    image_bytes = base64.b64decode(image_item["result"])
    image_path = await save_generated_image(image_bytes)

    logger.debug(
        "image saved request_id={} image_call_id={} bytes={} path={} revised_prompt_chars={} assistant_text_chars={}",
        request_id or conversation_id,
        image_item.get("id"),
        len(image_bytes),
        image_path,
        len(image_item.get("revised_prompt") or ""),
        len("".join(assistant_text).strip()),
    )

    return {
        "image_path": image_path,
        "image_call_id": image_item.get("id"),
        "status": image_item.get("status"),
        "revised_prompt": image_item.get("revised_prompt"),
        "assistant_text": "".join(assistant_text).strip(),
        "requested_model": requested_model,
        "backend_model": backend_model,
        "base_url": base_url,
    }
