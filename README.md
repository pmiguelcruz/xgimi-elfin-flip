# XGIMI Elfin Flip for Home Assistant

<img src="custom_components/xgimi_elfin_flip/brand/icon.png" width="72" alt="Four-color Elfin Flip mark">

Custom integration for the original **XGIMI Elfin Flip WK03C running XGIMI OS**.
It uses the projector's DIAL service for standby, with optional
Bluetooth wake. Minimum Home Assistant: **2026.11.0**. Tested runtime: 2026.11.1.
The observed projector firmware is **V1.4.5 H0033**, build
`Feb 24 2025_13:11:11_61_XGIMI_WK03C_V1.4.5_H0033`, reported from the device's
About screen by its owner. DIAL does not return that firmware string.

This is independent of the `xgimi` custom integration. Its domain is
`xgimi_elfin_flip`, so it can be installed alongside another XGIMI integration.
The old integration's UDP remote commands are not sent by this integration.

## What is implemented

| Feature | Implementation and evidence |
| --- | --- |
| Standby/off | Empty HTTP POST to DIAL `apps/system?action=sleep`; physically confirmed on WK03C on November 10, 2026. This is sleep, not complete shutdown. |
| Optional wake | Existing Home Assistant `button`/`input_button` that transmits the BLE wake advertisement, or a local advertising-capable BlueZ adapter. Matching DIAL identity must reappear before wake is reported as confirmed. |
| Optional keyboard | D-pad, OK, Back, Menu, volume and mute through one Home Assistant Remote entity and a paired ESPHome HID keyboard bridge. |
| Network status | A separate **DIAL reachable** connectivity sensor. |
| Power observation | `reachable`, `sleep_requested`, `standby`, `waking`, or `unknown`, with an explicit inference attribute. |
| Device metadata | Manufacturer, model, device name and stable DIAL device identity. |
| Diagnostics | Command outcomes, last response/command timestamps, redacted configuration, and known capability limits. |

Each entity belongs to one device. The integration creates a media player,
remote, standby/refresh buttons, an optional wake button, and diagnostic sensors.
Upgrading to 0.1.2 removes the old app controls and app-state entities.

### Power state is inferred

The media player/remote have `assumed_state: true`. A successful identity response
is represented as inferred `on`/`reachable`; it does not establish whether the
lamp is lit, an HDMI input is selected, or video is playing. A sleep request
must be accepted and followed by loss of the DIAL service before inferred
`off`/`standby` is reported. Unexplained network loss is `unknown`, including an
initially unreachable device after Home Assistant restarts. A network fault can
still coincide with a sleep request; this remains an inference, not telemetry.

Wake stays pending until the same DIAL device identity responds. A button service
response or completed advertisement alone does not establish wake. Missing
hardware, unavailable wake buttons and verification timeouts produce actionable
errors instead of optimistic success.

An explicit turn-on always sends the configured wake signal, even if DIAL was
already reachable: the control processor can respond without proving that the
projection is on. In that case the command result explicitly says the device
was already reachable; it does not claim an observed wake transition.

## Controls not established on this firmware

DIAL supplies standby and reachability. The optional ESPHome bridge supplies
keyboard controls. HDMI selection, picture settings and projector telemetry
remain unverified on this model. Menu and volume behavior depend on the
projector's HID mappings.

## Install

1. Open **Settings → Marketplace → Add from link** and enter
   `https://github.com/pmiguelcruz/xgimi-elfin-flip`. On older HACS installations,
   use **Custom repositories** and select **Integration**.
2. Install **XGIMI Elfin Flip** and restart Home Assistant.
3. Go to **Settings → Devices & services → Add integration → XGIMI Elfin Flip**.
4. Enter the projector's bare IP address or hostname while it is awake. Setup
   validates XGIMI/WK03C identity and detects duplicate entries.
5. Open **Configure** for optional wake or an existing ESPHome keyboard bridge.

No `configuration.yaml` changes are required. To change the projector address,
use the entry menu's **Reconfigure** action; it refuses a different device identity.
After initial setup, Home Assistant can start with the projector asleep using
its saved identity; it does not need to wake it automatically.

For manual installation, copy the release ZIP’s `custom_components/xgimi_elfin_flip`
folder into your configuration directory and restart Home Assistant.

## Optional Bluetooth wake

Choose one backend in Configure:

- **none:** disable turn-on; Wi-Fi standby remains available.
- **button:** select an existing Home Assistant `button` or `input_button` whose
  action transmits the wake advertisement. This works with a purpose-built
  ESPHome advertiser. The button's firmware owns the actual payload; entering
  manufacturer data in the integration does not reprogram that firmware.
- **local_bluetooth:** select an advertising-capable local BlueZ adapter, such
  as `/org/bluez/hci0`, and enter the 16-byte manufacturer payload. The adapter
  must already be powered and have a free advertising slot. The integration
  does not power, reset or take over another adapter. Standard ESPHome Bluetooth
  proxies do not supply this arbitrary-advertising interface.

For local BlueZ, enter the manufacturer data **without** the company identifier.
The company ID is `0x0046`, and a 16-byte payload is represented by 32 hex
characters. The exact configured payload is transmitted by default; optional
counter incrementing is experimental and must be explicitly enabled. Payloads
are omitted from diagnostics.

For an ESPHome raw manufacturer field, prefix the supplied payload with
`46 00` (the little-endian company identifier). For example, a device-specific
payload beginning `09 E6 ...` becomes `46 00 09 E6 ...`. Use the complete payload
captured for your own projector, not another projector's Wi-Fi MAC. The
reference [ESPHome advertiser example](https://github.com/manymuch/Xgimi-4-Home-Assistant/blob/edbaae867e02adcb7cbf7b6e2cac805c4dcd1ec2/assets/esphome-xgimi-wake.yaml)
shows the manufacturer-data transmission mechanism.

The local advertiser uses bounded D-Bus operations, a finite advertisement
lifetime, and cleanup on cancellation, failure and unload. Home Assistant's
Bluetooth integration supplies the compatible `dbus-fast` dependency.

## Actions

Use the created media player's normal `media_player.turn_off` and, when enabled,
`media_player.turn_on` actions. The Standby and Wake buttons provide the same
operations. The remote supports these exact command strings:

```text
sleep
poweroff
wake
poweron
```

With a paired ESPHome HID bridge, select its **Projector Keyboard Connected**
binary sensor in Configure. The bridge should expose the `Projector Keyboard`
buttons on the same ESPHome device. The integration resolves the buttons from
the registry, so custom entity IDs for the buttons work. It blocks key commands
while the sensor reports disconnected. Use `pair` to open the bridge’s pairing
window, then select the bridge on the projector.

The available keyboard commands are `up`, `down`, `left`, `right`, `ok`,
`back`, `menu`, `volume_up`, `volume_down`, `mute` and `pair`. Use the
[example dashboard](examples/projector-remote.yaml) for a native keypad. Replace
the two example entity IDs with your projector remote and the ESPHome
connection sensor. Each shortcut calls `remote.send_command` on the same remote.

Unknown commands, repeats and held-key requests are rejected. There is no generic
`power` toggle that guesses the physical state.

## Validation and packaging

Transport tests use real local HTTP servers and XML fixtures. Home Assistant
runtime checks instantiate a separate Home Assistant instance with fake transport
and services; they do not modify the running instance or control a real projector.
The Bluetooth lifecycle tests use actual `dbus-fast` with a fake bus.

```bash
uv venv --python python3.14 .venv
uv pip install --python .venv/bin/python aiohttp defusedxml pytest pytest-asyncio ruff dbus-fast==5.0.22
.venv/bin/pytest tests/test_api.py tests/test_local_ble.py
.venv/bin/ruff check custom_components tests build_release.py
python3.14 -m compileall -q custom_components
python3.14 build_release.py
```

`tests/ha_runtime_checks.py` additionally requires the Home Assistant 2026.11.1
runtime and its normal dependencies. Run it in a separate Python process, not
inside the running application's event loop. The release builder excludes test
environments, research captures, private configuration and Python caches, then
reads the ZIP back and records its SHA-256 in `dist/`.

Remove the integration entry through **Devices & services**, then remove only
the `custom_components/xgimi_elfin_flip` directory and restart to uninstall.
This does not alter projector firmware or the separate wake button firmware.

## References

- [Original reference integration](https://github.com/manymuch/Xgimi-4-Home-Assistant), inspected at commit `edbaae867e02adcb7cbf7b6e2cac805c4dcd1ec2` (1.1.4).
- [DIAL specification](https://www.dial-multiscreen.org/dial/protocol-specification).
- [DIAL system sleep implementation](https://github.com/Netflix/dial-reference/blob/master/server/system_callbacks.c).
- [Official WK03C specifications](https://us.xgimi.com/pages/elfin-flip).
- [Official Elfin Flip smartphone-control limitation](https://helpcenter.xgimi.com/hc/en-gb/articles/38762916732057-Can-I-navigate-through-the-Elfin-Flip-series-interface-with-a-smartphone).
- [Official sleep/full-shutdown distinction](https://helpcenter.xgimi.com/hc/en-gb/articles/38762884020121-How-do-I-power-the-Elfin-Flip-off).

The implementation does not copy the reference integration's UDP command map.
It is designed around the observed WK03C DIAL implementation.
