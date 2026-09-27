"""Async client for the Watts API (https://developer.watts-energy.dk)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
import time
from typing import Any

import aiohttp

from .const import API_BASE_URL, API_VERSION, TOKEN_SCOPE, TOKEN_URL

_LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)
# Refresh the token this many seconds before it actually expires.
TOKEN_EXPIRY_MARGIN = 120


class WattsError(Exception):
    """Base error for the Watts API."""


class WattsAuthError(WattsError):
    """Credentials were rejected."""


class WattsConnectionError(WattsError):
    """The API could not be reached or returned an unexpected response."""


def _iso(value: datetime) -> str:
    """Format a datetime for the API as UTC ISO-8601."""
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class WattsApiClient:
    """Minimal Watts API client with cached client-credentials token."""

    def __init__(
        self, session: aiohttp.ClientSession, client_id: str, client_secret: str
    ) -> None:
        self._session = session
        self._client_id = client_id
        self._client_secret = client_secret
        self._token: str | None = None
        self._token_expires = 0.0
        self._token_lock = asyncio.Lock()

    async def _async_get_token(self) -> str:
        async with self._token_lock:
            if self._token and time.monotonic() < self._token_expires:
                return self._token

            data = {
                "grant_type": "client_credentials",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "scope": TOKEN_SCOPE,
            }
            try:
                async with self._session.post(
                    TOKEN_URL, data=data, timeout=REQUEST_TIMEOUT
                ) as resp:
                    body = await resp.json(content_type=None)
                    if resp.status in (400, 401, 403):
                        raise WattsAuthError(
                            body.get("error_description") or body.get("error") or resp.status
                        )
                    resp.raise_for_status()
            except WattsAuthError:
                raise
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as err:
                raise WattsConnectionError(f"Token request failed: {err}") from err

            token = body.get("access_token")
            if not token:
                raise WattsAuthError("No access_token in token response")
            expires_in = int(body.get("expires_in", 3600))
            self._token = token
            self._token_expires = time.monotonic() + max(expires_in - TOKEN_EXPIRY_MARGIN, 60)
            return token

    async def _async_get(self, path: str, params: dict[str, str] | None = None) -> Any:
        query = {"v": API_VERSION, **(params or {})}
        for attempt in range(2):
            token = await self._async_get_token()
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
                    if resp.status == 401 and attempt == 0:
                        # Token revoked or expired early: fetch a new one and retry once.
                        self._token = None
                        continue
                    if resp.status in (401, 403):
                        raise WattsAuthError(f"Access denied ({resp.status})")
                    resp.raise_for_status()
                    return await resp.json(content_type=None)
            except WattsAuthError:
                raise
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as err:
                raise WattsConnectionError(f"GET {path} failed: {err}") from err
        raise WattsAuthError("Access denied")

    async def async_get_locations(self) -> list[dict[str, Any]]:
        """Return all locations (with their devices) for the account."""
        return await self._async_get("locations") or []

    async def async_get_consumptions(
        self, device_id: str, start: datetime, end: datetime | None = None
    ) -> list[dict[str, Any]]:
        """Return hourly consumption values for a device."""
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
        """Return hourly electricity prices for a location."""
        return (
            await self._async_get(
                f"locations/{location_id}/electricity-prices",
                {"from": _iso(start), "to": _iso(end)},
            )
            or []
        )
