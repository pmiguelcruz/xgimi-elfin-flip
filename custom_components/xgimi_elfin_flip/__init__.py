"""Local Wi-Fi standby and power state for the XGIMI Elfin Flip WK03C."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import DeviceInfo, DialClient
from .const import CONF_IDENTITY, DOMAIN, PLATFORMS
from .coordinator import ProjectorCoordinator

type ElfinConfigEntry = ConfigEntry[ProjectorCoordinator]


async def async_migrate_entry(hass: HomeAssistant, entry: ElfinConfigEntry) -> bool:
    """Keep the existing power entity and the Wi-Fi polling setting."""
    if entry.version != 1:
        return False
    if entry.minor_version < 3:
        power_id = f"{entry.data[CONF_IDENTITY]['udn']}_projector"
        registry = er.async_get(hass)
        for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
            if entity.platform == DOMAIN and entity.unique_id != power_id:
                registry.async_remove(entity.entity_id)
        options = {
            key: value for key, value in entry.options.items() if key == CONF_SCAN_INTERVAL
        }
        hass.config_entries.async_update_entry(entry, minor_version=3, options=options)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ElfinConfigEntry) -> bool:
    """Load with the saved device identity, including when the projector is off."""
    coordinator = ProjectorCoordinator(
        hass,
        entry,
        DialClient(async_get_clientsession(hass), entry.data[CONF_HOST]),
        DeviceInfo(**entry.data[CONF_IDENTITY]),
    )
    entry.runtime_data = coordinator
    try:
        await coordinator.async_config_entry_first_refresh()
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except BaseException:
        await coordinator.async_close()
        raise
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ElfinConfigEntry) -> bool:
    if await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.async_close()
        return True
    return False
