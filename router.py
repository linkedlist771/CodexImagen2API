from __future__ import annotations

import time
from typing import Any
from typing import Dict
from typing import List
from typing import Union
from uuid import uuid4

from fastapi import APIRouter
from fastapi import HTTPException
from pydantic import BaseModel
from pydantic import ConfigDict

from api import prompt_to_image_result
from exceptions import RequestError
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
async def chat_completions(payload: ChatCompletionRequest):
    if payload.stream:
        raise HTTPException(status_code=400, detail="stream is not supported")

    prompt, images = extract_prompt_and_images(payload.messages)

    try:
        result = await prompt_to_image_result(prompt, images, payload.model)
    except RequestError as error:
        raise HTTPException(status_code=502, detail=str(error))

    markdown_content = await image_path_to_markdown(result["image_path"])

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
