"""Async client for the Watts API (https://developer.watts-energy.dk)."""

from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime, timezone
import json
import logging
import re
import time
from typing import Any

import aiohttp

from .const import API_BASE_URL, API_VERSION, TOKEN_SCOPE, TOKEN_URL

_LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=60)
# Refresh the token this many seconds before it actually expires.
TOKEN_EXPIRY_MARGIN = 120
TOKEN_ATTEMPTS = 3
TOKEN_RETRY_DELAY = 2

# The API allows 10 requests per 60 seconds per client and answers 429 with
# Retry-After beyond that. Stay just under it.
RATE_LIMIT_CALLS = 9
RATE_LIMIT_PERIOD = 61.0
MAX_RETRY_AFTER = 90
RATE_LIMIT_RETRIES = 2


class WattsError(Exception):
    """Base error for the Watts API."""


class WattsAuthError(WattsError):
    """Credentials were rejected."""


class WattsConnectionError(WattsError):
    """The API could not be reached or returned an unexpected response."""


def _parse_json(text: str) -> Any:
    """Return parsed JSON, or None if the text isn't a JSON document."""
    try:
        return json.loads(text) if text.strip() else None
    except ValueError:
        return None


def _snippet(text: str, limit: int = 200) -> str:
    text = " ".join(text.split())
    return (text[:limit] + "...") if len(text) > limit else (text or "<empty body>")


_B2C_ERROR = re.compile(r"(AADB2C\d+)[:\s]*([^\"<\\\r\n]{0,160})")
_TITLE = re.compile(r"<title>\s*([^<]{0,120}?)\s*</title>", re.IGNORECASE)


def _describe_html(text: str) -> str:
    """Summarise an HTML/empty response: B2C error code if present, else title or snippet."""
    if not text.strip():
        return "empty body"
    if match := _B2C_ERROR.search(text):
        return f"{match.group(1)}: {match.group(2).strip()}"
    if match := _TITLE.search(text):
        return f"HTML page '{match.group(1)}'"
    return f"non-JSON response: {_snippet(text, 120)}"


def _iso(value: datetime) -> str:
    """Format a datetime for the API as UTC ISO-8601."""
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class _RateLimiter:
    """Sliding-window limiter: at most `calls` acquisitions per `period` seconds."""

    def __init__(self, calls: int, period: float) -> None:
        self._calls = calls
        self._period = period
        self._times: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                while self._times and now - self._times[0] >= self._period:
                    self._times.popleft()
                if len(self._times) < self._calls:
                    self._times.append(now)
                    return
                wait = self._period - (now - self._times[0])
                _LOGGER.debug("Watts rate limit reached, waiting %.1fs", wait)
                await asyncio.sleep(wait)

    def penalise(self) -> None:
        """Treat the window as full after a 429 we didn't see coming."""
        now = time.monotonic()
        self._times = deque([now] * self._calls)


# The limit is per API client, so share one limiter between the config flow and the entry.
_LIMITERS: dict[str, _RateLimiter] = {}


class WattsApiClient:
    """Minimal Watts API client with cached client-credentials token.

    The session passed in must not keep cookies: once the token endpoint has set its
    B2C session cookie, later token requests carrying it get the interactive sign-in
    page (HTTP 200, HTML) instead of a token.
    """

    def __init__(
        self, session: aiohttp.ClientSession, client_id: str, client_secret: str
    ) -> None:
        self._session = session
        self._client_id = client_id
        self._client_secret = client_secret
        self._token: str | None = None
        self._token_expires = 0.0
        self._token_lock = asyncio.Lock()
        self._limiter = _LIMITERS.setdefault(
            client_id, _RateLimiter(RATE_LIMIT_CALLS, RATE_LIMIT_PERIOD)
        )

    async def _async_request_token(self) -> tuple[int, str]:
        """POST to the token endpoint and return (status, body text)."""
        async with self._session.post(
            TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "scope": TOKEN_SCOPE,
            },
            headers={"Accept": "application/json"},
            timeout=REQUEST_TIMEOUT,
            # A redirect would only lead to the HTML sign-in page; report it instead.
            allow_redirects=False,
        ) as resp:
            text = await resp.text()
            if location := resp.headers.get("Location"):
                text = text or f"redirect to {location}"
            return resp.status, text

    async def _async_get_token(self) -> str:
        async with self._token_lock:
            if self._token and time.monotonic() < self._token_expires:
                return self._token

            failures: list[str] = []
            for attempt in range(TOKEN_ATTEMPTS):
                if attempt:
                    await asyncio.sleep(TOKEN_RETRY_DELAY * attempt)
                try:
                    status, text = await self._async_request_token()
                except (aiohttp.ClientError, asyncio.TimeoutError) as err:
                    failures.append(f"{type(err).__name__} {err}")
                    continue

                body = _parse_json(text)
                if body is None:
                    # Throttling, gateway errors and B2C's own error pages are HTML.
                    _LOGGER.debug("Token endpoint returned non-JSON (HTTP %s): %s", status, text)
                    failures.append(f"HTTP {status} {_describe_html(text)}")
                    continue
                if status in (400, 401, 403):
                    raise WattsAuthError(
                        body.get("error_description") or body.get("error") or f"HTTP {status}"
                    )
                if status >= 300:
                    failures.append(f"HTTP {status} {_snippet(text)}")
                    continue

                token = body.get("access_token")
                if not token:
                    raise WattsAuthError("No access_token in token response")
                expires_in = int(body.get("expires_in", 3600))
                self._token = token
                self._token_expires = time.monotonic() + max(expires_in - TOKEN_EXPIRY_MARGIN, 60)
                return token

            # Distinct failures only, so the message stays short enough to read in the UI.
            raise WattsConnectionError(
                "Token request failed: " + " | ".join(dict.fromkeys(failures))
            )

    async def _async_get(self, path: str, params: dict[str, str] | None = None) -> Any:
        query = {"v": API_VERSION, **(params or {})}
        auth_retried = False
        rate_retries = 0
        while True:
            token = await self._async_get_token()
            await self._limiter.acquire()
            try:
                async with self._session.get(
                    f"{API_BASE_URL}/{path}",
                    params=query,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/json",
                    },
                    timeout=REQUEST_TIMEOUT,
                ) as resp:
                    text = await resp.text()
                    status = resp.status
                    retry_after = resp.headers.get("Retry-After")
            except (aiohttp.ClientError, asyncio.TimeoutError) as err:
                raise WattsConnectionError(f"GET {path} failed: {err}") from err

            if status == 401 and not auth_retried:
                # Token revoked or expired early: fetch a new one and retry once.
                auth_retried = True
                self._token = None
                continue
            if status in (401, 403):
                raise WattsAuthError(f"Access denied ({status})")
            if status == 429 and rate_retries < RATE_LIMIT_RETRIES:
                rate_retries += 1
                self._limiter.penalise()
                try:
                    wait = min(float(retry_after or 60), MAX_RETRY_AFTER)
                except ValueError:
                    wait = 60
                _LOGGER.debug("Watts API rate limited on %s, retrying in %ss", path, wait)
                await asyncio.sleep(wait + 1)
                continue
            if status >= 300:
                raise WattsConnectionError(f"GET {path} failed: HTTP {status}: {_snippet(text)}")
            if not text.strip():
                return None
            body = _parse_json(text)
            if body is None:
                raise WattsConnectionError(
                    f"GET {path} returned non-JSON (HTTP {status}): {_snippet(text)}"
                )
            return body

    async def async_get_locations(self) -> list[dict[str, Any]]:
        """Return all locations (with their devices) for the account."""
        return await self._async_get("locations") or []

    async def async_get_consumptions(
        self, device_id: str, start: datetime, end: datetime | None = None
    ) -> list[dict[str, Any]]:
        """Return hourly consumption values for a device (a year+ fits in one call)."""
        params = {"from": _iso(start)}
        if end:
            params["to"] = _iso(end)
        return await self._async_get(f"devices/{device_id}/electricity-consumptions", params) or []

    async def async_get_live_data(
        self, device_id: str, start: datetime, end: datetime | None = None
    ) -> list[dict[str, Any]]:
        """Return 5-minute live values (last 24 hours only) for a device."""
        params = {"from": _iso(start)}
        if end:
            params["to"] = _iso(end)
        return await self._async_get(f"devices/{device_id}/electricity-livedata", params) or []

    async def async_get_prices(
        self, location_id: str, start: datetime, end: datetime
    ) -> list[dict[str, Any]]:
        """Return hourly electricity prices for a location (whole local days)."""
        return (
            await self._async_get(
                f"locations/{location_id}/electricity-prices",
                {"from": _iso(start), "to": _iso(end)},
            )
            or []
        )
