from __future__ import annotations

import time
from typing import Any
from typing import Dict
from typing import List
from typing import Union
from uuid import uuid4

from fastapi import APIRouter
from fastapi import HTTPException
from fastapi import Request
from loguru import logger
from pydantic import BaseModel
from pydantic import ConfigDict

from api import prompt_to_image_result
from exceptions import RequestError
from logging_utils import preview_text
from utils import image_path_to_markdown


router = APIRouter()


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="allow")

    role: str
    content: Union[str, List[Dict[str, Any]]]


class ChatCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str = "gpt-4o-image"
    messages: List[ChatMessage]
    stream: bool = False


def extract_prompt_and_images(messages: list[ChatMessage]) -> tuple[str, list[str]]:
    for message in reversed(messages):
        if message.role != "user":
            continue

        if isinstance(message.content, str):
            prompt = message.content.strip()
            if prompt:
                return prompt, []
            continue

        texts = []
        images = []
        for part in message.content:
            if part.get("type") == "text" and part.get("text"):
                texts.append(part["text"].strip())
            elif part.get("type") == "image_url":
                url = part.get("image_url", {}).get("url", "")
                if url:
                    images.append(url)

        prompt = "\n".join(texts).strip()
        if prompt or images:
            return prompt, images

    raise HTTPException(status_code=400, detail="No user prompt found")


@router.post("/v1/chat/completions")
async def chat_completions(payload: ChatCompletionRequest, request: Request):
    request_id = getattr(request.state, "request_id", uuid4().hex)
    client = request.client.host if request.client else "-"
    started_at = time.perf_counter()

    if payload.stream:
        logger.warning(
            "chat_completions rejected request_id={} client={} model={} reason=stream_not_supported",
            request_id,
            client,
            payload.model,
        )
        raise HTTPException(status_code=400, detail="stream is not supported")

    try:
        prompt, images = extract_prompt_and_images(payload.messages)
    except HTTPException as error:
        logger.warning(
            "chat_completions rejected request_id={} client={} model={} detail={!r}",
            request_id,
            client,
            payload.model,
            error.detail,
        )
        raise

    logger.info(
        "chat_completions request_id={} client={} model={} messages={} prompt_chars={} images={} prompt_preview={!r}",
        request_id,
        client,
        payload.model,
        len(payload.messages),
        len(prompt),
        len(images),
        preview_text(prompt),
    )

    try:
        result = await prompt_to_image_result(
            prompt,
            images,
            payload.model,
            request_id=request_id,
        )
    except RequestError as error:
        duration_ms = (time.perf_counter() - started_at) * 1000
        logger.error(
            "chat_completions failed request_id={} model={} prompt_chars={} images={} duration_ms={:.2f} error={!r}",
            request_id,
            payload.model,
            len(prompt),
            len(images),
            duration_ms,
            str(error),
        )
        raise HTTPException(status_code=502, detail=str(error))

    markdown_content = await image_path_to_markdown(result["image_path"])
    duration_ms = (time.perf_counter() - started_at) * 1000

    logger.info(
        "chat_completions done request_id={} model={} backend_model={} image_call_id={} output_status={} duration_ms={:.2f} image_path={}",
        request_id,
        payload.model,
        result["backend_model"],
        result["image_call_id"],
        result["status"],
        duration_ms,
        result["image_path"],
    )

    return {
        "id": f"chatcmpl-{uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": payload.model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": markdown_content,
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
    }
