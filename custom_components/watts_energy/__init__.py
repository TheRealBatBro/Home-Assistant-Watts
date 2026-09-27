"""The Watts integration."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
import aiohttp

from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import WattsApiClient
from .const import CONF_CLIENT_ID, CONF_CLIENT_SECRET, DOMAIN
from .coordinator import WattsConfigEntry, WattsCoordinator

PLATFORMS: list[Platform] = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: WattsConfigEntry) -> bool:
    """Set up Watts from a config entry."""
    # Own session without cookies: B2C session cookies from an earlier token call can
    # make the token endpoint answer with its interactive sign-in page.
    client = WattsApiClient(
        async_create_clientsession(hass, cookie_jar=aiohttp.DummyCookieJar()),
        entry.data[CONF_CLIENT_ID],
        entry.data[CONF_CLIENT_SECRET],
    )
    coordinator = WattsCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _async_link_meters_to_locations(hass, entry)
    return True


def _async_link_meters_to_locations(hass: HomeAssistant, entry: WattsConfigEntry) -> None:
    """Show each meter as connected via its location device."""
    registry = dr.async_get(hass)
    by_identifier = {
        identifier: device
        for device in dr.async_entries_for_config_entry(registry, entry.entry_id)
        for (domain, identifier) in device.identifiers
        if domain == DOMAIN
    }
    for dev in entry.runtime_data.data.devices.values():
        meter = by_identifier.get(dev.device_id)
        location = by_identifier.get(f"location_{dev.location_id}")
        if meter and location and meter.via_device_id != location.id:
            registry.async_update_device(meter.id, via_device_id=location.id)


async def async_unload_entry(hass: HomeAssistant, entry: WattsConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
