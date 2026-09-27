"""Import hourly meter data into Home Assistant long-term statistics.

Hourly consumption from the grid (DataHub) usually arrives 2-3 days late,
so a regular sensor can't place it at the right time. External statistics can,
which makes them usable in the Energy dashboard.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
from typing import TYPE_CHECKING

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import StatisticData, StatisticMetaData
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
)
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util, slugify

from .const import DOMAIN, STATISTICS_BACKFILL_DAYS

if TYPE_CHECKING:
    from .coordinator import DeviceData

_LOGGER = logging.getLogger(__name__)


@dataclass
class ImportState:
    """Where the statistic currently ends."""

    fetch_from: datetime
    last_start: datetime | None
    last_sum: float


def statistic_id(dev: DeviceData) -> str:
    kind = "production" if dev.is_production else "consumption"
    return f"{DOMAIN}:{slugify(dev.device_id)}_{kind}"


def _metadata(dev: DeviceData) -> StatisticMetaData:
    kind = "production" if dev.is_production else "consumption"
    meta: dict = {
        "source": DOMAIN,
        "statistic_id": statistic_id(dev),
        "name": f"Watts {kind} {dev.device_id[-4:]}",
        "unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR,
        "has_sum": True,
        "has_mean": False,
    }
    # Newer Home Assistant versions replaced has_mean with mean_type and added unit_class.
    try:
        from homeassistant.components.recorder.models import StatisticMeanType

        meta["mean_type"] = StatisticMeanType.NONE
        del meta["has_mean"]
    except ImportError:
        pass
    try:
        from homeassistant.util.unit_conversion import EnergyConverter

        meta["unit_class"] = EnergyConverter.UNIT_CLASS
    except (ImportError, AttributeError):
        pass
    return meta  # type: ignore[return-value]


def _as_datetime(value: float | datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromtimestamp(value, tz=timezone.utc)


async def async_get_import_state(hass: HomeAssistant, dev: DeviceData) -> ImportState:
    """Find the last imported hour, or where to start a backfill."""
    stat_id = statistic_id(dev)
    last = await get_instance(hass).async_add_executor_job(
        get_last_statistics, hass, 1, stat_id, True, {"sum"}
    )
    if last and last.get(stat_id):
        row = last[stat_id][0]
        last_start = _as_datetime(row["start"])
        return ImportState(last_start + timedelta(hours=1), last_start, float(row.get("sum") or 0.0))
    return ImportState(
        dt_util.start_of_local_day(dt_util.utcnow() - timedelta(days=STATISTICS_BACKFILL_DAYS)),
        None,
        0.0,
    )


def async_import_hourly(
    hass: HomeAssistant,
    dev: DeviceData,
    series: list[tuple[datetime, float]],
    state: ImportState,
) -> None:
    """Append hours newer than the last imported one to long-term statistics."""
    # Sum per hour in case the API ever returns sub-hour intervals.
    hours: dict[datetime, float] = {}
    for start, value in series:
        start = start.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
        if state.last_start is not None and start <= state.last_start:
            continue
        hours[start] = hours.get(start, 0.0) + value
    if not hours:
        return

    running_sum = state.last_sum
    stats: list[StatisticData] = []
    for start in sorted(hours):
        running_sum += hours[start]
        stats.append(StatisticData(start=start, state=hours[start], sum=running_sum))

    _LOGGER.debug("Importing %d hourly statistics into %s", len(stats), statistic_id(dev))
    async_add_external_statistics(hass, _metadata(dev), stats)
