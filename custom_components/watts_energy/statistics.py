"""Import hourly meter data into Home Assistant long-term statistics.

Hourly consumption from the grid (DataHub) usually arrives a day or more late,
so a regular sensor can't place it at the right time. External statistics can,
which makes them usable in the Energy dashboard.
"""

from __future__ import annotations

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
    from .api import WattsApiClient
    from .coordinator import DeviceData

_LOGGER = logging.getLogger(__name__)

# Keep each request to a manageable size during backfill.
FETCH_CHUNK = timedelta(days=31)


def statistic_id(dev: DeviceData) -> str:
    kind = "production" if dev.is_production else "consumption"
    return f"{DOMAIN}:{slugify(dev.device_id)}_{kind}"


def _metadata(dev: DeviceData) -> StatisticMetaData:
    kind = "production" if dev.is_production else "consumption"
    meta: dict = {
        "source": DOMAIN,
        "statistic_id": statistic_id(dev),
        "name": f"Watts {dev.device_id} {kind}",
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


async def async_import_consumption_statistics(
    hass: HomeAssistant, client: WattsApiClient, dev: DeviceData
) -> None:
    """Append any new hourly values for a device to long-term statistics."""
    from .coordinator import parse_time

    stat_id = statistic_id(dev)
    last = await get_instance(hass).async_add_executor_job(
        get_last_statistics, hass, 1, stat_id, True, {"sum"}
    )

    now = dt_util.utcnow()
    if last and last.get(stat_id):
        last_start = _as_datetime(last[stat_id][0]["start"])
        running_sum = float(last[stat_id][0].get("sum") or 0.0)
        fetch_from = last_start + timedelta(hours=1)
    else:
        last_start = None
        running_sum = 0.0
        fetch_from = dt_util.start_of_local_day(now - timedelta(days=STATISTICS_BACKFILL_DAYS))

    if fetch_from >= now:
        return

    # Sum per hour in case the API ever returns sub-hour intervals.
    hours: dict[datetime, float] = {}
    chunk_start = fetch_from
    while chunk_start < now:
        chunk_end = min(chunk_start + FETCH_CHUNK, now)
        for record in await client.async_get_consumptions(dev.device_id, chunk_start, chunk_end):
            start = parse_time(record.get("t"))
            if start is None or record.get("v") is None:
                continue
            start = start.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
            if last_start is not None and start <= last_start:
                continue
            hours[start] = hours.get(start, 0.0) + float(record["v"])
        chunk_start = chunk_end

    if not hours:
        return

    stats: list[StatisticData] = []
    for start in sorted(hours):
        running_sum += hours[start]
        stats.append(StatisticData(start=start, state=hours[start], sum=running_sum))

    _LOGGER.debug("Importing %d hourly statistics into %s", len(stats), stat_id)
    async_add_external_statistics(hass, _metadata(dev), stats)
