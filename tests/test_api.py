"""Tests for the Watts API client token handling."""

from unittest.mock import AsyncMock, patch

import pytest

from custom_components.watts_energy import api
from custom_components.watts_energy.api import (
    WattsApiClient,
    WattsAuthError,
    WattsConnectionError,
)

TOKEN_OK = '{"access_token": "tok", "expires_in": 3600}'
HTML = "<html><body>The service is unavailable.</body></html>"


@pytest.fixture(autouse=True)
def no_delay():
    with patch.object(api, "TOKEN_RETRY_DELAY", 0):
        yield


def _client():
    return WattsApiClient(None, "id", "secret")


async def test_token_retries_after_non_json():
    client = _client()
    mock = AsyncMock(side_effect=[(503, HTML), (200, ""), (200, TOKEN_OK)])
    with patch.object(WattsApiClient, "_async_request_token", mock):
        assert await client._async_get_token() == "tok"
    # Alternates between form body and query-string style.
    assert [c.kwargs["use_query"] for c in mock.call_args_list] == [False, True, False]


async def test_token_is_cached():
    client = _client()
    mock = AsyncMock(return_value=(200, TOKEN_OK))
    with patch.object(WattsApiClient, "_async_request_token", mock):
        await client._async_get_token()
        await client._async_get_token()
    assert mock.call_count == 1


async def test_token_invalid_client_is_auth_error():
    client = _client()
    body = '{"error":"invalid_client","error_description":"AADB2C90018: bad id"}'
    with patch.object(WattsApiClient, "_async_request_token", AsyncMock(return_value=(400, body))):
        with pytest.raises(WattsAuthError, match="AADB2C90018"):
            await client._async_get_token()


async def test_token_persistent_garbage_reports_status_and_body():
    client = _client()
    with patch.object(WattsApiClient, "_async_request_token", AsyncMock(return_value=(429, HTML))):
        with pytest.raises(WattsConnectionError, match=r"HTTP 429, non-JSON response: <html>.*unavailable"):
            await client._async_get_token()
