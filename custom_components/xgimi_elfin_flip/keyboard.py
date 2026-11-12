"""Resolve the paired ESPHome keyboard's buttons from its device registry."""

from homeassistant.const import STATE_ON, STATE_UNAVAILABLE
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er

from .const import CONF_KEYBOARD

KEYBOARD_COMMANDS = {
    "up": "Up",
    "down": "Down",
    "left": "Left",
    "right": "Right",
    "ok": "Enter",
    "back": "Escape",
    "menu": "Menu",
    "volume_up": "Volume Up",
    "volume_down": "Volume Down",
    "mute": "Mute",
    "pair": "Pair for 90 seconds",
}


def keyboard_device(hass, sensor_id):
    """Identify the bridge using the source entity, including renamed entities."""
    sensor = er.async_get(hass).async_get(sensor_id)
    if (
        sensor
        and sensor.domain == "binary_sensor"
        and sensor.platform == "esphome"
        and sensor.original_name == "Projector Keyboard Connected"
    ):
        return sensor.device_id
    return None


def keyboard_targets(hass, options, commands):
    """Validate the requested keyboard commands before a sequence starts."""
    sensor_id = options.get(CONF_KEYBOARD)
    device_id = keyboard_device(hass, sensor_id) if sensor_id else None
    if device_id is None:
        raise ServiceValidationError("Choose the ESPHome keyboard connection sensor in Configure.")
    if any(command != "pair" for command in commands):
        status = hass.states.get(sensor_id)
        if status is None or status.state != STATE_ON:
            raise ServiceValidationError("The ESPHome projector keyboard is disconnected.")
    registry = er.async_get(hass)
    buttons = {
        entry.original_name: entry.entity_id
        for entry in er.async_entries_for_device(registry, device_id)
        if entry.domain == "button" and entry.platform == "esphome" and not entry.disabled
    }
    targets = {}
    for command in commands:
        entity_id = buttons.get(f"Projector Keyboard {KEYBOARD_COMMANDS[command]}")
        state = hass.states.get(entity_id) if entity_id else None
        if state is None or state.state == STATE_UNAVAILABLE:
            raise ServiceValidationError(f"The ESPHome button for {command} is unavailable.")
        targets[command] = entity_id
    return targets
