"""An accurately named communication sensor, not a fabricated power sensor."""

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.helpers.entity import EntityCategory

from .entity import ElfinEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([ElfinReachability(entry.runtime_data)])


class ElfinReachability(ElfinEntity, BinarySensorEntity):
    _attr_name = "DIAL reachable"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator):
        super().__init__(coordinator, "dial_reachable")

    @property
    def is_on(self):
        return self.coordinator.data.reachable
