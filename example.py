from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

import httpx

from config import DEFAULT_EDIT_PROMPT
from config import DEFAULT_SERVER_BASE_URL
from config import DEFAULT_TEXT_PROMPT
from config import EXAMPLE_OUTPUT_DIR
from config import HTTP_TIMEOUT
from config import IMAGE_SAVE_DIR
from utils import create_reference_png
from utils import extract_image_bytes
from utils import image_file_to_data_url
from utils import save_output_image


DEFAULT_BASE_URL = DEFAULT_SERVER_BASE_URL
SAMPLE_IMAGE = IMAGE_SAVE_DIR / "reference_input.png"


async def generate_image(
    base_url: str,
    payload: dict,
    output_name: str,
) -> Path:
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        response = await client.post(f"{base_url}/v1/chat/completions", json=payload)
        response.raise_for_status()
        data = response.json()

    markdown_content = data["choices"][0]["message"]["content"]
    image_bytes, suffix = extract_image_bytes(markdown_content)
    return await save_output_image(EXAMPLE_OUTPUT_DIR, output_name, image_bytes, suffix)


async def text_to_image_example(base_url: str) -> Path:
    payload = {
        "model": "gpt-4o-image",
        "stream": False,
        "messages": [
            {
                "role": "user",
                "content": DEFAULT_TEXT_PROMPT,
            }
        ],
    }
    return await generate_image(base_url, payload, "text_to_image")


async def image_edit_example(base_url: str) -> Path:
    if not SAMPLE_IMAGE.exists():
        await create_reference_png(SAMPLE_IMAGE)

    payload = {
        "model": "gpt-4o-image",
        "stream": False,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": DEFAULT_EDIT_PROMPT,
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": image_file_to_data_url(SAMPLE_IMAGE),
                        },
                    },
                ],
            }
        ],
    }
    return await generate_image(base_url, payload, "image_edit")


async def main():
    parser = argparse.ArgumentParser(
        description="Codex image generation example client",
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"API base url, default: {DEFAULT_BASE_URL}",
    )
    parser.add_argument(
        "--mode",
        choices=["text", "edit", "all"],
        default="all",
        help="Run text-to-image, image-edit, or both examples",
    )
    args = parser.parse_args()

    print(f"Using API: {args.base_url}")

    if args.mode in {"text", "all"}:
        text_result = await text_to_image_example(args.base_url)
        print(f"[text] saved to: {text_result}")

    if args.mode in {"edit", "all"}:
        edit_result = await image_edit_example(args.base_url)
        print(f"[edit] saved to: {edit_result}")


if __name__ == "__main__":
    asyncio.run(main())
