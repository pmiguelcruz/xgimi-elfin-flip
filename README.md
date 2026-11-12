# XGIMI Elfin Flip for Home Assistant

<img src="custom_components/xgimi_elfin_flip/brand/icon.png" width="72" alt="Elfin Flip integration icon">

Local Wi-Fi integration for the original **XGIMI Elfin Flip WK03C** running XGIMI OS. Requires Home Assistant **2026.11.0** or later.

The integration adds one media player entity. Its **On** or **Off** state is inferred from the projector's DIAL service on the local network. A matching device response shows On. No response shows Off after two consecutive missed polls if the device was On. A reply from a different device or an invalid DIAL response shows Unknown. A network outage can also appear as Off, and the projector can keep serving DIAL while the image is off.

`media_player.turn_off` sends the projector's Wi-Fi standby request. The integration checks that the address still belongs to the configured projector before sending it. The action puts the projector in standby.

## Install

1. Add `https://github.com/pmiguelcruz/xgimi-elfin-flip` as a custom integration repository in Home Assistant, install **XGIMI Elfin Flip**, and restart Home Assistant. For a manual install, copy the release ZIP's `custom_components/xgimi_elfin_flip` folder into the Home Assistant configuration directory before restarting.
2. Go to **Settings → Devices & services → Add integration → XGIMI Elfin Flip**.
3. With the projector on, enter its local IP address or hostname. Use **Reconfigure** on the entry if that address changes.

Use your projector entity in a dashboard or automation.

```yaml
action: media_player.turn_off
target:
  entity_id: media_player.o_meu_elfin_flip
```

Existing installations retain the media player entity ID. Check any dashboards or automations that used the integration's older entities when upgrading to version 0.4.0.

## Verify and package

```bash
uv venv --python python3.14 .venv
uv pip install --python .venv/bin/python aiohttp defusedxml pytest pytest-asyncio ruff
.venv/bin/pytest
.venv/bin/ruff check custom_components tests build_release.py
python3.14 build_release.py
```

Run `python -m unittest discover -s tests -p ha_runtime_checks.py` in a Home Assistant Python environment to check the config migration and media player behavior against isolated fixtures.
