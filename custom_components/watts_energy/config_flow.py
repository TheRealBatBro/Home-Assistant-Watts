"""Config flow for Watts."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import WattsApiClient, WattsAuthError, WattsConnectionError
from .const import CONF_CLIENT_ID, CONF_CLIENT_SECRET, DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_CLIENT_ID): str,
        vol.Required(CONF_CLIENT_SECRET): str,
    }
)
DOCS_URL = "https://developer.watts-energy.dk/keys"


class WattsConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Watts."""

    VERSION = 1

    async def _async_validate(self, user_input: dict[str, Any]) -> tuple[dict[str, str], int]:
        """Return (errors, location count)."""
        session = async_create_clientsession(
            self.hass, auto_cleanup=False, cookie_jar=aiohttp.DummyCookieJar()
        )
        client = WattsApiClient(
            session,
            user_input[CONF_CLIENT_ID].strip(),
            user_input[CONF_CLIENT_SECRET].strip(),
        )
        try:
            locations = await client.async_get_locations()
        except WattsAuthError:
            return {"base": "invalid_auth"}, 0
        except WattsConnectionError:
            return {"base": "cannot_connect"}, 0
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Unexpected error validating Watts credentials")
            return {"base": "unknown"}, 0
        finally:
            session.detach()
        if not locations:
            return {"base": "no_locations"}, 0
        return {}, len(locations)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input = {k: v.strip() for k, v in user_input.items()}
            await self.async_set_unique_id(user_input[CONF_CLIENT_ID])
            self._abort_if_unique_id_configured()
            errors, _ = await self._async_validate(user_input)
            if not errors:
                return self.async_create_entry(title="Watts Energy", data=user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(STEP_USER_SCHEMA, user_input),
            errors=errors,
            description_placeholders={"keys_url": DOCS_URL},
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a new client secret (secrets expire)."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {
                CONF_CLIENT_ID: entry.data[CONF_CLIENT_ID],
                CONF_CLIENT_SECRET: user_input[CONF_CLIENT_SECRET].strip(),
            }
            errors, _ = await self._async_validate(data)
            if not errors:
                return self.async_update_reload_and_abort(entry, data=data)

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_CLIENT_SECRET): str}),
            errors=errors,
            description_placeholders={"keys_url": DOCS_URL},
        )
