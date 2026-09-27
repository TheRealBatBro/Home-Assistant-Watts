"""Sensors for Watts."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import UnitOfEnergy, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN, HEATING_TYPES, HOUSE_TYPES
from .coordinator import DeviceData, WattsConfigEntry, WattsCoordinator
from .statistics import statistic_id

# Live values are 5-minute intervals; treat anything older than this as stale.
LIVE_STALE_AFTER = timedelta(minutes=30)
INTERVALS_PER_HOUR = 12

PRICE_UNIT = "DKK/kWh"


def _location_name(location: dict[str, Any]) -> str:
    addr = location.get("address") or {}
    street = " ".join(p for p in (addr.get("streetName"), addr.get("houseNumber")) if p)
    city = addr.get("city")
    return ", ".join(p for p in (street, city) if p) or f"Location {location.get('id')}"


def _sum_between(
    values: list[tuple[datetime, float]], start: datetime, end: datetime | None = None
) -> float | None:
    selected = [v for t, v in values if t >= start and (end is None or t < end)]
    return round(sum(selected), 3) if selected else None


def _latest_live(dev: DeviceData) -> tuple[datetime, float, dict[str, Any]] | None:
    if not dev.live:
        return None
    latest = dev.live[-1]
    if dt_util.utcnow() - latest[0] > LIVE_STALE_AFTER + timedelta(minutes=5):
        return None
    return latest


def _power(dev: DeviceData) -> float | None:
    latest = _latest_live(dev)
    # kWh over a 5-minute interval -> average W over that interval.
    return round(latest[1] * INTERVALS_PER_HOUR * 1000, 1) if latest else None


def _power_attrs(dev: DeviceData) -> dict[str, Any]:
    latest = _latest_live(dev)
    if not latest:
        return {}
    raw = latest[2]
    attrs: dict[str, Any] = {"interval_start": latest[0].isoformat(), "interval_kwh": latest[1]}
    for key, name in (
        ("a", "interval_absolute_kwh"),
        ("MPT", "max_power_total"),
        ("MPL1", "max_power_l1"),
        ("MPL2", "max_power_l2"),
        ("MPL3", "max_power_l3"),
    ):
        if raw.get(key) is not None:
            attrs[name] = raw[key]
    return attrs


def _live_today(dev: DeviceData) -> float:
    today = dt_util.start_of_local_day()
    return round(sum(v for t, v, _ in dev.live if t >= today), 3)


def _last_full_day(dev: DeviceData) -> tuple[str, float] | None:
    """Most recent local day with complete hourly data (23-25 hours around DST)."""
    days: dict[datetime, list[float]] = {}
    for t, v in dev.hourly:
        days.setdefault(dt_util.start_of_local_day(dt_util.as_local(t)), []).append(v)
    today = dt_util.start_of_local_day()
    for day in sorted(days, reverse=True):
        hours_in_day = round((dt_util.start_of_local_day(day + timedelta(hours=25)) - day).total_seconds() / 3600)
        if day < today and len(days[day]) >= hours_in_day:
            return day.date().isoformat(), round(sum(days[day]), 3)
    return None


def _last_day_value(dev: DeviceData) -> float | None:
    result = _last_full_day(dev)
    return result[1] if result else None


def _last_day_attrs(dev: DeviceData) -> dict[str, Any]:
    result = _last_full_day(dev)
    return {"date": result[0]} if result else {}


def _this_month(dev: DeviceData) -> float | None:
    month_start = dt_util.start_of_local_day(dt_util.now().replace(day=1))
    return _sum_between(dev.hourly, month_start)


def _latest_hour(dev: DeviceData) -> float | None:
    return dev.hourly[-1][1] if dev.hourly else None


def _latest_hour_attrs(dev: DeviceData) -> dict[str, Any]:
    attrs: dict[str, Any] = {"statistic_id": statistic_id(dev)}
    if dev.hourly:
        attrs["hour_start"] = dev.hourly[-1][0].isoformat()
    return attrs


@dataclass(frozen=True, kw_only=True)
class WattsMeterSensorDescription(SensorEntityDescription):
    value_fn: Callable[[DeviceData], float | None]
    attrs_fn: Callable[[DeviceData], dict[str, Any]] | None = None
    live_only: bool = False


METER_SENSORS: tuple[WattsMeterSensorDescription, ...] = (
    WattsMeterSensorDescription(
        key="power",
        translation_key="power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        value_fn=_power,
        attrs_fn=_power_attrs,
        live_only=True,
    ),
    WattsMeterSensorDescription(
        key="energy_today_live",
        translation_key="energy_today_live",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        value_fn=_live_today,
        live_only=True,
    ),
    WattsMeterSensorDescription(
        key="energy_last_day",
        translation_key="energy_last_day",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        value_fn=_last_day_value,
        attrs_fn=_last_day_attrs,
    ),
    WattsMeterSensorDescription(
        key="energy_this_month",
        translation_key="energy_this_month",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        value_fn=_this_month,
    ),
    WattsMeterSensorDescription(
        key="energy_latest_hour",
        translation_key="energy_latest_hour",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=3,
        value_fn=_latest_hour,
        attrs_fn=_latest_hour_attrs,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WattsConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Watts sensors."""
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = [
        WattsPriceSensor(coordinator, location_id) for location_id in coordinator.data.locations
    ]
    for dev in coordinator.data.devices.values():
        entities.extend(
            WattsMeterSensor(coordinator, dev.device_id, description)
            for description in METER_SENSORS
            if dev.has_live_card or not description.live_only
        )
    async_add_entities(entities)


def _location_device_info(coordinator: WattsCoordinator, location_id: str) -> DeviceInfo:
    location = coordinator.data.locations.get(location_id, {})
    return DeviceInfo(
        identifiers={(DOMAIN, f"location_{location_id}")},
        name=_location_name(location),
        manufacturer="Watts",
        model=HOUSE_TYPES.get(location.get("houseType"), "Location"),
        configuration_url="https://watts.dk/",
    )


class WattsPriceSensor(CoordinatorEntity[WattsCoordinator], SensorEntity):
    """Current electricity price for a location."""

    _attr_has_entity_name = True
    _attr_translation_key = "price"
    _attr_native_unit_of_measurement = PRICE_UNIT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator: WattsCoordinator, location_id: str) -> None:
        super().__init__(coordinator)
        self._location_id = location_id
        self._attr_unique_id = f"{location_id}_price"
        self._attr_device_info = _location_device_info(coordinator, location_id)

    @property
    def _prices(self) -> list[tuple[datetime, float]]:
        return self.coordinator.data.prices.get(self._location_id, [])

    @property
    def available(self) -> bool:
        return super().available and self._location_id in self.coordinator.data.locations

    @property
    def native_value(self) -> float | None:
        now = dt_util.utcnow()
        past = [(t, p) for t, p in self._prices if t <= now]
        # Guard against serving a stale price if the API stops returning today's prices.
        if not past or now - past[-1][0] > timedelta(hours=2):
            return None
        return past[-1][1]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        location = self.coordinator.data.locations.get(self._location_id, {})
        today_start = dt_util.start_of_local_day()
        tomorrow_start = today_start + timedelta(days=1)
        day_after = tomorrow_start + timedelta(days=1)
        now = dt_util.utcnow()

        def rows(start: datetime, end: datetime) -> list[dict[str, Any]]:
            return [
                {"start": dt_util.as_local(t).isoformat(), "price": p}
                for t, p in self._prices
                if start <= t < end
            ]

        today = rows(today_start, tomorrow_start)
        today_prices = [r["price"] for r in today]
        upcoming = [p for t, p in self._prices if t > now]
        attrs: dict[str, Any] = {
            "today": today,
            "tomorrow": rows(tomorrow_start, day_after),
            "tomorrow_available": any(tomorrow_start <= t < day_after for t, _ in self._prices),
            "next_price": upcoming[0] if upcoming else None,
            "house_type": HOUSE_TYPES.get(location.get("houseType")),
            "primary_heating": HEATING_TYPES.get(location.get("primaryHeatingType")),
            "persons": location.get("persons"),
            "square_meters": location.get("squareMeters"),
        }
        if today_prices:
            attrs.update(
                today_min=min(today_prices),
                today_max=max(today_prices),
                today_average=round(sum(today_prices) / len(today_prices), 4),
            )
        return attrs


class WattsMeterSensor(CoordinatorEntity[WattsCoordinator], SensorEntity):
    """A sensor for one electricity meter."""

    _attr_has_entity_name = True
    entity_description: WattsMeterSensorDescription

    def __init__(
        self,
        coordinator: WattsCoordinator,
        device_id: str,
        description: WattsMeterSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._device_id = device_id
        self._attr_unique_id = f"{device_id}_{description.key}"
        dev = coordinator.data.devices[device_id]
        kind = "Production meter" if dev.is_production else "Electricity meter"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            # Several meters can share an address; the address is on the location device.
            name=f"{kind} {device_id[-4:]}",
            manufacturer="Watts",
            model=f"{kind}{' with Watts Live' if dev.has_live_card else ''}",
            serial_number=device_id,
        )

    @property
    def _dev(self) -> DeviceData | None:
        return self.coordinator.data.devices.get(self._device_id)

    @property
    def available(self) -> bool:
        return super().available and self._dev is not None

    @property
    def native_value(self) -> float | None:
        dev = self._dev
        return self.entity_description.value_fn(dev) if dev else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        dev = self._dev
        if dev is None or self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(dev)
