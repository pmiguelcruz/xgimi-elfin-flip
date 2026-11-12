"""UI setup, optional wake configuration and identity-preserving reconfigure."""

from __future__ import annotations

import re
from dataclasses import asdict
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_HOST, CONF_SCAN_INTERVAL
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import EntitySelector, EntitySelectorConfig

from .api import DialClient, ProjectorConnectionError, ProjectorError
from .const import (
    CONF_BLE_ADAPTER,
    CONF_BLE_DATA,
    CONF_BLE_DURATION,
    CONF_IDENTITY,
    CONF_INCREMENT,
    CONF_KEYBOARD,
    CONF_WAKE_BACKEND,
    CONF_WAKE_BUTTON,
    CONF_WAKE_TIMEOUT,
    DEFAULT_INTERVAL,
    DEFAULT_WAKE_TIMEOUT,
    DOMAIN,
    WAKE_BUTTON,
    WAKE_LOCAL,
    WAKE_NONE,
)
from .keyboard import keyboard_device


def _schema(current: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONF_WAKE_BACKEND, default=current.get(CONF_WAKE_BACKEND, WAKE_NONE)
            ): vol.In([WAKE_NONE, WAKE_BUTTON, WAKE_LOCAL]),
            vol.Optional(
                CONF_WAKE_BUTTON, description={"suggested_value": current.get(CONF_WAKE_BUTTON)}
            ): EntitySelector(EntitySelectorConfig(domain=["button", "input_button"])),
            vol.Optional(
                CONF_KEYBOARD,
                description={"suggested_value": current.get(CONF_KEYBOARD)},
            ): EntitySelector(EntitySelectorConfig(domain="binary_sensor", integration="esphome")),
            vol.Optional(
                CONF_BLE_DATA, description={"suggested_value": current.get(CONF_BLE_DATA)}
            ): str,
            vol.Optional(
                CONF_BLE_ADAPTER, description={"suggested_value": current.get(CONF_BLE_ADAPTER)}
            ): str,
            vol.Required(CONF_BLE_DURATION, default=current.get(CONF_BLE_DURATION, 4)): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=10)
            ),
            vol.Required(CONF_INCREMENT, default=current.get(CONF_INCREMENT, False)): bool,
            vol.Required(
                CONF_WAKE_TIMEOUT, default=current.get(CONF_WAKE_TIMEOUT, DEFAULT_WAKE_TIMEOUT)
            ): vol.All(vol.Coerce(int), vol.Range(min=10, max=120)),
            vol.Required(
                CONF_SCAN_INTERVAL, default=current.get(CONF_SCAN_INTERVAL, DEFAULT_INTERVAL)
            ): vol.All(vol.Coerce(int), vol.Range(min=5, max=300)),
        }
    )


async def _validate_options(hass, values: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    options = dict(values)
    errors: dict[str, str] = {}
    if (sensor := options.get(CONF_KEYBOARD)) and keyboard_device(hass, sensor) is None:
        errors[CONF_KEYBOARD] = "invalid_keyboard"
    backend = options.get(CONF_WAKE_BACKEND, WAKE_NONE)
    if backend == WAKE_BUTTON:
        entity_id = options.get(CONF_WAKE_BUTTON, "")
        if (
            entity_id.partition(".")[0] not in ("button", "input_button")
            or hass.states.get(entity_id) is None
        ):
            errors[CONF_WAKE_BUTTON] = "invalid_button"
        else:
            from homeassistant.helpers import entity_registry as er  # noqa: PLC0415

            item = er.async_get(hass).async_get(entity_id)
            if item is not None and item.platform == DOMAIN:
                errors[CONF_WAKE_BUTTON] = "invalid_button"
    elif backend == WAKE_LOCAL:
        value = options.get(CONF_BLE_DATA, "")
        # Accept conventional whitespace between bytes, then store canonical hex.
        token = "".join(value.split()).upper()
        if not re.fullmatch(r"[0-9A-F]{32}", token):
            errors[CONF_BLE_DATA] = "invalid_manufacturer_data"
        else:
            options[CONF_BLE_DATA] = token
        try:
            from .local_ble import async_discover_adapters  # noqa: PLC0415

            adapters = await async_discover_adapters()
            chosen = options.get(CONF_BLE_ADAPTER, "")
            paths = {a.path for a in adapters if a.powered and a.available_instances != 0}
            if chosen.startswith("hci"):
                chosen = "/org/bluez/" + chosen
            if chosen not in paths:
                errors[CONF_BLE_ADAPTER] = "no_advertising_adapter"
            else:
                options[CONF_BLE_ADAPTER] = chosen
        except OSError, RuntimeError, TimeoutError:
            errors[CONF_BLE_ADAPTER] = "no_advertising_adapter"
        except Exception:  # D-Bus/BlueZ absence is a form error, not a setup crash.
            errors[CONF_BLE_ADAPTER] = "no_advertising_adapter"
    elif backend != WAKE_NONE:
        errors[CONF_WAKE_BACKEND] = "invalid_backend"
    return options, errors


class ElfinFlipConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1
    MINOR_VERSION = 2

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> ElfinFlipOptionsFlow:
        return ElfinFlipOptionsFlow()

    async def async_step_user(self, user_input=None) -> ConfigFlowResult:
        errors = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            try:
                client = DialClient(async_get_clientsession(self.hass), host)
                identity = await client.async_get_device_info()
                await self.async_set_unique_id(identity.udn)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=identity.name,
                    data={CONF_HOST: host, CONF_IDENTITY: asdict(identity)},
                    options={CONF_WAKE_BACKEND: WAKE_NONE, CONF_SCAN_INTERVAL: DEFAULT_INTERVAL},
                )
            except ProjectorConnectionError:
                errors["base"] = "cannot_connect"
            except ProjectorError, ValueError:
                errors["base"] = "invalid_device"
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST): str,
                }
            ),
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
            except ProjectorError, ValueError:
                errors["base"] = "invalid_device"
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST, default=entry.data[CONF_HOST]): str,
                }
            ),
            errors=errors,
        )


class ElfinFlipOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input=None) -> ConfigFlowResult:
        current = dict(self.config_entry.options)
        errors = {}
        if user_input is not None:
            options, errors = await _validate_options(self.hass, user_input)
            if not errors:
                return self.async_create_entry(title="", data=options)
            current.update(user_input)
        return self.async_show_form(step_id="init", data_schema=_schema(current), errors=errors)
