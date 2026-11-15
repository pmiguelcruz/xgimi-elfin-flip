"""Poll the projector over Wi-Fi and coordinate wake and standby."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from time import monotonic

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceNotFound
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .api import (
    DeviceInfo,
    DialClient,
    ProjectorConnectionError,
    ProjectorError,
    ProjectorProtocolError,
)
from .const import DEFAULT_INTERVAL, DOMAIN, WAKE_ACTION

_LOGGER = logging.getLogger(__name__)
WAKE_DISCOVERY_SECONDS = 18


class ProjectorCoordinator(DataUpdateCoordinator[bool | None]):
    """Infer power from the reachability of the matching DIAL device."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, client: DialClient, identity: DeviceInfo
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            config_entry=entry,
            update_interval=timedelta(
                seconds=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_INTERVAL)
            ),
            always_update=False,
        )
        self.client = client
        self.identity = identity
        self._request_lock = asyncio.Lock()
        self._control_lock = asyncio.Lock()
        self._missed_polls = 0
        self._control_task: asyncio.Task | None = None
        self._closed = False

    async def _check_identity(self) -> None:
        info = await self.client.async_get_device_info()
        if info.udn != self.identity.udn:
            raise ProjectorProtocolError("The address now belongs to a different projector.")

    async def _async_update_data(self) -> bool | None:
        async with self._request_lock:
            try:
                await self._check_identity()
            except ProjectorConnectionError:
                self._missed_polls += 1
                # A brief Wi-Fi interruption should not flip an observed on state.
                return self.data is True and self._missed_polls == 1
            except ProjectorError:
                self._missed_polls = 0
                return None
            self._missed_polls = 0
            return True

    async def async_set_power(self, on: bool) -> None:
        """Send a power command and refresh the inferred state from DIAL."""
        async with self._control_lock:
            if self._closed:
                raise HomeAssistantError("The integration is unloading.")
            self._control_task = asyncio.current_task()
            try:
                if on:
                    await self._async_wake()
                else:
                    async with self._request_lock:
                        await self._check_identity()
                        await self.client.async_sleep()
            except ServiceNotFound as err:
                raise HomeAssistantError(
                    "The S3 Box wake action is unavailable. Check its ESPHome connection."
                ) from err
            except ProjectorError as err:
                raise HomeAssistantError(f"Could not put the projector in standby: {err}") from err
            finally:
                self._control_task = None
        if not on and not self._closed:
            await self.async_request_refresh()

    async def _async_wake(self) -> None:
        # DIAL may still reply in standby. Always honor an explicit wake request.
        for _ in range(2):
            await self.hass.services.async_call(
                "esphome", WAKE_ACTION, {"projector_udn": self.identity.udn}, blocking=True
            )
            deadline = monotonic() + WAKE_DISCOVERY_SECONDS
            while True:
                await self.async_refresh()
                if self.data is True and self._missed_polls == 0:
                    return
                remaining = deadline - monotonic()
                if remaining <= 0:
                    break
                await asyncio.sleep(min(1, remaining))
        raise HomeAssistantError(
            "The projector did not return on Wi-Fi after two wake attempts through the S3 Box."
        )

    async def async_close(self) -> None:
        self._closed = True
        task = self._control_task
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await self.async_shutdown()
