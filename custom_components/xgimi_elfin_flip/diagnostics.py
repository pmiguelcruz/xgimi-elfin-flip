"""Diagnostics omit wake payloads and device identifiers."""

from homeassistant.components.diagnostics import async_redact_data


async def async_get_config_entry_diagnostics(hass, entry):
    coordinator = entry.runtime_data
    data = coordinator.data
    return {
        "configuration": async_redact_data(
            dict(entry.options),
            {
                "manufacturer_data",
                "wake_button",
                "bluetooth_adapter",
            },
        ),
        "device": {
            "manufacturer": coordinator.identity.manufacturer,
            "model": coordinator.identity.model,
        },
        "observation": {
            "reachable": data.reachable,
            "power": data.power,
            "display_power_observable": False,
            "power_is_inferred": True,
            "last_command": data.last_command,
            "command_result": data.command_result,
            "last_seen": data.last_seen.isoformat() if data.last_seen else None,
            "error": data.error,
        },
        "limitations": [
            "DIAL reachability is not a measurement of display power.",
            "No WK03C navigation, volume, HDMI or picture API established.",
            "Sleep is standby, not full system shutdown.",
            "The port-8080 WebSocket service rejects this LAN client.",
        ],
    }
