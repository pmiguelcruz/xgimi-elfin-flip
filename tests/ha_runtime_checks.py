"""Exercise the Wi-Fi integration against an isolated Home Assistant instance."""

from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from dataclasses import asdict, replace
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from homeassistant.components.media_player import MediaPlayerEntityFeature, MediaPlayerState  # noqa: E402
from homeassistant.config_entries import ConfigEntry, ConfigEntryState  # noqa: E402
from homeassistant.const import CONF_HOST, CONF_SCAN_INTERVAL  # noqa: E402
from homeassistant.core import HomeAssistant  # noqa: E402
from homeassistant.exceptions import HomeAssistantError  # noqa: E402
from homeassistant.helpers import device_registry as dr  # noqa: E402
from homeassistant.helpers import entity_registry as er  # noqa: E402

from custom_components import xgimi_elfin_flip as integration  # noqa: E402
from custom_components.xgimi_elfin_flip.api import (  # noqa: E402
    DeviceInfo,
    ProjectorCommandError,
    ProjectorConnectionError,
    ProjectorProtocolError,
)
from custom_components.xgimi_elfin_flip.config_flow import ElfinFlipConfigFlow  # noqa: E402
from custom_components.xgimi_elfin_flip.const import CONF_IDENTITY, DOMAIN, PLATFORMS  # noqa: E402
from custom_components.xgimi_elfin_flip.coordinator import ProjectorCoordinator  # noqa: E402
from custom_components.xgimi_elfin_flip.media_player import ElfinMediaPlayer  # noqa: E402

IDENTITY = DeviceInfo(
    host="192.0.2.10",
    manufacturer="XGIMI",
    model="WK03C",
    name="Fixture projector",
    udn="uuid:fixture-projector",
    application_url="http://192.0.2.10:56789/apps/",
)


def config_entry(options=None, minor_version=3):
    return ConfigEntry(
        version=1,
        minor_version=minor_version,
        domain=DOMAIN,
        title=IDENTITY.name,
        data={CONF_HOST: IDENTITY.host, CONF_IDENTITY: asdict(IDENTITY)},
        options=options or {},
        source="user",
        unique_id=IDENTITY.udn,
        discovery_keys=MappingProxyType({}),
        subentries_data=None,
        state=ConfigEntryState.SETUP_IN_PROGRESS,
    )


class RuntimeChecks(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="xgimi-isolated-ha-")
        self.hass = HomeAssistant(self.directory.name)
        self.manager = SimpleNamespace(
            async_forward_entry_setups=AsyncMock(),
            async_unload_platforms=AsyncMock(return_value=True),
            async_wait_initialized=AsyncMock(),
            async_get_entry=lambda _entry_id: None,
            async_update_entry=Mock(),
        )
        self.hass.config_entries = self.manager
        dr.async_setup(self.hass)
        await dr.async_load(self.hass, load_empty=True)
        await er.async_get(self.hass).async_load(load_empty=True)
        self.client = SimpleNamespace(
            async_get_device_info=AsyncMock(return_value=IDENTITY),
            async_sleep=AsyncMock(),
        )
        self.entry = config_entry()
        self.coordinator = ProjectorCoordinator(self.hass, self.entry, self.client, IDENTITY)
        self.player = ElfinMediaPlayer(self.coordinator)
        self.coordinator.async_request_refresh = self.coordinator.async_refresh

    async def asyncTearDown(self):
        await self.coordinator.async_close()
        await self.hass.async_stop()
        self.directory.cleanup()

    async def test_on_off_and_reconnect_through_polls(self):
        await self.coordinator.async_refresh()
        self.assertEqual(self.player.state, MediaPlayerState.ON)
        self.client.async_get_device_info.side_effect = ProjectorConnectionError
        await self.coordinator.async_refresh()
        self.assertEqual(self.player.state, MediaPlayerState.ON)
        await self.coordinator.async_refresh()
        self.assertEqual(self.player.state, MediaPlayerState.OFF)
        self.client.async_get_device_info.side_effect = None
        await self.coordinator.async_refresh()
        self.assertEqual(self.player.state, MediaPlayerState.ON)
        self.assertTrue(self.player.assumed_state)

    async def test_startup_offline_is_off_and_available(self):
        self.client.async_get_device_info.side_effect = ProjectorConnectionError
        await self.coordinator.async_config_entry_first_refresh()
        self.assertEqual(self.player.state, MediaPlayerState.OFF)
        self.assertTrue(self.player.available)

    async def test_turn_off_uses_only_wifi_and_refreshes(self):
        self.assertEqual(self.player.supported_features, MediaPlayerEntityFeature.TURN_OFF)
        self.assertEqual(self.player.unique_id, f"{IDENTITY.udn}_projector")
        self.client.async_sleep.side_effect = lambda: setattr(
            self.client.async_get_device_info, "side_effect", ProjectorConnectionError
        )
        await self.coordinator.async_refresh()
        await self.player.async_turn_off()
        self.client.async_sleep.assert_awaited_once_with()
        await self.coordinator.async_refresh()
        self.assertEqual(self.player.state, MediaPlayerState.OFF)

    async def test_http_acceptance_does_not_override_reachable_state(self):
        await self.player.async_turn_off()
        self.client.async_sleep.assert_awaited_once_with()
        self.assertEqual(self.player.state, MediaPlayerState.ON)

    async def test_different_projector_is_unknown_and_never_receives_standby(self):
        self.client.async_get_device_info.return_value = replace(IDENTITY, udn="uuid:other")
        await self.coordinator.async_refresh()
        self.assertIsNone(self.player.state)
        with self.assertRaises(HomeAssistantError):
            await self.player.async_turn_off()
        self.client.async_sleep.assert_not_awaited()

    async def test_protocol_and_standby_failure_do_not_assert_power_off(self):
        self.client.async_get_device_info.side_effect = ProjectorProtocolError
        await self.coordinator.async_refresh()
        self.assertIsNone(self.player.state)
        self.client.async_get_device_info.side_effect = None
        await self.coordinator.async_refresh()
        self.client.async_sleep.side_effect = ProjectorCommandError("HTTP 503")
        with self.assertRaises(HomeAssistantError):
            await self.player.async_turn_off()
        self.assertEqual(self.player.state, MediaPlayerState.ON)

    async def test_unloading_cancels_inflight_standby(self):
        started = asyncio.Event()

        async def sleep():
            started.set()
            await asyncio.Future()

        self.client.async_sleep.side_effect = sleep
        task = asyncio.create_task(self.player.async_turn_off())
        await asyncio.wait_for(started.wait(), timeout=1)
        await self.coordinator.async_close()
        self.assertTrue(task.cancelled())

    async def test_existing_entry_preserves_entity_and_interval(self):
        entry = config_entry({CONF_SCAN_INTERVAL: 30, "retired_setting": "fixture"}, 2)
        other = config_entry()
        entries = {entry.entry_id: entry, other.entry_id: other}
        self.manager.async_get_entry = entries.get
        registry = er.async_get(self.hass)
        power = registry.async_get_or_create(
            "media_player", DOMAIN, f"{IDENTITY.udn}_projector", config_entry=entry
        )
        retired = registry.async_get_or_create(
            "remote", DOMAIN, f"{IDENTITY.udn}_remote", config_entry=entry
        )
        unrelated = registry.async_get_or_create(
            "remote", DOMAIN, "uuid:other_remote", config_entry=other
        )
        self.assertTrue(await integration.async_migrate_entry(self.hass, entry))
        self.assertIsNotNone(registry.async_get(power.entity_id))
        self.assertIsNone(registry.async_get(retired.entity_id))
        self.assertIsNotNone(registry.async_get(unrelated.entity_id))
        self.manager.async_update_entry.assert_called_once_with(
            entry, minor_version=3, options={CONF_SCAN_INTERVAL: 30}
        )

    async def test_setup_and_unload_forward_only_the_media_player(self):
        with (
            patch.object(integration, "async_get_clientsession", return_value=None),
            patch.object(integration, "DialClient", return_value=self.client),
        ):
            self.assertTrue(await integration.async_setup_entry(self.hass, self.entry))
        self.manager.async_forward_entry_setups.assert_awaited_once_with(self.entry, PLATFORMS)
        self.assertEqual([platform.value for platform in PLATFORMS], ["media_player"])
        self.assertTrue(await integration.async_unload_entry(self.hass, self.entry))

    async def test_setup_form_asks_for_only_the_projector_address(self):
        flow = ElfinFlipConfigFlow()
        flow.hass = self.hass
        result = await flow.async_step_user()
        self.assertEqual(result["step_id"], "user")
        self.assertEqual(
            result["data_schema"]({CONF_HOST: "192.0.2.10"}), {CONF_HOST: "192.0.2.10"}
        )
