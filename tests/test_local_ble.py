"""Transport lifecycle tests with a D-Bus fake; never open Bluetooth hardware."""

import asyncio
import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from dbus_fast.constants import MessageType

_module_path = (
    Path(__file__).resolve().parents[1] / "custom_components/xgimi_elfin_flip/local_ble.py"
)
_spec = importlib.util.spec_from_file_location("elfin_local_ble_test_target", _module_path)
ble = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = ble
_spec.loader.exec_module(ble)


TOKEN = "09E66759EF74D8FFFFFF3043524B544D"


class FakeBus:
    def __init__(self, mode="ok", powered=True, slots=1):
        self.connected = False
        self.mode = mode
        self.powered = powered
        self.slots = slots
        self.events = []
        self.registering = asyncio.Event()
        self.disconnected = asyncio.Event()
        self.exported = None

    async def connect(self):
        self.connected = True
        self.events.append("connect")
        return self

    async def call(self, message):
        self.events.append(message.member)
        if message.member == "GetManagedObjects":
            return SimpleNamespace(
                message_type=MessageType.METHOD_RETURN,
                body=[
                    {
                        "/org/bluez/hci7": {
                            ble.ADAPTER_IFACE: {"Powered": self.powered, "Alias": "USB Bluetooth"},
                            ble.MANAGER_IFACE: {
                                "SupportedInstances": self.slots,
                                "ActiveInstances": 0,
                            },
                        },
                        "/org/bluez/hci0": {ble.ADAPTER_IFACE: {"Powered": True}},
                    }
                ],
            )
        if message.member == "RegisterAdvertisement":
            self.registering.set()
            if self.mode == "hang_registration":
                await asyncio.Event().wait()
            if self.mode == "reject":
                return SimpleNamespace(
                    message_type=MessageType.ERROR, error_name="org.bluez.Error.NotPermitted"
                )
            if self.mode == "release":
                self.exported.released.set()
            if self.mode == "disconnect":
                self.disconnect()
        if message.member == "UnregisterAdvertisement" and self.mode == "hang_cleanup":
            await asyncio.Event().wait()
        return SimpleNamespace(message_type=MessageType.METHOD_RETURN, body=[])

    def export(self, path, advertisement):
        self.exported = advertisement
        self.events.append("export")

    def unexport(self, path, advertisement):
        self.events.append("unexport")

    def disconnect(self):
        self.connected = False
        self.disconnected.set()
        self.events.append("disconnect")

    async def wait_for_disconnect(self):
        await self.disconnected.wait()


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    def make(self, bus, **kwargs):
        return ble.LocalBlueZWake(
            TOKEN, "/org/bluez/hci7", duration=1, bus_factory=lambda: bus, **kwargs
        )

    async def test_exact_payload_and_company_identifier(self):
        bus = FakeBus()
        await self.make(bus).async_wake()
        manufacturer = bus.exported.manufacturer_data
        self.assertEqual(set(manufacturer), {0x0046})
        self.assertEqual(manufacturer[0x0046].value, bytes.fromhex(TOKEN))
        self.assertEqual(len(manufacturer[0x0046].value), 16)
        self.assertEqual(bus.exported.timeout, 3)
        self.assertEqual(bus.events[-3:], ["UnregisterAdvertisement", "unexport", "disconnect"])

    async def test_cancel_during_registration_still_unregisters(self):
        bus = FakeBus("hang_registration")
        task = asyncio.create_task(self.make(bus).async_wake())
        await bus.registering.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertIn("UnregisterAdvertisement", bus.events)
        self.assertIn("unexport", bus.events)
        self.assertFalse(bus.connected)

    async def test_rejected_registration_is_error_and_cleans_up(self):
        bus = FakeBus("reject")
        with self.assertRaisesRegex(ble.WakeError, "NotPermitted"):
            await self.make(bus).async_wake()
        self.assertFalse(bus.connected)
        self.assertIn("unexport", bus.events)

    async def test_release_before_duration_is_error(self):
        bus = FakeBus("release")
        with self.assertRaisesRegex(ble.WakeError, "ended before"):
            await self.make(bus).async_wake()
        self.assertFalse(bus.connected)

    async def test_bus_loss_is_error(self):
        bus = FakeBus("disconnect")
        with self.assertRaisesRegex(ble.WakeError, "ended before"):
            await self.make(bus).async_wake()
        self.assertIn("unexport", bus.events)

    async def test_close_interrupts_active_advertisement(self):
        bus = FakeBus()
        backend = self.make(bus)
        task = asyncio.create_task(backend.async_wake())
        await bus.registering.wait()
        await backend.async_close()
        with self.assertRaisesRegex(ble.WakeError, "closed"):
            await task
        self.assertFalse(bus.connected)
        with self.assertRaisesRegex(ble.WakeError, "closed"):
            await backend.async_wake()

    async def test_no_free_slots_never_registers(self):
        bus = FakeBus(slots=0)
        with self.assertRaisesRegex(ble.WakeError, "no free"):
            await self.make(bus).async_wake()
        self.assertNotIn("RegisterAdvertisement", bus.events)
        self.assertFalse(bus.connected)

    async def test_failed_unregister_still_disconnects_after_bounded_wait(self):
        bus = FakeBus("hang_cleanup")
        backend = self.make(bus)
        # The explicit cleanup timeout is a transport bound; this fake never
        # replies, and must not keep the owned advertisement/bus alive.
        with patch.object(ble, "CLEANUP_TIMEOUT", 0.02):
            await asyncio.wait_for(backend.async_wake(), 2)
        self.assertIn("unexport", bus.events)
        self.assertFalse(bus.connected)

    async def test_powered_off_adapter_is_not_powered_on(self):
        bus = FakeBus(powered=False)
        with self.assertRaisesRegex(ble.WakeError, "powered off"):
            await self.make(bus).async_wake()
        self.assertNotIn("Set", bus.events)
        self.assertFalse(bus.connected)

    async def test_discovery_does_not_assume_hci0(self):
        bus = FakeBus()
        adapters = await ble.async_discover_adapters(lambda: bus)
        self.assertEqual([a.path for a in adapters], ["/org/bluez/hci7"])
        self.assertFalse(bus.connected)

    async def test_optional_counter_changes_only_first_byte(self):
        bus = FakeBus()
        backend = self.make(bus, increment_counter=True)
        await backend.async_wake()
        first = bus.exported.manufacturer_data[0x0046].value
        bus.disconnected.clear()
        await backend.async_wake()
        second = bus.exported.manufacturer_data[0x0046].value
        self.assertEqual(second[0], (first[0] + 1) % 256)
        self.assertEqual(second[1:], first[1:])


class ValidationTests(unittest.TestCase):
    def test_reject_company_identifier_prepended(self):
        with self.assertRaises(ble.WakeError):
            ble.normalize_token("4600" + TOKEN)

    def test_invalid_hex_has_safe_message(self):
        with self.assertRaisesRegex(ble.WakeError, "32 hexadecimal"):
            ble.normalize_token("not a token")


if __name__ == "__main__":
    unittest.main()
