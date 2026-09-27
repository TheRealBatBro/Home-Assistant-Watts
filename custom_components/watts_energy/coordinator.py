"""Data update coordinator for Watts."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import WattsApiClient, WattsAuthError, WattsError
from .const import (
    DEVICE_TYPE_ELECTRICITY,
    DOMAIN,
    SLOW_UPDATE_INTERVAL,
    UPDATE_INTERVAL,
)
from .statistics import async_get_import_state, async_import_hourly

_LOGGER = logging.getLogger(__name__)

type WattsConfigEntry = ConfigEntry[WattsCoordinator]

# Hourly meter data typically lags 2-3 days, so keep a week for the "last full day" sensor.
HOURLY_SENSOR_DAYS = 7
# Refresh prices at least this often even when nothing new is expected.
PRICE_MAX_AGE = timedelta(hours=6)
# Next-day prices are published around 13:00 Danish time.
TOMORROW_PRICES_HOUR = 13


def parse_time(value: str | None) -> datetime | None:
    """Parse an API timestamp. Timestamps without an offset are treated as UTC."""
    if not value:
        return None
    parsed = dt_util.parse_datetime(value)
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def parse_series(records: list[dict[str, Any]], key: str) -> list[tuple[datetime, float]]:
    """Turn API records into a time-sorted list of (start, value)."""
    return sorted(
        (
            (t, float(r[key]))
            for r in records
            if (t := parse_time(r.get("t"))) and r.get(key) is not None
        ),
        key=lambda x: x[0],
    )


@dataclass
class DeviceData:
    """State for one electricity meter."""

    info: dict[str, Any]
    location_id: str
    # (start, value kWh, raw record) sorted by start
    live: list[tuple[datetime, float, dict[str, Any]]] = field(default_factory=list)
    hourly: list[tuple[datetime, float]] = field(default_factory=list)

    @property
    def device_id(self) -> str:
        return self.info["id"]

    @property
    def is_production(self) -> bool:
        return bool(self.info.get("isProduction"))

    @property
    def has_live_card(self) -> bool:
        return bool(self.info.get("isLiveCardInstalled"))


@dataclass
class WattsData:
    """Everything the entities need."""

    locations: dict[str, dict[str, Any]] = field(default_factory=dict)
    devices: dict[str, DeviceData] = field(default_factory=dict)
    # location id -> [(start, price)] sorted by start
    prices: dict[str, list[tuple[datetime, float]]] = field(default_factory=dict)


def _is_active(device: dict[str, Any], now: datetime) -> bool:
    valid_to = parse_time(device.get("validTo"))
    # .NET serialises "no end date" as year 1 or 9999; only skip real past end dates.
    return valid_to is None or valid_to.year <= 1 or valid_to > now


class WattsCoordinator(DataUpdateCoordinator[WattsData]):
    """Polls the Watts API.

    The API allows 10 requests a minute, so only live data is polled every
    update; locations and hourly consumption refresh hourly, prices only when
    new ones can be expected.
    """

    config_entry: WattsConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: WattsConfigEntry, client: WattsApiClient
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
        )
        self.client = client
        self._last_slow_update: datetime | None = None
        self._last_price_update: datetime | None = None

    async def _async_update_data(self) -> WattsData:
        try:
            return await self._async_fetch()
        except WattsAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except WattsError as err:
            raise UpdateFailed(str(err)) from err

    async def _async_fetch(self) -> WattsData:
        now = dt_util.utcnow()
        previous = self.data
        slow_due = (
            previous is None
            or self._last_slow_update is None
            or now - self._last_slow_update >= SLOW_UPDATE_INTERVAL
        )

        if slow_due:
            data = await self._async_fetch_locations(now)
        else:
            assert previous is not None
            data = WattsData(
                locations=previous.locations,
                devices={
                    device_id: DeviceData(dev.info, dev.location_id, hourly=dev.hourly)
                    for device_id, dev in previous.devices.items()
                },
                prices=previous.prices,
            )

        for dev in data.devices.values():
            if dev.has_live_card:
                records = await self.client.async_get_live_data(
                    dev.device_id, now - timedelta(hours=24), now
                )
                dev.live = sorted(
                    (
                        (t, float(r["v"]), r)
                        for r in records
                        if (t := parse_time(r.get("t"))) and r.get("v") is not None
                    ),
                    key=lambda x: x[0],
                )

        if slow_due:
            await self._async_fetch_hourly(data, now)
            data.prices = previous.prices if previous else {}
            if self._prices_due(data, now):
                await self._async_fetch_prices(data, now)
            self._last_slow_update = now

        return data

    async def _async_fetch_locations(self, now: datetime) -> WattsData:
        data = WattsData()
        for location in await self.client.async_get_locations():
            location_id = location.get("id")
            if not location_id:
                continue
            data.locations[location_id] = location
            for device in location.get("devices") or []:
                if (
                    device.get("id")
                    and device.get("type") == DEVICE_TYPE_ELECTRICITY
                    and _is_active(device, now)
                ):
                    data.devices[device["id"]] = DeviceData(device, location_id)
        return data

    async def _async_fetch_hourly(self, data: WattsData, now: datetime) -> None:
        """One request per meter feeds both the sensors and long-term statistics."""
        local_now = dt_util.as_local(now)
        sensor_start = min(
            dt_util.start_of_local_day(local_now.replace(day=1)),
            dt_util.start_of_local_day(local_now) - timedelta(days=HOURLY_SENSOR_DAYS),
        )
        for dev in data.devices.values():
            import_state = await async_get_import_state(self.hass, dev)
            fetch_from = min(sensor_start, import_state.fetch_from)
            records = await self.client.async_get_consumptions(dev.device_id, fetch_from, now)
            series = parse_series(records, "v")
            dev.hourly = [(t, v) for t, v in series if t >= sensor_start]
            try:
                async_import_hourly(self.hass, dev, series, import_state)
            except Exception:  # noqa: BLE001 - statistics must never break the sensors
                _LOGGER.exception("Failed to import statistics for %s", dev.device_id)

    def _prices_due(self, data: WattsData, now: datetime) -> bool:
        if self._last_price_update is None or now - self._last_price_update >= PRICE_MAX_AGE:
            return True
        local_now = dt_util.as_local(now)
        today = dt_util.start_of_local_day(local_now)
        tomorrow = today + timedelta(days=1)
        for location_id in data.locations:
            prices = data.prices.get(location_id, [])
            if not any(today <= t < tomorrow for t, _ in prices):
                return True
            if local_now.hour >= TOMORROW_PRICES_HOUR and not any(t >= tomorrow for t, _ in prices):
                return True
        return False

    async def _async_fetch_prices(self, data: WattsData, now: datetime) -> None:
        day_start = dt_util.start_of_local_day(dt_util.as_local(now))
        for location_id in data.locations:
            records = await self.client.async_get_prices(
                location_id, day_start, day_start + timedelta(days=2)
            )
            data.prices[location_id] = parse_series(records, "p")
        self._last_price_update = now
