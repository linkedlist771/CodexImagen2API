from __future__ import annotations

import base64
import json
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
from config import DEFAULT_REASONING
from config import HTTP_TIMEOUT
from config import ORIGINATOR
from exceptions import RequestError
from utils import read_config_value
from utils import save_generated_image


def default_base_url(auth_mode: str | None) -> str:
    if auth_mode in {"chatgpt", "chatgpt_auth_tokens", "agent_identity"}:
        return DEFAULT_CHATGPT_BASE_URL
    return DEFAULT_API_BASE_URL


def responses_url(base_url: str) -> str:
    return base_url.rstrip("/") + "/responses"


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
        "tools": [{"type": "image_generation", "output_format": "png"}],
        "tool_choice": "force",
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
    auth = await load_auth()
    base_url = read_config_value(CONFIG_PATH, "base_url") or default_base_url(
        auth.get("auth_mode")
    )
    backend_model = resolve_backend_model()
    installation_id = str(uuid4())
    window_id = str(uuid4())
    conversation_id = str(uuid4())
    url = responses_url(base_url)
    headers = build_headers(auth, conversation_id, installation_id, window_id)

    if images:
        content = image_edit_content(prompt, images)
    else:
        content = text_to_image_content(prompt)

    logger.debug(
        "prepare image request request_id={} model={} auth_mode={} base_url={} prompt_chars={} images={}",
        request_id or conversation_id,
        backend_model,
        auth.get("auth_mode"),
        base_url,
        len(prompt),
        len(images),
    )

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
        image_item, assistant_text = await send_request(
            client,
            auth,
            url,
            headers,
            payload,
            request_id=request_id or conversation_id,
        )

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
