"""Identify the projector and keep its identity when changing its address."""

from dataclasses import asdict

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import DialClient, ProjectorConnectionError, ProjectorError
from .const import CONF_IDENTITY, DOMAIN


class ElfinFlipConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1
    MINOR_VERSION = 3

    async def async_step_user(self, user_input=None) -> ConfigFlowResult:
        errors = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            try:
                identity = await DialClient(
                    async_get_clientsession(self.hass), host
                ).async_get_device_info()
                await self.async_set_unique_id(identity.udn)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=identity.name,
                    data={CONF_HOST: host, CONF_IDENTITY: asdict(identity)},
                )
            except ProjectorConnectionError:
                errors["base"] = "cannot_connect"
            except (ProjectorError, ValueError):
                errors["base"] = "invalid_device"
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_HOST): str}),
            errors=errors,
        )

    async def async_step_reconfigure(self, user_input=None) -> ConfigFlowResult:
        entry = self._get_reconfigure_entry()
        errors = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            try:
                identity = await DialClient(
                    async_get_clientsession(self.hass), host
                ).async_get_device_info()
                if identity.udn != entry.unique_id:
                    errors["base"] = "different_device"
                else:
                    return self.async_update_reload_and_abort(
                        entry,
                        data_updates={CONF_HOST: host, CONF_IDENTITY: asdict(identity)},
                    )
            except ProjectorConnectionError:
                errors["base"] = "cannot_connect"
            except (ProjectorError, ValueError):
                errors["base"] = "invalid_device"
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema({vol.Required(CONF_HOST, default=entry.data[CONF_HOST]): str}),
            errors=errors,
        )
