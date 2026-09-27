"""End-to-end tests for Watts."""

from unittest.mock import AsyncMock, patch

from homeassistant import config_entries
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.util import dt as dt_util
from datetime import timedelta
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.watts_energy.api import WattsAuthError
from custom_components.watts_energy.const import DOMAIN

DATA = {"client_id": "abc", "client_secret": "secret"}


async def test_config_flow(hass, mock_api):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    with patch("custom_components.watts_energy.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], DATA)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == DATA


async def test_config_flow_invalid_auth(hass):
    with patch("custom_components.watts_energy.api.WattsApiClient.async_get_locations",
               AsyncMock(side_effect=WattsAuthError("nope"))):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
        result = await hass.config_entries.flow.async_configure(result["flow_id"], DATA)
    assert result["errors"] == {"base": "invalid_auth"}


async def test_sensors_and_statistics(hass, mock_api):
    entry = MockConfigEntry(domain=DOMAIN, data=DATA, unique_id="abc")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    states = {s.entity_id: s for s in hass.states.async_all("sensor")}
    for eid, s in sorted(states.items()):
        print(eid, s.state, s.attributes.get("unit_of_measurement"))
    # Only the active electricity meter gets meter sensors (5) + 1 price sensor.
    assert len(states) == 6

    power = next(s for e, s in states.items() if e.endswith("_power"))
    assert float(power.state) == 1200.0  # 0.1 kWh / 5 min
    assert power.attributes["max_power_total"] == 1.5

    price = next(s for e, s in states.items() if "price" in e or "elpris" in e)
    assert price.state not in ("unknown", "unavailable")
    assert len(price.attributes["today"]) >= 23
    assert price.attributes["tomorrow_available"] is True

    last_day = next(s for e, s in states.items() if "last_full_day" in e)
    assert float(last_day.state) == 24.0  # 1 kWh every hour of a complete day
    assert last_day.attributes["date"] < dt_util.now().date().isoformat()

    await async_wait_recording_done(hass)
    stat_id = "watts_energy:571313100000000001_consumption"
    stats = await get_instance(hass).async_add_executor_job(
        statistics_during_period, hass, dt_util.utcnow() - timedelta(days=400), None,
        {stat_id}, "hour", None, {"sum", "state"},
    )
    rows = stats[stat_id]
    print("statistics rows:", len(rows), "last sum:", rows[-1]["sum"])
    assert len(rows) > 24 * 360
    assert rows[-1]["sum"] == len(rows)

    # Second import must only append, not duplicate.
    coordinator = entry.runtime_data
    coordinator._last_slow_update = None
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    await async_wait_recording_done(hass)
    stats2 = await get_instance(hass).async_add_executor_job(
        statistics_during_period, hass, dt_util.utcnow() - timedelta(days=400), None,
        {stat_id}, "hour", None, {"sum"},
    )
    assert stats2[stat_id][-1]["sum"] == rows[-1]["sum"]

    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_auth_failure_starts_reauth(hass, mock_api):
    entry = MockConfigEntry(domain=DOMAIN, data=DATA, unique_id="abc")
    entry.add_to_hass(hass)
    mock_api.side_effect = WattsAuthError("expired")
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress()
    assert any(f["context"]["source"] == "reauth" for f in flows)


async def test_meter_linked_to_location(hass, mock_api):
    from homeassistant.helpers import device_registry as dr

    entry = MockConfigEntry(domain=DOMAIN, data=DATA, unique_id="abc")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    reg = dr.async_get(hass)
    devices = {i: d for d in dr.async_entries_for_config_entry(reg, entry.entry_id) for _, i in d.identifiers}
    meter, location = devices["571313100000000001"], devices["location_loc1"]
    assert meter.via_device_id == location.id
