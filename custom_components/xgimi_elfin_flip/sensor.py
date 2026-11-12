"""Projector power observations, timestamps and command results."""

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.helpers.entity import EntityCategory

from .entity import ElfinEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = entry.runtime_data
    async_add_entities(
        [
            ElfinSensor(coordinator, "power", "Power observation"),
            ElfinSensor(coordinator, "last_seen", "Last DIAL response"),
            ElfinSensor(coordinator, "command_time", "Last command time"),
            ElfinSensor(coordinator, "command_result", "Last command result"),
        ]
    )


class ElfinSensor(ElfinEntity, SensorEntity):
    def __init__(self, coordinator, key, name):
        super().__init__(coordinator, key)
        self.key = key
        self._attr_name = name
        if key in ("last_seen", "command_time"):
            self._attr_device_class = SensorDeviceClass.TIMESTAMP
        elif key == "power":
            self._attr_device_class = SensorDeviceClass.ENUM
            self._attr_options = ["reachable", "sleep_requested", "standby", "waking"]
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self):
        data = self.coordinator.data
        value = getattr(data, self.key)
        return None if self.key == "power" and value == "unknown" else value

    @property
    def extra_state_attributes(self):
        if self.key == "power":
            return {
                "inferred": True,
                "display_power_observable": False,
                "network_error": self.coordinator.data.error,
            }
        if self.key == "command_result":
            return {"command": self.coordinator.data.last_command}
        return None
