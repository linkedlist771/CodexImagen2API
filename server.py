from __future__ import annotations

import time
from argparse import ArgumentParser
from uuid import uuid4

import uvicorn
from fastapi import FastAPI
from fastapi import Request
from loguru import logger

from logging_utils import configure_logger
from router import router


configure_logger()

app = FastAPI()
app.include_router(router)


@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or uuid4().hex
    client = request.client.host if request.client else "-"
    start_time = time.perf_counter()

    request.state.request_id = request_id

    logger.debug(
        "http request start request_id={} client={} method={} path={}",
        request_id,
        client,
        request.method,
        request.url.path,
    )

    try:
        response = await call_next(request)
    except Exception:
        duration_ms = (time.perf_counter() - start_time) * 1000
        logger.exception(
            "http request error request_id={} client={} method={} path={} duration_ms={:.2f}",
            request_id,
            client,
            request.method,
            request.url.path,
            duration_ms,
        )
        raise

    duration_ms = (time.perf_counter() - start_time) * 1000
    response.headers["x-request-id"] = request_id

    logger.debug(
        "http request end request_id={} status={} duration_ms={:.2f}",
        request_id,
        response.status_code,
        duration_ms,
    )

    return response


def parse_args():
    parser = ArgumentParser(description="Codex image generation server")
    parser.add_argument(
        "--host",
        type=str,
        default="0.0.0.0",
        help="Host to bind the server",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port to bind the server",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Number of worker processes",
    )
    return parser.parse_args()


def start_server():
    args = parse_args()
    logger.info(
        "starting server host={} port={} workers={}",
        args.host,
        args.port,
        args.workers,
    )
    config = uvicorn.Config(
        app,
        host=args.host,
        port=args.port,
        log_level="info",
        workers=args.workers,
    )
    server = uvicorn.Server(config)
    server.run()


if __name__ == "__main__":
    start_server()
