"""HTTP client wrapper with cookie management, rate limiting, and retry logic."""

from __future__ import annotations

import asyncio
import logging
import random
from pathlib import Path
from typing import Any

import httpx

from .config import Config

log = logging.getLogger(__name__)

# Rotate User-Agent strings to reduce fingerprinting
_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
]


class EHClient:
    """Async HTTP client for e-hentai / exhentai with built-in protections."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self._rate_limit_lock = asyncio.Lock()
        self._last_request_time: float = 0.0
        self._client: httpx.AsyncClient | None = None

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            cookies = httpx.Cookies(self.config.get_cookies())
            self._client = httpx.AsyncClient(
                cookies=cookies,
                headers={
                    "User-Agent": random.choice(_USER_AGENTS),
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.5",
                },
                follow_redirects=True,
                timeout=httpx.Timeout(60.0, connect=15.0),
                http2=True,
            )
        return self._client

    async def _rate_limit(self) -> None:
        """Enforce minimum delay between requests."""
        async with self._rate_limit_lock:
            now = asyncio.get_event_loop().time()
            elapsed = now - self._last_request_time
            delay = self.config.rate_limit_delay
            if elapsed < delay:
                jitter = random.uniform(0, 0.5)
                await asyncio.sleep(delay - elapsed + jitter)
            self._last_request_time = asyncio.get_event_loop().time()

    async def get(self, url: str, *, skip_rate_limit: bool = False) -> httpx.Response:
        """GET request with rate limiting and retry."""
        if not skip_rate_limit:
            await self._rate_limit()

        client = await self._ensure_client()

        for attempt in range(self.config.retry_count):
            try:
                response = await client.get(url)

                if response.status_code == 429:
                    wait = self.config.retry_delay * (2 ** attempt)
                    log.warning("Rate limited (429), waiting %.1fs...", wait)
                    await asyncio.sleep(wait)
                    continue

                if response.status_code == 503:
                    log.warning("Server busy (503), waiting %.1fs...", self.config.retry_delay)
                    await asyncio.sleep(self.config.retry_delay)
                    continue

                response.raise_for_status()
                return response

            except httpx.TimeoutException as e:
                log.warning("Timeout on GET %s (attempt %d/%d): %s", url[:80], attempt + 1, self.config.retry_count, e)
                if attempt == self.config.retry_count - 1:
                    raise
                await asyncio.sleep(self.config.retry_delay)
            except httpx.HTTPStatusError as e:
                log.warning("HTTP error on GET %s (attempt %d/%d): %s", url[:80], attempt + 1, self.config.retry_count, e)
                if attempt == self.config.retry_count - 1:
                    raise
                await asyncio.sleep(self.config.retry_delay)

        raise RuntimeError(f"Failed to fetch {url} after {self.config.retry_count} attempts")

    async def post_json(self, url: str, data: dict[str, Any]) -> dict[str, Any]:
        """POST JSON request (for e-hentai API)."""
        await self._rate_limit()
        client = await self._ensure_client()

        for attempt in range(self.config.retry_count):
            try:
                response = await client.post(url, json=data)
                response.raise_for_status()
                return response.json()
            except (httpx.TimeoutException, httpx.HTTPStatusError) as e:
                log.warning("POST %s attempt %d failed: %s", url, attempt + 1, e)
                if attempt == self.config.retry_count - 1:
                    raise
                await asyncio.sleep(self.config.retry_delay)

        raise RuntimeError(f"Failed to POST {url} after {self.config.retry_count} attempts")

    async def download_file(
        self,
        url: str,
        dest: str,
        *,
        referer: str | None = None,
        max_attempts: int | None = None,
        quiet: bool = False,
    ) -> int:
        """Download a file to disk. Returns bytes written.

        Args:
            url: Direct URL to the file.
            dest: Local path to save the file.
            referer: Optional Referer header (some CDN servers require this).
            max_attempts: Override the configured retry count for this transfer.
            quiet: Log transfer failures at debug level so callers can summarize them.
        """
        client = await self._ensure_client()

        headers: dict[str, str] = {}
        if referer:
            headers["Referer"] = referer

        attempts = max(1, max_attempts if max_attempts is not None else self.config.retry_count)

        for attempt in range(attempts):
            try:
                async with client.stream("GET", url, headers=headers) as response:
                    response.raise_for_status()
                    total = 0
                    # Ensure parent directory exists
                    Path(dest).parent.mkdir(parents=True, exist_ok=True)
                    with open(dest, "wb") as f:
                        async for chunk in response.aiter_bytes(chunk_size=65536):
                            f.write(chunk)
                            total += len(chunk)
                    return total

            except (httpx.TimeoutException, httpx.HTTPStatusError, httpx.StreamError) as e:
                status_code = _http_status_code(e)
                retryable = status_code is None or status_code in {408, 409, 425, 429, 500, 502, 503, 504}
                final_attempt = attempt == attempts - 1 or not retryable

                log_fn = log.debug if quiet or not final_attempt else log.warning
                log_fn(
                    "Download %s attempt %d/%d failed: %s",
                    url[:80], attempt + 1, attempts, e,
                )
                # Remove partial file
                p = Path(dest)
                if p.exists():
                    p.unlink(missing_ok=True)

                if final_attempt:
                    raise
                await asyncio.sleep(self.config.retry_delay)

        raise RuntimeError(f"Failed to download {url}")

    def can_access_exhentai(self) -> bool:
        """Check if ExHentai cookies are configured."""
        return self.config.has_exhentai_cookies

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()


def _http_status_code(exc: BaseException) -> int | None:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code
    return None
