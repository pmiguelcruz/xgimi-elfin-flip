"""One projector entity for inferred Wi-Fi power and standby."""

from homeassistant.components.media_player import (
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import ProjectorCoordinator

PARALLEL_UPDATES = 0


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([ElfinMediaPlayer(entry.runtime_data)])


class ElfinMediaPlayer(CoordinatorEntity[ProjectorCoordinator], MediaPlayerEntity):
    _attr_has_entity_name = True
    _attr_name = None
    _attr_device_class = MediaPlayerDeviceClass.TV
    _attr_assumed_state = True
    _attr_supported_features = MediaPlayerEntityFeature.TURN_OFF

    def __init__(self, coordinator: ProjectorCoordinator) -> None:
        super().__init__(coordinator)
        identity = coordinator.identity
        self._attr_unique_id = f"{identity.udn}_projector"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, identity.udn)},
            name=identity.name,
            manufacturer=identity.manufacturer,
            model=identity.model,
        )

    @property
    def state(self) -> MediaPlayerState | None:
        if self.coordinator.data is None:
            return None
        return MediaPlayerState.ON if self.coordinator.data else MediaPlayerState.OFF

    async def async_turn_off(self) -> None:
        await self.coordinator.async_sleep()
