"""采集用 HTTP 工具：超时、重试、限速（合规，不绕过反爬）."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.core.config import get_settings

logger = logging.getLogger(__name__)

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9",
}


class RateLimiter:
    """简单全局限速（每源可独立实例）."""

    def __init__(self, min_interval: float) -> None:
        self.min_interval = max(0.0, min_interval)
        self._lock = asyncio.Lock()
        self._last = 0.0

    async def wait(self) -> None:
        async with self._lock:
            now = time.monotonic()
            gap = self.min_interval - (now - self._last)
            if gap > 0:
                await asyncio.sleep(gap)
            self._last = time.monotonic()


class HttpFetcher:
    def __init__(
        self,
        *,
        timeout: float | None = None,
        min_interval: float | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        settings = get_settings()
        self.timeout = timeout if timeout is not None else float(settings.http_timeout)
        self.limiter = RateLimiter(
            min_interval
            if min_interval is not None
            else max(1.0 / max(settings.crawl_rate_limit_per_second, 0.1), 0.5)
        )
        self.headers = {**DEFAULT_HEADERS, **(headers or {})}
        self.max_retries = settings.http_max_retries

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout),
            headers=self.headers,
            follow_redirects=True,
        )

    async def get_text(self, url: str, *, params: dict[str, Any] | None = None) -> str:
        await self.limiter.wait()
        return await self._get_text_retry(url, params=params)

    async def post_form(self, url: str, data: dict[str, Any]) -> httpx.Response:
        await self.limiter.wait()
        return await self._post_form_retry(url, data=data)

    async def get_json(self, url: str, *, params: dict[str, Any] | None = None) -> Any:
        await self.limiter.wait()
        text_resp = await self._get_response_retry(url, params=params)
        return text_resp.json()

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError)),
    )
    async def _get_text_retry(self, url: str, *, params: dict[str, Any] | None) -> str:
        async with self._client() as client:
            resp = await client.get(url, params=params)
            if resp.status_code >= 500:
                resp.raise_for_status()
            resp.raise_for_status()
            resp.encoding = resp.encoding or "utf-8"
            return resp.text

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError)),
    )
    async def _get_response_retry(
        self, url: str, *, params: dict[str, Any] | None
    ) -> httpx.Response:
        async with self._client() as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            return resp

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError)),
    )
    async def _post_form_retry(self, url: str, data: dict[str, Any]) -> httpx.Response:
        async with self._client() as client:
            resp = await client.post(url, data=data)
            resp.raise_for_status()
            return resp
