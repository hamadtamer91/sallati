"""Polite HTTP client shared by all adapters: per-host rate limit, retries, robots.txt."""
from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any
from urllib.parse import urlsplit

import httpx

from .robots import Robots

log = logging.getLogger(__name__)

DEFAULT_UA = "SallatiPriceBot/0.1 (+https://example.com/bot; price comparison for Lebanon)"
RETRY_STATUS = {408, 425, 429, 500, 502, 503, 504}


class RobotsDisallowed(RuntimeError):
    pass


class TokenBucket:
    """Async token bucket: `rate` requests per second with a small burst."""

    def __init__(self, rate: float, burst: int = 1):
        self.rate = max(rate, 0.01)
        self.capacity = max(burst, 1)
        self.tokens = float(self.capacity)
        self.updated = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
                self.updated = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                await asyncio.sleep((1 - self.tokens) / self.rate)


class PoliteHttpClient:
    def __init__(self, rate_per_sec: float = 0.5, burst: int = 2, timeout: float = 30.0,
                 max_retries: int = 4, respect_robots: bool = True, user_agent: str = DEFAULT_UA,
                 headers: dict[str, str] | None = None, transport: httpx.AsyncBaseTransport | None = None):
        self._client = httpx.AsyncClient(
            timeout=timeout, follow_redirects=True, transport=transport,
            headers={"User-Agent": user_agent, "Accept-Language": "ar,en;q=0.8", **(headers or {})},
        )
        self._buckets: dict[str, TokenBucket] = {}
        self._robots: dict[str, Robots | None] = {}
        self.rate, self.burst = rate_per_sec, burst
        self.max_retries = max_retries
        self.respect_robots = respect_robots
        self.user_agent = user_agent
        self.requests_made = 0

    async def _allowed(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            rules: Robots | None = None
            try:
                r = await self._client.get(f"{origin}/robots.txt")
                if r.status_code < 400:
                    rules = Robots(r.text, self.user_agent)
            except httpx.HTTPError:
                rules = None  # unreachable robots.txt -> treated as no restrictions
            self._robots[origin] = rules
        rules = self._robots[origin]
        return rules is None or rules.allowed(url)

    async def request(self, method: str, url: str, **kw: Any) -> httpx.Response:
        if not await self._allowed(url):
            raise RobotsDisallowed(url)
        host = urlsplit(url).netloc
        bucket = self._buckets.setdefault(host, TokenBucket(self.rate, self.burst))
        delay = 1.0
        for attempt in range(self.max_retries + 1):
            await bucket.acquire()
            self.requests_made += 1
            try:
                resp = await self._client.request(method, url, **kw)
            except (httpx.TimeoutException, httpx.TransportError) as e:
                if attempt == self.max_retries:
                    raise
                log.warning("network error %s on %s, retry %d", e, url, attempt + 1)
            else:
                if resp.status_code not in RETRY_STATUS or attempt == self.max_retries:
                    resp.raise_for_status()
                    return resp
                ra = resp.headers.get("Retry-After", "")
                if ra.isdigit():
                    delay = max(delay, float(ra))
                log.warning("HTTP %s on %s, retry %d in %.1fs", resp.status_code, url, attempt + 1, delay)
            await asyncio.sleep(delay + random.uniform(0, delay / 2))
            delay = min(delay * 2, 60)
        raise RuntimeError("unreachable")

    async def get_json(self, url: str, **kw: Any) -> Any:
        return (await self.request("GET", url, **kw)).json()

    async def get_text(self, url: str, **kw: Any) -> str:
        return (await self.request("GET", url, **kw)).text

    async def get_bytes(self, url: str, **kw: Any) -> tuple[bytes, str | None]:
        r = await self.request("GET", url, **kw)
        return r.content, r.headers.get("content-type")

    async def aclose(self) -> None:
        await self._client.aclose()
