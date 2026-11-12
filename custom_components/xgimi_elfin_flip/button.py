"""Standby, optional wake and refresh."""

from homeassistant.components.button import ButtonEntity
from homeassistant.helpers.entity import EntityCategory

from .const import WAKE_NONE
from .entity import ElfinEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = entry.runtime_data
    entities = [
        ElfinButton(coordinator, "sleep", "Standby", "mdi:projector-off"),
        ElfinButton(coordinator, "refresh", "Refresh", "mdi:refresh"),
    ]
    if coordinator.wake_backend != WAKE_NONE:
        entities.append(ElfinButton(coordinator, "wake", "Wake", "mdi:projector"))
    async_add_entities(entities)


class ElfinButton(ElfinEntity, ButtonEntity):
    def __init__(self, coordinator, key, name, icon):
        super().__init__(coordinator, key)
        self.key = key
        self._attr_name = name
        self._attr_icon = icon
        if key == "refresh":
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

    async def async_press(self):
        if self.key == "sleep":
            await self.coordinator.async_sleep()
        elif self.key == "wake":
            await self.coordinator.async_wake()
        elif self.key == "refresh":
            await self.coordinator.async_request_refresh()
