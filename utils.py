from __future__ import annotations

import asyncio
import base64
import mimetypes
import re
import zlib
from pathlib import Path
from uuid import uuid4

from config import IMAGE_SAVE_DIR


def read_config_value(path: Path, key: str) -> str | None:
    if not path.exists():
        return None

    text = path.read_text()
    match = re.search(rf'(?m)^\s*{re.escape(key)}\s*=\s*"([^"]+)"', text)
    if not match:
        return None

    return match.group(1)


def image_file_to_data_url(path: Path) -> str:
    mime_type = mimetypes.guess_type(path.name)[0] or "image/png"
    encoded = base64.b64encode(path.read_bytes()).decode("utf-8")
    return f"data:{mime_type};base64,{encoded}"


def image_bytes_to_data_url(image_bytes: bytes, suffix: str = ".png") -> str:
    mime_type = mimetypes.guess_type(f"image{suffix}")[0] or "image/png"
    encoded = base64.b64encode(image_bytes).decode("utf-8")
    return f"data:{mime_type};base64,{encoded}"


async def image_path_to_markdown(path: Path) -> str:
    image_bytes = await asyncio.to_thread(path.read_bytes)
    suffix = path.suffix or ".png"
    data_url = image_bytes_to_data_url(image_bytes, suffix)
    return f"![image]({data_url})"


def extract_image_bytes(markdown_content: str) -> tuple[bytes, str]:
    match = re.search(
        r"!\[image\]\((data:(image/[\w.+-]+);base64,([A-Za-z0-9+/=]+))\)",
        markdown_content,
    )
    if not match:
        raise ValueError("响应中没有找到 Markdown base64 图片数据")

    mime_type = match.group(2)
    image_bytes = base64.b64decode(match.group(3))
    suffix = mimetypes.guess_extension(mime_type) or ".png"
    return image_bytes, suffix


def png_chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        len(payload).to_bytes(4, "big")
        + kind
        + payload
        + zlib.crc32(kind + payload).to_bytes(4, "big")
    )


def make_reference_png(path: Path, width: int = 256, height: int = 256) -> None:
    rows = bytearray()

    for y in range(height):
        rows.append(0)
        for x in range(width):
            sky_blend = y / max(height - 1, 1)
            r = int(250 - sky_blend * 140)
            g = int(120 - sky_blend * 70)
            b = int(50 + sky_blend * 150)

            if y > height * 0.62:
                water_blend = (y - height * 0.62) / max(height * 0.38, 1)
                r = int(30 + water_blend * 20)
                g = int(70 + water_blend * 40)
                b = int(110 + water_blend * 60)

            sun_dx = x - width * 0.5
            sun_dy = y - height * 0.36
            if sun_dx * sun_dx + sun_dy * sun_dy < (width * 0.12) ** 2:
                r, g, b = 255, 240, 170

            if abs(y - height * 0.62) < 2:
                r, g, b = 255, 210, 150

            if abs(x - (width * 0.2 + y * 0.3)) < 3 and y > height * 0.25:
                r, g, b = 30, 25, 40

            rows.extend((r, g, b, 255))

    ihdr = width.to_bytes(4, "big") + height.to_bytes(4, "big") + bytes([8, 6, 0, 0, 0])
    png_bytes = b"".join(
        [
            b"\x89PNG\r\n\x1a\n",
            png_chunk(b"IHDR", ihdr),
            png_chunk(b"IDAT", zlib.compress(bytes(rows), level=9)),
            png_chunk(b"IEND", b""),
        ]
    )
    path.write_bytes(png_bytes)


async def create_reference_png(path: Path, width: int = 256, height: int = 256) -> Path:
    await asyncio.to_thread(make_reference_png, path, width, height)
    return path


async def save_generated_image(image_bytes: bytes) -> Path:
    output_path = IMAGE_SAVE_DIR / f"{uuid4().hex}.png"
    await asyncio.to_thread(output_path.write_bytes, image_bytes)
    return output_path


async def save_output_image(
    output_dir: Path,
    output_name: str,
    image_bytes: bytes,
    suffix: str = ".png",
) -> Path:
    output_dir.mkdir(exist_ok=True, parents=True)
    output_path = output_dir / f"{output_name}{suffix}"
    await asyncio.to_thread(output_path.write_bytes, image_bytes)
    return output_path
