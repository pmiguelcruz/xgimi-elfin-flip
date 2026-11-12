"""Constants for the WK03C integration."""

from homeassistant.const import Platform

DOMAIN = "xgimi_elfin_flip"
NAME = "XGIMI Elfin Flip"
PLATFORMS = [
    Platform.MEDIA_PLAYER,
    Platform.REMOTE,
    Platform.BUTTON,
    Platform.BINARY_SENSOR,
    Platform.SENSOR,
]
CONF_KEYBOARD = "keyboard_connection_sensor"
CONF_IDENTITY = "identity"
CONF_WAKE_BACKEND = "wake_backend"
CONF_WAKE_BUTTON = "wake_button"
CONF_BLE_DATA = "manufacturer_data"
CONF_BLE_ADAPTER = "bluetooth_adapter"
CONF_BLE_DURATION = "advertisement_duration"
CONF_INCREMENT = "increment_counter"
CONF_WAKE_TIMEOUT = "wake_timeout"
WAKE_NONE = "none"
WAKE_BUTTON = "button"
WAKE_LOCAL = "local_bluetooth"
DEFAULT_INTERVAL = 15
DEFAULT_WAKE_TIMEOUT = 60
