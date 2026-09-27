"""Tests for the Watts API client: token handling and rate limiting."""

from unittest.mock import AsyncMock, patch

import pytest

from custom_components.watts_energy import api
from custom_components.watts_energy.api import (
    WattsApiClient,
    WattsAuthError,
    WattsConnectionError,
)

TOKEN_OK = '{"access_token": "tok", "expires_in": 3600}'
B2C_PAGE = "<!DOCTYPE html>\r\n<!-- Build: 1.1.795.0 -->\r\n<html><head><title>Sign in</title></head></html>"


@pytest.fixture(autouse=True)
def no_delays():
    """Fake clock: sleeping advances time instantly."""
    api._LIMITERS.clear()
    clock = [1000.0]

    async def fake_sleep(seconds):
        clock[0] += seconds

    with (
        patch.object(api, "TOKEN_RETRY_DELAY", 0),
        patch.object(api.time, "monotonic", lambda: clock[0]),
        patch.object(api.asyncio, "sleep", AsyncMock(side_effect=fake_sleep)) as sleep,
    ):
        yield sleep


class FakeResponse:
    def __init__(self, status, text, headers=None):
        self.status, self._text, self.headers = status, text, headers or {}

    async def text(self):
        return self._text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class FakeSession:
    """Returns queued responses for GET and POST."""

    def __init__(self, gets=(), posts=()):
        self.gets, self.posts = list(gets), list(posts)
        self.post_kwargs = []

    def get(self, url, **kwargs):
        return self.gets.pop(0)

    def post(self, url, **kwargs):
        self.post_kwargs.append(kwargs)
        return self.posts.pop(0)


def _client(session=None, client_id="id"):
    return WattsApiClient(session, client_id, "secret")


async def test_token_request_disables_redirects():
    session = FakeSession(posts=[FakeResponse(200, TOKEN_OK)])
    assert await _client(session)._async_get_token() == "tok"
    assert session.post_kwargs[0]["allow_redirects"] is False
    assert session.post_kwargs[0]["data"]["grant_type"] == "client_credentials"


async def test_token_retries_after_non_json():
    session = FakeSession(posts=[FakeResponse(503, "<html>busy</html>"), FakeResponse(200, TOKEN_OK)])
    assert await _client(session)._async_get_token() == "tok"


async def test_token_is_cached():
    client = _client()
    mock = AsyncMock(return_value=(200, TOKEN_OK))
    with patch.object(WattsApiClient, "_async_request_token", mock):
        await client._async_get_token()
        await client._async_get_token()
    assert mock.call_count == 1


async def test_token_invalid_client_is_auth_error():
    body = '{"error":"invalid_client","error_description":"AADB2C90018: bad id"}'
    session = FakeSession(posts=[FakeResponse(400, body)])
    with pytest.raises(WattsAuthError, match="AADB2C90018"):
        await _client(session)._async_get_token()


async def test_token_sign_in_page_is_reported_readably():
    session = FakeSession(posts=[FakeResponse(200, B2C_PAGE)] * api.TOKEN_ATTEMPTS)
    with pytest.raises(WattsConnectionError, match=r"^Token request failed: HTTP 200 HTML page 'Sign in'$"):
        await _client(session)._async_get_token()


async def test_429_waits_retry_after_then_succeeds(no_delays):
    session = FakeSession(
        posts=[FakeResponse(200, TOKEN_OK)],
        gets=[
            FakeResponse(429, '{"message": "Rate limit is exceeded."}', {"Retry-After": "42"}),
            FakeResponse(200, '[{"id": "loc1"}]'),
        ],
    )
    assert await _client(session).async_get_locations() == [{"id": "loc1"}]
    assert any(call.args == (43.0,) for call in no_delays.call_args_list)


async def test_persistent_429_raises(no_delays):
    rate_limited = FakeResponse(429, "{}", {"Retry-After": "5"})
    session = FakeSession(posts=[FakeResponse(200, TOKEN_OK)], gets=[rate_limited] * 5)
    with pytest.raises(WattsConnectionError, match="HTTP 429"):
        await _client(session).async_get_locations()


async def test_rate_limiter_blocks_call_over_limit(no_delays):
    limiter = api._RateLimiter(3, 60)
    for _ in range(3):
        await limiter.acquire()
    no_delays.assert_not_called()
    await limiter.acquire()
    # Fourth call had to wait for the first one to leave the window.
    assert no_delays.call_args.args[0] == pytest.approx(60)


async def test_empty_body_returns_empty_list():
    session = FakeSession(posts=[FakeResponse(200, TOKEN_OK)], gets=[FakeResponse(200, "")])
    assert await _client(session).async_get_locations() == []
