"""Projector power and commands for an optional paired keyboard."""

import asyncio

from homeassistant.components.remote import RemoteEntity
from homeassistant.exceptions import ServiceValidationError

from .entity import ElfinEntity
from .keyboard import KEYBOARD_COMMANDS, keyboard_targets

PARALLEL_UPDATES = 0


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([ElfinRemote(entry.runtime_data)])


class ElfinRemote(ElfinEntity, RemoteEntity):
    _attr_name = "Remote"
    _attr_assumed_state = True

    def __init__(self, coordinator):
        super().__init__(coordinator, "remote")
        self._key_lock = asyncio.Lock()

    @property
    def is_on(self):
        if self.coordinator.data.power == "waking":
            return None
        if self.coordinator.data.power == "standby":
            return False
        return True if self.coordinator.data.reachable else None

    async def async_turn_off(self, **kwargs):
        await self.coordinator.async_sleep()

    async def async_turn_on(self, **kwargs):
        await self.coordinator.async_wake()

    async def async_send_command(self, command, **kwargs):
        commands = [command] if isinstance(command, str) else list(command)
        power_commands = {"sleep", "poweroff", "wake", "poweron"}
        if kwargs.get("num_repeats", 1) != 1 or kwargs.get("hold_secs", 0):
            raise ServiceValidationError("Repeats and held keys are not supported.")
        if any(c not in power_commands and c not in KEYBOARD_COMMANDS for c in commands):
            supported = ", ".join(sorted(power_commands | KEYBOARD_COMMANDS.keys()))
            raise ServiceValidationError(f"Supported commands: {supported}.")

        async with self._key_lock:
            keyboard = [c for c in commands if c in KEYBOARD_COMMANDS]
            targets = keyboard_targets(self.hass, self.coordinator.entry.options, keyboard) if keyboard else {}
            for item in commands:
                if item in targets:
                    await self.hass.services.async_call(
                        "button", "press", target={"entity_id": targets[item]}, blocking=True
                    )
                    # ESPHome sends the release 80 ms after the press.
                    await asyncio.sleep(0.12)
                elif item in ("sleep", "poweroff"):
                    await self.coordinator.async_sleep()
                else:
                    await self.coordinator.async_wake()
