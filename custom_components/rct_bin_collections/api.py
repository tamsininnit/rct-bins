"""Polite asynchronous HTTP client used by providers.

Responsibilities:
- HTTPS only, TLS verification left to the shared aiohttp session (enabled by default).
- Bounded connect/read timeouts and a maximum response size.
- No automatic redirects (the council site has been seen redirecting to plain HTTP).
- A small number of retries with exponential backoff for transient failures.
- Honouring ``Retry-After`` on HTTP 429.

It knows nothing about HTML or waste collections; parsing lives in the providers.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
import logging
import random

import aiohttp
from yarl import URL

from .const import VERSION
from .exceptions import (
    PropertyNotFoundError,
    RateLimitedError,
    ServiceUnavailableError,
    UnsupportedResponseError,
)

_LOGGER = logging.getLogger(__name__)

USER_AGENT = f"HomeAssistant-RCTBinCollections/{VERSION} (unofficial community integration)"

DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=45, connect=10, sock_read=30)
MAX_RESPONSE_BYTES = 3 * 1024 * 1024
MAX_ATTEMPTS = 3
BACKOFF_BASE_SECONDS = 2.0
#: Retry-After values up to this are waited out inside one request; longer ones are raised.
MAX_INLINE_RETRY_AFTER = 30.0
_CHUNK_SIZE = 64 * 1024


class HttpClient:
    """Fetch text documents from a fixed HTTPS origin."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        timeout: aiohttp.ClientTimeout = DEFAULT_TIMEOUT,
        max_attempts: int = MAX_ATTEMPTS,
    ) -> None:
        """Initialise with a shared aiohttp session."""
        self._session = session
        self._timeout = timeout
        self._max_attempts = max_attempts
        self.last_status: int | None = None

    async def get_text(self, url: URL, params: Mapping[str, str]) -> str:
        """GET ``url`` with ``params`` and return the decoded body.

        Raises:
            ServiceUnavailableError: on network errors, timeouts or HTTP 5xx after retries.
            RateLimitedError: on HTTP 429 with a long or missing ``Retry-After``.
            PropertyNotFoundError: on HTTP 404.
            UnsupportedResponseError: on redirects, other 4xx, wrong content type,
                empty or oversized bodies.

        """
        if url.scheme != "https":
            raise UnsupportedResponseError("refusing non-HTTPS URL")

        attempt = 0
        while True:
            attempt += 1
            try:
                return await self._get_once(url, params)
            except RateLimitedError as err:
                wait = err.retry_after
                if attempt >= self._max_attempts or wait is None or wait > MAX_INLINE_RETRY_AFTER:
                    raise
                _LOGGER.debug("Rate limited; waiting %.0f s before retrying", wait)
                await asyncio.sleep(wait)
            except ServiceUnavailableError as err:
                if attempt >= self._max_attempts:
                    raise
                delay = BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
                delay += random.uniform(0, delay / 2)  # noqa: S311 - jitter, not crypto
                _LOGGER.debug(
                    "Transient error (%s); retry %d/%d in %.1f s",
                    err,
                    attempt,
                    self._max_attempts - 1,
                    delay,
                )
                await asyncio.sleep(delay)

    async def _get_once(self, url: URL, params: Mapping[str, str]) -> str:
        """Perform a single request without retries."""
        try:
            async with self._session.get(
                url,
                params=params,
                allow_redirects=False,
                timeout=self._timeout,
                headers={"User-Agent": USER_AGENT, "Accept": "text/html"},
            ) as resp:
                self.last_status = resp.status
                self._check_status(resp)
                content_type = resp.headers.get("Content-Type", "").split(";")[0].strip().lower()
                if content_type not in ("text/html", "application/xhtml+xml"):
                    raise UnsupportedResponseError(f"unexpected content type {content_type!r}")
                body = await self._read_limited(resp)
                charset = resp.charset or "utf-8"
        # "from None" drops the original exception: aiohttp messages can contain the request
        # URL (and so the UPRN), and HA prints full tracebacks at debug level.
        except TimeoutError:
            raise ServiceUnavailableError("request timed out") from None
        except aiohttp.ClientError as err:
            raise ServiceUnavailableError(f"connection error ({type(err).__name__})") from None

        try:
            text = body.decode(charset, errors="replace")
        except LookupError:
            text = body.decode("utf-8", errors="replace")
        if not text.strip():
            raise UnsupportedResponseError("empty response body")
        return text

    @staticmethod
    def _check_status(resp: aiohttp.ClientResponse) -> None:
        """Map HTTP status codes onto integration exceptions."""
        status = resp.status
        if 200 <= status < 300:
            return
        if 300 <= status < 400:
            raise UnsupportedResponseError(f"unexpected redirect (HTTP {status})")
        if status == 429:
            raise RateLimitedError(
                "rate limited (HTTP 429)",
                retry_after=parse_retry_after(resp.headers.get("Retry-After")),
            )
        if status == 404:
            raise PropertyNotFoundError("not found (HTTP 404)")
        if status >= 500:
            raise ServiceUnavailableError(f"server error (HTTP {status})")
        raise UnsupportedResponseError(f"unexpected HTTP status {status}")

    @staticmethod
    async def _read_limited(resp: aiohttp.ClientResponse) -> bytes:
        """Read the body, refusing anything larger than ``MAX_RESPONSE_BYTES``."""
        declared = resp.headers.get("Content-Length", "")
        if declared.isdigit() and int(declared) > MAX_RESPONSE_BYTES:
            raise UnsupportedResponseError("response too large")
        chunks: list[bytes] = []
        total = 0
        async for chunk in resp.content.iter_chunked(_CHUNK_SIZE):
            total += len(chunk)
            if total > MAX_RESPONSE_BYTES:
                raise UnsupportedResponseError("response too large")
            chunks.append(chunk)
        return b"".join(chunks)


def parse_retry_after(value: str | None) -> float | None:
    """Parse a ``Retry-After`` header (seconds or HTTP date) into seconds."""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        when = parsedate_to_datetime(value)
    except TypeError, ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - datetime.now(UTC)).total_seconds())
