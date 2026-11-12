"""Standby and optional wake for the projector."""

from homeassistant.components.media_player import (
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)

from .const import WAKE_NONE
from .entity import ElfinEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([ElfinMediaPlayer(entry.runtime_data)])


class ElfinMediaPlayer(ElfinEntity, MediaPlayerEntity):
    _attr_name = None
    _attr_device_class = MediaPlayerDeviceClass.TV
    _attr_assumed_state = True

    def __init__(self, coordinator):
        super().__init__(coordinator, "projector")

    @property
    def supported_features(self):
        features = MediaPlayerEntityFeature.TURN_OFF
        if self.coordinator.wake_backend != WAKE_NONE:
            features |= MediaPlayerEntityFeature.TURN_ON
        return features

    @property
    def state(self):
        data = self.coordinator.data
        if data.power == "waking":
            return None
        if data.power == "standby":
            return MediaPlayerState.OFF
        if data.reachable:
            # Network presence does not prove display power or playback.
            return MediaPlayerState.ON
        return None

    @property
    def extra_state_attributes(self):
        data = self.coordinator.data
        return {
            "power_observation": data.power,
            "power_state_is_inferred": True,
            "display_power_observable": False,
            "last_command": data.last_command,
            "last_command_result": data.command_result,
            "available_controls": ["standby"]
            + (["wake"] if self.coordinator.wake_backend != WAKE_NONE else []),
            "unexposed_controls": [
                "volume",
                "mute",
                "hdmi_input",
                "navigation",
                "focus",
                "keystone",
                "picture",
                "temperature",
                "full_shutdown",
            ],
        }

    async def async_turn_off(self):
        await self.coordinator.async_sleep()

    async def async_turn_on(self):
        await self.coordinator.async_wake()
