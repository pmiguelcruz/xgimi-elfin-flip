# XGIMI Elfin Flip for Home Assistant

<img src="custom_components/xgimi_elfin_flip/brand/icon.png" width="72" alt="Elfin Flip integration icon">

Local power control for the original **XGIMI Elfin Flip WK03C** running XGIMI OS. Requires Home Assistant **2026.11.0** or later.

The integration adds one media player entity. Its **On** or **Off** state is inferred from the projector's DIAL service on the local network. A matching device response shows On. No response shows Off after two consecutive missed polls if the device was On. A reply from a different device or an invalid DIAL response shows Unknown. A network outage can also appear as Off, and the projector can keep serving DIAL while the image is off.

`media_player.turn_on` asks the ESPHome S3 Box to broadcast the projector's Bluetooth wake packet for four seconds. `media_player.turn_off` checks the projector's identity and sends its Wi-Fi standby request. Both actions use the same media player entity. State follows the DIAL poll after either command. If the projector stays off the network after the first wake request, Home Assistant tries once more and reports an error if it still cannot reach the projector.

## Install

1. Add `https://github.com/pmiguelcruz/xgimi-elfin-flip` as a custom integration repository in Home Assistant, install **XGIMI Elfin Flip**, and restart Home Assistant. For a manual install, copy the release ZIP's `custom_components/xgimi_elfin_flip` folder into the Home Assistant configuration directory before restarting.
2. Go to **Settings → Devices & services → Add integration → XGIMI Elfin Flip**.
3. With the projector on, enter its local IP address or hostname. Use **Reconfigure** on the entry if that address changes.

Existing installations keep their media player entity ID and projector address when updating to 0.5.0. With the S3 Box configured, **Turn on** becomes available after Home Assistant loads the update.

```yaml
action: media_player.turn_on
target:
  entity_id: media_player.your_projector
```

## S3 Box wake setup

On an ESPHome **2026.10.2** or later S3 Box with the node name `s3-box`, copy [`esphome/xgimi-wake.yaml`](esphome/xgimi-wake.yaml) into `common/` beside your ESPHome configuration and include it under the existing `packages` key.

```yaml
packages:
  xgimi_wake: !include common/xgimi-wake.yaml
```

Set `xgimi_wake_suffix` in your local ESPHome `secrets.yaml` to the last 15 bytes of your captured manufacturer payload, written as a quoted, comma-separated list of `0xNN` values. The package supplies the company ID and maintains the rolling first byte of the payload. Set `xgimi_projector_udn` to the full `UDN` from the projector's `http://HOST:56790/dd.xml`, including the `uuid:` prefix. The S3 checks that identifier before sending the wake packet.

Upload the firmware and let the ESPHome integration reconnect to the S3 Box. Home Assistant will register `esphome.s3_box_xgimi_wake` for the integration to use. The package adds no S3 Box entity. Standby and state polling remain available when the S3 Box is disconnected.

## Verify and package

```bash
uv venv --python python3.14 .venv
uv pip install --python .venv/bin/python aiohttp defusedxml pytest pytest-asyncio ruff
.venv/bin/pytest
.venv/bin/ruff check custom_components tests build_release.py
python3.14 build_release.py
```

Run `python -m unittest discover -s tests -p ha_runtime_checks.py` in a Home Assistant Python environment to check the config migration and media player behavior against isolated fixtures.
