"""Fixtures for Watts tests."""

from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.util import dt as dt_util

pytest_plugins = ["pytest_homeassistant_custom_component"]


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(recorder_mock, enable_custom_integrations):
    yield


LOCATIONS = [
    {
        "id": "loc1",
        "squareMeters": 120,
        "persons": 3,
        "address": {"streetName": "Vestergade", "houseNumber": "1", "city": "Aarhus", "postalCode": 8000},
        "houseType": 2,
        "primaryHeatingType": 3,
        "secondaryHeatingType": 0,
        "devices": [
            {"id": "571313100000000001", "type": 4, "validFrom": "2020-01-01T00:00:00Z",
             "validTo": "9999-12-31T23:59:59", "isProduction": False, "isLiveCardInstalled": True},
            {"id": "water1", "type": 1, "isProduction": False, "isLiveCardInstalled": False},
            {"id": "old", "type": 4, "validTo": "2021-01-01T00:00:00Z", "isProduction": False},
        ],
    }
]


def _iso(t):
    return t.astimezone(dt_util.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def make_live(start, end):
    now = dt_util.utcnow().replace(second=0, microsecond=0)
    now -= timedelta(minutes=now.minute % 5)
    out, t = [], now - timedelta(hours=24)
    while t <= now:
        out.append({"t": _iso(t), "a": 0.1, "v": 0.1, "MPT": 1.5, "MPL1": 0.5, "MPL2": 0.5, "MPL3": 0.5})
        t += timedelta(minutes=5)
    return out


def make_hourly(start, end):
    # Data lags: nothing newer than 30 hours ago.
    stop = min(end, dt_util.utcnow() - timedelta(hours=30))
    t = start.astimezone(dt_util.UTC).replace(minute=0, second=0, microsecond=0)
    out = []
    while t < stop:
        out.append({"t": _iso(t), "i": 0, "v": 1.0})
        t += timedelta(hours=1)
    return out


def make_prices(start, end):
    t = start.astimezone(dt_util.UTC).replace(minute=0, second=0, microsecond=0)
    out = []
    while t < end:
        out.append({"t": _iso(t), "p": 2.0 + t.hour / 10})
        t += timedelta(hours=1)
    return out


@pytest.fixture
def mock_api():
    with (
        patch("custom_components.watts_energy.api.WattsApiClient.async_get_locations",
              AsyncMock(return_value=LOCATIONS)) as loc,
        patch("custom_components.watts_energy.api.WattsApiClient.async_get_live_data",
              AsyncMock(side_effect=lambda d, s, e=None: make_live(s, e))),
        patch("custom_components.watts_energy.api.WattsApiClient.async_get_consumptions",
              AsyncMock(side_effect=lambda d, s, e=None: make_hourly(s, e or dt_util.utcnow()))),
        patch("custom_components.watts_energy.api.WattsApiClient.async_get_prices",
              AsyncMock(side_effect=lambda l, s, e: make_prices(s, e))),
    ):
        yield loc
