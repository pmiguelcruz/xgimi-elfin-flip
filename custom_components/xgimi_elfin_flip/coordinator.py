"""Keep command acceptance separate from observable device/network state."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_SCAN_INTERVAL, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .api import (
    DeviceInfo,
    DialClient,
    ProjectorCommandError,
    ProjectorConnectionError,
    ProjectorError,
    ProjectorProtocolError,
)
from .const import (
    CONF_BLE_ADAPTER,
    CONF_BLE_DATA,
    CONF_BLE_DURATION,
    CONF_INCREMENT,
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

LOGGER = logging.getLogger(__name__)
SLEEP_CONFIRMATION_TIMEOUT = timedelta(seconds=30)


@dataclass(frozen=True)
class ProjectorData:
    """No network response means unreachable, not an observed power-off."""

    reachable: bool = False
    power: str = "unknown"
    last_seen: datetime | None = None
    last_command: str | None = None
    command_result: str | None = None
    command_time: datetime | None = None
    error: str | None = None


class ProjectorCoordinator(DataUpdateCoordinator[ProjectorData]):
    """Poll projector identity and verify the requested network transition."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, client: DialClient, identity: DeviceInfo
    ) -> None:
        super().__init__(
            hass,
            LOGGER,
            name=DOMAIN,
            config_entry=entry,
            update_interval=timedelta(
                seconds=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_INTERVAL)
            ),
            always_update=False,
        )
        self.entry = entry
        self.client = client
        self.identity = identity
        self._control_lock = asyncio.Lock()
        self._local_wake: Any = None
        self._control_task: asyncio.Task | None = None
        self._closed = False
        self._power_hint: str | None = None
        self._last_seen: datetime | None = None
        self._last_command: str | None = None
        self._command_result: str | None = None
        self._command_time: datetime | None = None

    @property
    def wake_backend(self) -> str:
        return self.entry.options.get(CONF_WAKE_BACKEND, WAKE_NONE)

    def _snapshot(self, reachable: bool, *, error: str | None = None) -> ProjectorData:
        if self._power_hint == "waking":
            # Some firmware keeps DIAL alive in display standby. Preserve the
            # requested transition even when a concurrent poll reaches DIAL.
            power = "waking"
        elif reachable:
            power = "sleep_requested" if self._power_hint == "sleep_requested" else "reachable"
        elif self._power_hint in ("sleep_requested", "standby"):
            power = "standby"
        else:
            power = "unknown"
        return ProjectorData(
            reachable=reachable,
            power=power,
            last_seen=self._last_seen,
            last_command=self._last_command,
            command_result=self._command_result,
            command_time=self._command_time,
            error=error,
        )

    def _expire_pending_sleep(self) -> None:
        """A later unrelated outage must not confirm an old sleep request."""
        if (
            self._power_hint == "sleep_requested"
            and self._command_time is not None
            and dt_util.utcnow() - self._command_time >= SLEEP_CONFIRMATION_TIMEOUT
        ):
            self._power_hint = None
            self._command_result = "sleep_accepted_standby_unconfirmed"

    async def _async_update_data(self) -> ProjectorData:
        self._expire_pending_sleep()
        try:
            identity = await self.client.async_get_device_info()
            if identity.udn != self.identity.udn:
                self._power_hint = None
                return self._snapshot(False, error="identity_changed")
        except ProjectorConnectionError:
            # Also check after the request: its deadline can cross the end of
            # the confirmation window even if the poll started inside it.
            self._expire_pending_sleep()
            if self._power_hint == "sleep_requested":
                self._power_hint = "standby"
                self._command_result = "sleep_accepted_network_unreachable"
            return self._snapshot(False, error="unreachable")
        except ProjectorError:
            self._power_hint = None
            return self._snapshot(False, error="invalid_device_response")
        self._last_seen = dt_util.utcnow()
        if self._power_hint == "standby":
            self._power_hint = None
        self._expire_pending_sleep()
        return self._snapshot(True)

    def _record_command(self, command: str, result: str) -> None:
        self._last_command = command
        self._command_result = result
        self._command_time = dt_util.utcnow()

    async def _check_identity(self) -> None:
        info = await self.client.async_get_device_info()
        if info.udn != self.identity.udn:
            raise ProjectorProtocolError("The address now belongs to a different projector.")

    def _publish_failed_command(
        self, command: str, error: ProjectorError, *, identity_verified: bool = False
    ) -> None:
        """Publish failure without preserving a stale standby or wake hint."""
        self._power_hint = None
        self._record_command(command, "failed")
        # Only an explicit command rejection following a matching identity is
        # evidence that our projector is still reachable. An invalid identity,
        # malformed response, or connection loss must remain unknown.
        reachable = identity_verified and isinstance(error, ProjectorCommandError)
        self.async_set_updated_data(self._snapshot(reachable, error="command_failed"))

    def _publish_cancelled_command(self, command: str) -> None:
        """Cancellation cannot establish the physical result of a request."""
        self._power_hint = None
        self._record_command(command, "cancelled")
        self.async_set_updated_data(self._snapshot(False, error="command_cancelled"))

    async def async_sleep(self) -> None:
        """Request standby. An offline initial state is deliberately not success."""
        async with self._control_lock:
            self._control_task = asyncio.current_task()
            identity_verified = False
            try:
                if self._closed:
                    raise HomeAssistantError("The integration is unloading.")
                await self._check_identity()
                identity_verified = True
                await self.client.async_sleep()
                self._record_command("sleep", "accepted")
                self._power_hint = "sleep_requested"
                self.async_set_updated_data(self._snapshot(True))
                await self.async_request_refresh()
            except ProjectorError as err:
                self._publish_failed_command("sleep", err, identity_verified=identity_verified)
                raise HomeAssistantError(f"Projector did not accept sleep: {err}") from err
            except asyncio.CancelledError:
                self._publish_cancelled_command("sleep")
                raise
            finally:
                self._control_task = None

    async def _transmit_wake(self) -> None:
        options = self.entry.options
        if self.wake_backend == WAKE_BUTTON:
            entity_id = options.get(CONF_WAKE_BUTTON, "")
            domain = entity_id.partition(".")[0]
            state = self.hass.states.get(entity_id)
            registered = er.async_get(self.hass).async_get(entity_id)
            if (
                domain not in ("button", "input_button")
                or state is None
                or state.state == STATE_UNAVAILABLE
                or (registered is not None and registered.platform == DOMAIN)
                or not self.hass.services.has_service(domain, "press")
            ):
                raise HomeAssistantError("The selected wake button is unavailable or invalid.")
            try:
                async with asyncio.timeout(15):
                    await self.hass.services.async_call(
                        domain,
                        "press",
                        {"entity_id": entity_id},
                        blocking=True,
                    )
            except TimeoutError as err:
                raise HomeAssistantError(
                    "The selected wake button did not finish within 15 seconds."
                ) from err
        elif self.wake_backend == WAKE_LOCAL:
            from .local_ble import LocalBlueZWake, WakeError  # noqa: PLC0415

            if self._local_wake is None:
                self._local_wake = LocalBlueZWake(
                    options[CONF_BLE_DATA],
                    options[CONF_BLE_ADAPTER],
                    options.get(CONF_BLE_DURATION, 4),
                    options.get(CONF_INCREMENT, False),
                )
            try:
                await self._local_wake.async_wake()
            except (WakeError, OSError) as err:
                raise HomeAssistantError(f"Bluetooth wake failed: {err}") from err
        else:
            raise ServiceValidationError("Enable a wake backend in integration options first.")

    async def async_wake(self) -> None:
        """Transmit explicit wake even if the standby device still serves DIAL."""
        if self.wake_backend == WAKE_NONE:
            raise ServiceValidationError("No wake backend is configured.")
        async with self._control_lock:
            self._control_task = asyncio.current_task()
            try:
                if self._closed:
                    raise HomeAssistantError("The integration is unloading.")
                initially_reachable = False
                # Reachability is an identity check, not display-power state.
                # A matching DIAL server can still be present during standby.
                try:
                    await self._check_identity()
                except ProjectorConnectionError:
                    pass
                except ProjectorError as err:
                    raise HomeAssistantError(str(err)) from err
                else:
                    initially_reachable = True
                self._power_hint = "waking"
                self._record_command("wake", "transmitting")
                self.async_set_updated_data(self._snapshot(initially_reachable))
                await self._transmit_wake()
                self._record_command("wake", "transmitted_waiting_for_device")
                timeout = self.entry.options.get(CONF_WAKE_TIMEOUT, DEFAULT_WAKE_TIMEOUT)
                async with asyncio.timeout(timeout):
                    while not self._closed:
                        try:
                            await self._check_identity()
                            break
                        except ProjectorConnectionError:
                            await asyncio.sleep(2)
                    else:
                        raise HomeAssistantError("The integration is unloading.")
                self._power_hint = None
                result = (
                    "wake_transmitted_dial_already_reachable"
                    if initially_reachable
                    else "confirmed_dial_reachable"
                )
                self._record_command("wake", result)
                await self.async_request_refresh()
            except TimeoutError as err:
                self._power_hint = None
                self._record_command("wake", "timed_out")
                self.async_set_updated_data(self._snapshot(False, error="wake_timeout"))
                raise HomeAssistantError(
                    "Wake was transmitted, but the projector did not return on the network."
                ) from err
            except asyncio.CancelledError:
                self._publish_cancelled_command("wake")
                raise
            except (HomeAssistantError, ProjectorError) as err:
                self._power_hint = None
                self._record_command("wake", "failed")
                self.async_set_updated_data(self._snapshot(False, error="wake_failed"))
                if isinstance(err, HomeAssistantError):
                    raise
                raise HomeAssistantError(str(err)) from err
            finally:
                self._control_task = None

    async def async_close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self.async_shutdown()
        task = self._control_task
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if self._local_wake is not None:
            await self._local_wake.async_close()
