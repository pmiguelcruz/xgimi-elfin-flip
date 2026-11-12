"""Poll the projector over Wi-Fi and send its standby command."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .api import (
    DeviceInfo,
    DialClient,
    ProjectorConnectionError,
    ProjectorError,
    ProjectorProtocolError,
)
from .const import DEFAULT_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)


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

    async def async_sleep(self) -> None:
        async with self._request_lock:
            if self._closed:
                raise HomeAssistantError("The integration is unloading.")
            self._control_task = asyncio.current_task()
            try:
                await self._check_identity()
                await self.client.async_sleep()
            except ProjectorError as err:
                raise HomeAssistantError(f"Could not put the projector in standby: {err}") from err
            finally:
                self._control_task = None
        if not self._closed:
            await self.async_request_refresh()

    async def async_close(self) -> None:
        self._closed = True
        task = self._control_task
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await self.async_shutdown()
