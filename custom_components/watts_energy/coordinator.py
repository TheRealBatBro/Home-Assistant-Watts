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
from .statistics import async_import_consumption_statistics

_LOGGER = logging.getLogger(__name__)

type WattsConfigEntry = ConfigEntry[WattsCoordinator]


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
    """Polls the Watts API."""

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

        # Live data: every update.
        for dev in data.devices.values():
            if not dev.has_live_card:
                continue
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
            await self._async_fetch_slow(data, now)
            self._last_slow_update = now
        else:
            for device_id, dev in data.devices.items():
                if previous and device_id in previous.devices:
                    dev.hourly = previous.devices[device_id].hourly
            data.prices = previous.prices if previous else {}

        return data

    async def _async_fetch_slow(self, data: WattsData, now: datetime) -> None:
        local_now = dt_util.as_local(now)
        month_start = dt_util.start_of_local_day(local_now.replace(day=1))
        # Include the tail of the previous month so "yesterday" works on the 1st.
        hourly_start = min(month_start, dt_util.start_of_local_day(local_now) - timedelta(days=1))

        for dev in data.devices.values():
            records = await self.client.async_get_consumptions(dev.device_id, hourly_start, now)
            dev.hourly = sorted(
                (
                    (t, float(r["v"]))
                    for r in records
                    if (t := parse_time(r.get("t"))) and r.get("v") is not None
                ),
                key=lambda x: x[0],
            )
            try:
                await async_import_consumption_statistics(self.hass, self.client, dev)
            except WattsAuthError:
                raise
            except Exception:  # noqa: BLE001 - statistics must never break the sensors
                _LOGGER.exception("Failed to import statistics for %s", dev.device_id)

        day_start = dt_util.start_of_local_day(local_now)
        for location_id in data.locations:
            records = await self.client.async_get_prices(
                location_id, day_start, day_start + timedelta(days=2)
            )
            data.prices[location_id] = sorted(
                (
                    (t, float(r["p"]))
                    for r in records
                    if (t := parse_time(r.get("t"))) and r.get("p") is not None
                ),
                key=lambda x: x[0],
            )
