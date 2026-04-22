from __future__ import annotations

from argparse import ArgumentParser

import uvicorn
from fastapi import FastAPI

from router import router


app = FastAPI()
app.include_router(router)


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
