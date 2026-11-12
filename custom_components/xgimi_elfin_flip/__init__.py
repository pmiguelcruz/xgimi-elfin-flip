"""XGIMI Elfin Flip WK03C: model-specific DIAL control and optional BLE wake."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import DeviceInfo, DialClient
from .const import CONF_IDENTITY, PLATFORMS
from .coordinator import ProjectorCoordinator

type ElfinConfigEntry = ConfigEntry[ProjectorCoordinator]

_RETIRED_APP_IDS = ("Netflix", "AmazonInstantVideo", "YouTube", "YouTubeTV")


async def async_migrate_entry(hass: HomeAssistant, entry: ElfinConfigEntry) -> bool:
    """Retire the app entities from existing installations."""
    if entry.version != 1:
        return False
    if entry.minor_version < 2:
        udn = entry.data[CONF_IDENTITY]["udn"]
        retired_ids = {
            f"{udn}_{action}:{app}"
            for app in _RETIRED_APP_IDS
            for action in ("launch", "stop", "app")
        }
        retired_ids.add(f"{udn}_system_state")
        registry = er.async_get(hass)
        for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
            if entity.platform == entry.domain and entity.unique_id in retired_ids:
                registry.async_remove(entity.entity_id)
        hass.config_entries.async_update_entry(entry, minor_version=2)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ElfinConfigEntry) -> bool:
    """Load using cached identity so an asleep projector can still be woken."""
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
        entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    except BaseException:
        # Config-entry retries and interrupted platform setup must not retain
        # the coordinator scheduler or an owned Bluetooth advertisement.
        await coordinator.async_close()
        raise
    return True


async def _async_options_updated(hass: HomeAssistant, entry: ElfinConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ElfinConfigEntry) -> bool:
    if await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.async_close()
        return True
    return False
