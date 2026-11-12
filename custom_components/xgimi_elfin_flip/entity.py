"""One stable device identity shared by all entities."""

from homeassistant.helpers.device_registry import DeviceInfo as HADeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import ProjectorCoordinator


class ElfinEntity(CoordinatorEntity[ProjectorCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: ProjectorCoordinator, key: str) -> None:
        super().__init__(coordinator)
        identity = coordinator.identity
        self._attr_unique_id = f"{identity.udn}_{key}"
        self._attr_device_info = HADeviceInfo(
            identifiers={(DOMAIN, identity.udn)},
            name=identity.name,
            manufacturer=identity.manufacturer,
            model=identity.model,
        )

    @property
    def available(self) -> bool:
        # Offline is expected in standby. Wake/diagnostics must remain usable.
        return self.coordinator.last_update_success
