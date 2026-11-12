"""Run coordinator/setup checks against installed HA, without a real projector.

Usage, from the integration repository with Home Assistant importable:
    python -m unittest discover -s tests -p ha_runtime_checks.py -v

All HomeAssistant objects use a new temporary configuration directory. DIAL is
replaced by an in-memory fake; services are registered only on those isolated
objects. This script never loads or writes the production /config state.
"""

from __future__ import annotations

import asyncio
import importlib
import sys
import tempfile
import unittest
from dataclasses import asdict, replace
from datetime import timedelta
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from homeassistant.config_entries import ConfigEntry, ConfigEntryState  # noqa: E402
from homeassistant.const import CONF_HOST, STATE_UNAVAILABLE, STATE_UNKNOWN  # noqa: E402
from homeassistant.core import HomeAssistant  # noqa: E402
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError  # noqa: E402
from homeassistant.helpers import device_registry as dr  # noqa: E402
from homeassistant.helpers import entity_registry as er  # noqa: E402

import custom_components.xgimi_elfin_flip as integration  # noqa: E402
import custom_components.xgimi_elfin_flip.coordinator as coordinator_module  # noqa: E402
from custom_components.xgimi_elfin_flip.api import (  # noqa: E402
    DeviceInfo,
    ProjectorConnectionError,
    ProjectorProtocolError,
)
from custom_components.xgimi_elfin_flip.const import (  # noqa: E402
    CONF_IDENTITY,
    CONF_KEYBOARD,
    CONF_WAKE_BACKEND,
    CONF_WAKE_BUTTON,
    CONF_WAKE_TIMEOUT,
    DOMAIN,
    WAKE_BUTTON,
    WAKE_NONE,
)
from custom_components.xgimi_elfin_flip.coordinator import ProjectorCoordinator  # noqa: E402
from custom_components.xgimi_elfin_flip.media_player import ElfinMediaPlayer  # noqa: E402
from custom_components.xgimi_elfin_flip.remote import ElfinRemote  # noqa: E402

IDENTITY = DeviceInfo(
    host="192.0.2.10",
    manufacturer="XGIMI",
    model="WK03C",
    name="Fixture projector",
    udn="uuid:fixture-projector",
    application_url="http://192.0.2.10:56789/apps/",
)


class FakeClient:
    """In-memory protocol outcomes; no aiohttp session or socket exists."""

    def __init__(self, mode="online", sleep_result="offline"):
        self.mode = mode
        self.sleep_result = sleep_result
        self.calls = []
        self.block_sleep = False
        self.sleep_started = asyncio.Event()
        self.sleep_cancelled = False

    async def async_get_device_info(self):
        self.calls.append("identity")
        if self.mode == "offline":
            raise ProjectorConnectionError("fixture network unavailable")
        if self.mode == "protocol":
            raise ProjectorProtocolError("fixture invalid description")
        if self.mode == "other":
            return replace(IDENTITY, udn="uuid:different-projector")
        return IDENTITY

    async def async_sleep(self):
        self.calls.append("sleep")
        self.sleep_started.set()
        if self.block_sleep:
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                self.sleep_cancelled = True
                raise
        self.mode = self.sleep_result


def config_entry(options=None):
    return ConfigEntry(
        version=1,
        minor_version=1,
        domain=DOMAIN,
        title="Fixture projector",
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
            async_reload=AsyncMock(return_value=True),
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
        self.coordinators = []
        self.extra_tasks = []
        self.presses = []

        async def press(call):
            self.presses.append(call.data["entity_id"])

        self.hass.services.async_register("button", "press", press)
        self.hass.services.async_register("input_button", "press", press)

    async def asyncTearDown(self):
        for task in self.extra_tasks:
            task.cancel()
        await asyncio.gather(*self.extra_tasks, return_exceptions=True)
        for coordinator in self.coordinators:
            await coordinator.async_close()
            await coordinator.async_shutdown()
        await self.hass.async_stop()
        self.directory.cleanup()

    async def make(self, mode="online", options=None, sleep_result="offline"):
        client = FakeClient(mode, sleep_result)
        entry = config_entry(options)
        coordinator = ProjectorCoordinator(self.hass, entry, client, IDENTITY)
        self.coordinators.append(coordinator)
        entry.runtime_data = coordinator
        await coordinator.async_config_entry_first_refresh()
        # Exercise the real refresh implementation without the HA debouncer's
        # cooldown obscuring deterministic command transition assertions.
        coordinator.async_request_refresh = coordinator.async_refresh
        return coordinator, client, entry

    def button_options(self, entity_id="button.fixture_wake", **extra):
        return {
            CONF_WAKE_BACKEND: WAKE_BUTTON,
            CONF_WAKE_BUTTON: entity_id,
            CONF_WAKE_TIMEOUT: 0.03,
            **extra,
        }

    async def test_startup_network_failure_remains_unknown_not_off(self):
        coordinator, _, _ = await self.make("offline")
        self.assertFalse(coordinator.data.reachable)
        self.assertEqual(coordinator.data.power, "unknown")
        self.assertEqual(coordinator.data.error, "unreachable")
        self.assertTrue(coordinator.last_update_success)

    async def test_real_remote_state_preserves_unknown(self):
        coordinator, _, _ = await self.make("offline")
        remote = ElfinRemote(coordinator)
        self.assertIsNone(remote.is_on)
        self.assertIsNone(remote.state)
        coordinator.async_set_updated_data(
            replace(coordinator.data, reachable=True, power="waking")
        )
        self.assertIsNone(remote.state)
        self.assertIsNone(ElfinMediaPlayer(coordinator).state)

    async def test_keyboard_routes_commands_to_the_configured_device(self):
        registry = er.async_get(self.hass)
        sensor = registry.async_get_or_create(
            "binary_sensor", "esphome", "fixture-keyboard",
            original_name="Projector Keyboard Connected",
        )
        up = registry.async_get_or_create(
            "button", "esphome", "fixture-up", original_name="Projector Keyboard Up",
            suggested_object_id="renamed_up",
        )
        volume = registry.async_get_or_create(
            "button", "esphome", "fixture-volume", original_name="Projector Keyboard Volume Down",
        )
        with patch("custom_components.xgimi_elfin_flip.keyboard.keyboard_device", return_value="fixture-device"):
            coordinator, _, _ = await self.make(options={CONF_KEYBOARD: sensor.entity_id})
            remote = ElfinRemote(coordinator)
            remote.hass = self.hass
            self.hass.states.async_set(up.entity_id, "unknown")
            self.hass.states.async_set(volume.entity_id, "unknown")
            self.hass.states.async_set(sensor.entity_id, "off")
            with patch.object(er, "async_entries_for_device", return_value=[up, volume]):
                with self.assertRaisesRegex(ServiceValidationError, "disconnected"):
                    await remote.async_send_command(["up"])
                self.hass.states.async_set(sensor.entity_id, "on")
                with self.assertRaisesRegex(ServiceValidationError, "unavailable"):
                    await remote.async_send_command(["up", "left"])
                self.assertEqual(self.presses, [])
                await remote.async_send_command(["up", "volume_down"])
                self.assertEqual(self.presses, [up.entity_id, volume.entity_id])

    async def test_upgrade_removes_renamed_app_entity_but_keeps_power_controls(self):
        entry = config_entry(self.button_options())
        other_entry = config_entry()
        entries = {item.entry_id: item for item in (entry, other_entry)}
        self.manager.async_get_entry = entries.get
        registry = er.async_get(self.hass)
        retired = registry.async_get_or_create(
            "button", DOMAIN, f"{IDENTITY.udn}_launch:YouTube", config_entry=entry
        )
        retired = registry.async_update_entity(
            retired.entity_id, new_entity_id="button.renamed_projector_app"
        )
        standby = registry.async_get_or_create(
            "button", DOMAIN, f"{IDENTITY.udn}_sleep", config_entry=entry
        )
        other_device = registry.async_get_or_create(
            "button", DOMAIN, "uuid:another-projector_launch:YouTube", config_entry=other_entry
        )

        self.assertTrue(await integration.async_migrate_entry(self.hass, entry))
        self.assertIsNone(registry.async_get(retired.entity_id))
        self.assertIsNotNone(registry.async_get(standby.entity_id))
        self.assertIsNotNone(registry.async_get(other_device.entity_id))
        self.manager.async_update_entry.assert_called_once_with(entry, minor_version=2)

    async def test_wake_without_backend_is_explicitly_unsupported(self):
        coordinator, _, _ = await self.make("offline", {CONF_WAKE_BACKEND: WAKE_NONE})
        with self.assertRaises(ServiceValidationError):
            await coordinator.async_wake()
        self.assertFalse(self.presses)
        self.assertEqual(coordinator.data.power, "unknown")

    async def test_nonexistent_button_cannot_report_success(self):
        coordinator, _, _ = await self.make("offline", self.button_options())
        with self.assertRaises(HomeAssistantError):
            await coordinator.async_wake()
        self.assertFalse(self.presses)
        self.assertEqual(coordinator.data.command_result, "failed")
        self.assertEqual(coordinator.data.power, "unknown")

    async def test_unavailable_button_is_not_pressed(self):
        self.hass.states.async_set("button.fixture_wake", STATE_UNAVAILABLE)
        coordinator, _, _ = await self.make("offline", self.button_options())
        with self.assertRaises(HomeAssistantError):
            await coordinator.async_wake()
        self.assertFalse(self.presses)
        self.assertEqual(coordinator.data.power, "unknown")

    async def test_own_button_is_rejected_to_prevent_recursive_service_calls(self):
        registry = er.async_get(self.hass)
        own = registry.async_get_or_create("button", DOMAIN, "fixture-own-wake")
        self.hass.states.async_set(own.entity_id, "2026-11-10T00:00:00Z")
        coordinator, _, _ = await self.make("offline", self.button_options(own.entity_id))
        with self.assertRaises(HomeAssistantError):
            await coordinator.async_wake()
        self.assertFalse(self.presses)

    async def test_sent_wake_timeout_never_marks_on(self):
        self.hass.states.async_set("button.fixture_wake", "2026-11-10T00:00:00Z")
        coordinator, _, _ = await self.make("offline", self.button_options())
        with self.assertRaises(HomeAssistantError):
            await coordinator.async_wake()
        self.assertEqual(self.presses, ["button.fixture_wake"])
        self.assertEqual(coordinator.data.command_result, "timed_out")
        self.assertEqual(coordinator.data.power, "unknown")
        self.assertFalse(coordinator.data.reachable)

    async def test_matching_identity_after_button_confirms_network_wake(self):
        self.hass.states.async_set("button.fixture_wake", "2026-11-10T00:00:00Z")
        coordinator, client, _ = await self.make("offline", self.button_options())

        async def wake(call):
            self.presses.append(call.data["entity_id"])
            client.mode = "online"

        self.hass.services.async_register("button", "press", wake)
        await coordinator.async_wake()
        self.assertEqual(coordinator.data.command_result, "confirmed_dial_reachable")
        self.assertEqual(coordinator.data.power, "reachable")
        self.assertTrue(coordinator.data.reachable)

    async def test_never_pressed_button_unknown_timestamp_is_valid(self):
        # ButtonEntity.state is its last-pressed timestamp and is None until
        # its first press. HA's unknown state is not an unavailable button.
        self.hass.states.async_set("button.fixture_wake", STATE_UNKNOWN)
        coordinator, client, _ = await self.make("offline", self.button_options())

        async def wake(call):
            self.presses.append(call.data["entity_id"])
            client.mode = "online"

        self.hass.services.async_register("button", "press", wake)
        await coordinator.async_wake()
        self.assertEqual(self.presses, ["button.fixture_wake"])
        self.assertEqual(coordinator.data.command_result, "confirmed_dial_reachable")

    async def test_already_reachable_dial_still_transmits_explicit_wake(self):
        self.hass.states.async_set("button.fixture_wake", STATE_UNKNOWN)
        coordinator, _, _ = await self.make("online", self.button_options())
        await coordinator.async_wake()
        self.assertEqual(self.presses, ["button.fixture_wake"])
        self.assertEqual(coordinator.data.last_command, "wake")
        self.assertEqual(coordinator.data.command_result, "wake_transmitted_dial_already_reachable")
        self.assertTrue(coordinator.data.reachable)
        self.assertNotEqual(coordinator.data.command_result, "confirmed_dial_reachable")

    async def test_concurrent_reachable_poll_preserves_inflight_waking_hint(self):
        self.hass.states.async_set("button.fixture_wake", STATE_UNKNOWN)
        coordinator, _, _ = await self.make("online", self.button_options())
        started = asyncio.Event()
        release = asyncio.Event()

        async def paused_button(call):
            started.set()
            await release.wait()

        self.hass.services.async_register("button", "press", paused_button)
        task = asyncio.create_task(coordinator.async_wake())
        self.extra_tasks.append(task)
        await started.wait()
        await coordinator.async_refresh()
        self.assertTrue(coordinator.data.reachable)
        self.assertEqual(coordinator.data.power, "waking")
        self.assertEqual(coordinator.data.command_result, "transmitting")
        release.set()
        await task
        self.assertEqual(coordinator.data.command_result, "wake_transmitted_dial_already_reachable")

    async def test_hung_button_call_has_bound_and_does_not_claim_wake_transmitted(self):
        self.hass.states.async_set("button.fixture_wake", "2026-11-10T00:00:00Z")
        coordinator, _, _ = await self.make("offline", self.button_options())

        async def slow_press(call):
            await asyncio.sleep(0.1)

        self.hass.services.async_register("button", "press", slow_press)
        original_timeout = asyncio.timeout
        requested_bounds = []

        def short_test_timeout(seconds):
            requested_bounds.append(seconds)
            return original_timeout(0.02 if seconds == 15 else seconds)

        with patch.object(coordinator_module.asyncio, "timeout", short_test_timeout):
            with self.assertRaisesRegex(HomeAssistantError, "within 15 seconds"):
                await coordinator.async_wake()
        self.assertIn(15, requested_bounds)
        self.assertEqual(coordinator.data.command_result, "failed")
        self.assertEqual(coordinator.data.power, "unknown")

    async def test_changed_identity_prevents_sleep(self):
        coordinator, client, _ = await self.make()
        client.mode = "other"
        with self.assertRaises(HomeAssistantError):
            await coordinator.async_sleep()
        self.assertNotIn("sleep", client.calls)
        await coordinator.async_refresh()
        self.assertEqual(coordinator.data.error, "identity_changed")
        self.assertEqual(coordinator.data.power, "unknown")

    async def test_changed_identity_also_prevents_bluetooth_wake(self):
        self.hass.states.async_set("button.fixture_wake", "2026-11-10T00:00:00Z")
        coordinator, client, _ = await self.make(options=self.button_options())
        client.mode = "other"
        with self.assertRaises(HomeAssistantError):
            await coordinator.async_wake()
        self.assertFalse(self.presses)

    async def test_sleep_accept_then_network_loss_is_labeled_inferred_standby(self):
        coordinator, client, _ = await self.make()
        await coordinator.async_sleep()
        self.assertIn("sleep", client.calls)
        self.assertEqual(coordinator.data.power, "standby")
        self.assertFalse(coordinator.data.reachable)
        self.assertEqual(coordinator.data.command_result, "sleep_accepted_network_unreachable")

    async def test_sleep_accept_then_invalid_response_is_unknown(self):
        coordinator, _, _ = await self.make(sleep_result="protocol")
        await coordinator.async_sleep()
        self.assertEqual(coordinator.data.power, "unknown")
        self.assertFalse(coordinator.data.reachable)
        self.assertEqual(coordinator.data.error, "invalid_device_response")

    async def test_sleep_accept_without_network_transition_is_unconfirmed(self):
        coordinator, _, _ = await self.make(sleep_result="online")
        await coordinator.async_sleep()
        self.assertEqual(coordinator.data.power, "sleep_requested")
        coordinator._command_time -= timedelta(seconds=31)
        await coordinator.async_refresh()
        self.assertEqual(coordinator.data.power, "reachable")
        self.assertEqual(coordinator.data.command_result, "sleep_accepted_standby_unconfirmed")

    async def test_expired_sleep_cannot_be_confirmed_by_a_later_unrelated_outage(self):
        coordinator, client, _ = await self.make(sleep_result="online")
        await coordinator.async_sleep()
        self.assertEqual(coordinator.data.power, "sleep_requested")
        coordinator._command_time -= timedelta(seconds=31)
        client.mode = "offline"
        await coordinator.async_refresh()
        self.assertEqual(coordinator.data.power, "unknown")
        self.assertEqual(coordinator.data.command_result, "sleep_accepted_standby_unconfirmed")
        self.assertFalse(coordinator.data.reachable)

    async def test_connection_deadline_crossing_sleep_window_does_not_confirm_standby(self):
        coordinator, client, _ = await self.make(sleep_result="online")
        await coordinator.async_sleep()
        command_time = coordinator._command_time
        client.mode = "offline"
        with patch.object(
            coordinator_module.dt_util,
            "utcnow",
            side_effect=[
                command_time + timedelta(seconds=29),
                command_time + timedelta(seconds=31),
            ],
        ):
            data = await coordinator._async_update_data()
        self.assertEqual(data.power, "unknown")
        self.assertEqual(data.command_result, "sleep_accepted_standby_unconfirmed")

    async def test_failed_control_publishes_failure_result(self):
        coordinator, client, _ = await self.make()
        client.mode = "offline"
        with self.assertRaises(HomeAssistantError):
            await coordinator.async_sleep()
        self.assertEqual(coordinator.data.last_command, "sleep")
        self.assertEqual(coordinator.data.command_result, "failed")

    async def test_options_update_reloads_only_its_entry(self):
        _, _, entry = await self.make()
        await integration._async_options_updated(self.hass, entry)
        self.manager.async_reload.assert_awaited_once_with(entry.entry_id)

    async def test_successful_unload_closes_runtime(self):
        coordinator, _, entry = await self.make()
        local = SimpleNamespace(async_close=AsyncMock())
        coordinator._local_wake = local
        self.assertTrue(await integration.async_unload_entry(self.hass, entry))
        self.assertTrue(coordinator._closed)
        local.async_close.assert_awaited_once()

    async def test_failed_platform_unload_does_not_close_running_runtime(self):
        coordinator, _, entry = await self.make()
        self.manager.async_unload_platforms.return_value = False
        self.assertFalse(await integration.async_unload_entry(self.hass, entry))
        self.assertFalse(coordinator._closed)

    async def test_closed_runtime_refuses_sleep(self):
        coordinator, client, _ = await self.make()
        await coordinator.async_close()
        with self.assertRaises(HomeAssistantError):
            await coordinator.async_sleep()
        self.assertNotIn("sleep", client.calls)

    async def test_unload_cancels_inflight_standby_command(self):
        coordinator, client, entry = await self.make()
        client.block_sleep = True
        task = asyncio.create_task(coordinator.async_sleep())
        self.extra_tasks.append(task)
        await client.sleep_started.wait()
        await integration.async_unload_entry(self.hass, entry)
        self.assertTrue(task.done())
        self.assertTrue(client.sleep_cancelled)

    async def test_setup_failure_closes_partially_created_runtime(self):
        entry = config_entry()
        failed = SimpleNamespace(
            async_config_entry_first_refresh=AsyncMock(side_effect=RuntimeError("fixture failure")),
            async_close=AsyncMock(),
        )
        with (
            patch.object(integration, "ProjectorCoordinator", return_value=failed),
            patch.object(integration, "DialClient", return_value=object()),
            patch.object(integration, "async_get_clientsession", return_value=None),
        ):
            with self.assertRaises(RuntimeError):
                await integration.async_setup_entry(self.hass, entry)
        failed.async_close.assert_awaited_once()
        self.manager.async_forward_entry_setups.assert_not_awaited()

    async def test_options_validation_rejects_missing_or_recursive_button(self):
        flow = importlib.import_module(f"custom_components.{DOMAIN}.config_flow")
        _, errors = await flow._validate_options(self.hass, self.button_options())
        self.assertEqual(errors, {CONF_WAKE_BUTTON: "invalid_button"})
        own = er.async_get(self.hass).async_get_or_create("button", DOMAIN, "own-option")
        self.hass.states.async_set(own.entity_id, "2026-11-10T00:00:00Z")
        _, errors = await flow._validate_options(self.hass, self.button_options(own.entity_id))
        self.assertEqual(errors, {CONF_WAKE_BUTTON: "invalid_button"})

    async def test_config_flow_first_form_uses_current_ha_api(self):
        flow_module = importlib.import_module(f"custom_components.{DOMAIN}.config_flow")
        flow = flow_module.ElfinFlipConfigFlow()
        flow.hass = self.hass
        result = await flow.async_step_user()
        self.assertEqual(result["type"], "form")
        self.assertEqual(result["step_id"], "user")
        self.assertEqual(
            result["data_schema"]({CONF_HOST: "192.0.2.10"}), {CONF_HOST: "192.0.2.10"}
        )

    async def test_options_first_form_and_save_use_current_ha_api(self):
        flow_module = importlib.import_module(f"custom_components.{DOMAIN}.config_flow")
        entry = config_entry()
        self.manager.async_get_known_entry = lambda entry_id: entry
        flow = flow_module.ElfinFlipOptionsFlow()
        flow.hass = self.hass
        flow.handler = entry.entry_id
        result = await flow.async_step_init()
        self.assertEqual(result["type"], "form")
        self.assertEqual(result["step_id"], "init")
        values = result["data_schema"]({CONF_WAKE_BACKEND: WAKE_NONE})
        saved = await flow.async_step_init(values)
        self.assertEqual(saved["type"], "create_entry")
        self.assertEqual(saved["data"][CONF_WAKE_BACKEND], WAKE_NONE)


class ImportChecks(unittest.TestCase):
    def test_every_integration_module_imports_against_real_ha(self):
        package = Path(integration.__file__).parent
        for path in sorted(package.glob("*.py")):
            if path.stem == "__init__":
                continue
            with self.subTest(module=path.stem):
                importlib.import_module(f"custom_components.{DOMAIN}.{path.stem}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
