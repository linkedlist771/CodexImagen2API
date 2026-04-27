from __future__ import annotations


class RequestError(Exception):
    """请求失败或没有拿到图片结果时抛出"""

    pass


class RateLimitError(RequestError):
    def __init__(self, message: str, retry_after_seconds: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds
