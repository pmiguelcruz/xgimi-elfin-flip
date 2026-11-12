"""Bounded BlueZ wake transport for XGIMI; no Home Assistant state mutation.

Design reference: manymuch/Xgimi-4-Home-Assistant at
edbaae867e02adcb7cbf7b6e2cac805c4dcd1ec2. This implementation is independent.
An accepted transmission is NOT evidence that the projector woke up.
"""

# Do not enable postponed annotations: dbus-fast reads the D-Bus signatures.
import asyncio
import math
from contextlib import suppress
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from dbus_fast import Message, Variant
from dbus_fast.aio import MessageBus
from dbus_fast.constants import BusType, MessageType, PropertyAccess
from dbus_fast.service import ServiceInterface, dbus_property, method

COMPANY_ID = 0x0046
ADAPTER_IFACE = "org.bluez.Adapter1"
MANAGER_IFACE = "org.bluez.LEAdvertisingManager1"
ADVERTISEMENT_IFACE = "org.bluez.LEAdvertisement1"
DBUS_CALL_TIMEOUT = 5
CLEANUP_TIMEOUT = 3


class WakeError(Exception):
    """A transport/configuration error whose text contains no BLE token."""


@dataclass(frozen=True)
class AdvertisingAdapter:
    """A local advertising adapter; remote proxies are not BlueZ adapters."""

    path: str
    name: str
    address: str | None
    powered: bool
    available_instances: int | None


def normalize_token(token: str) -> bytes:
    """Validate the 16-byte payload, excluding the manufacturer identifier."""
    try:
        payload = bytes.fromhex(token)
    except (TypeError, ValueError) as err:
        raise WakeError("Manufacturer data must be 32 hexadecimal characters.") from err
    if len(payload) != 16:
        raise WakeError("Manufacturer data must contain exactly 16 bytes, without 46 00.")
    return payload


def _value(value: Any) -> Any:
    return value.value if isinstance(value, Variant) else value


async def _call(bus: Any, message: Message, timeout: float = DBUS_CALL_TIMEOUT) -> Message:
    try:
        reply = await asyncio.wait_for(bus.call(message), timeout)
    except Exception as err:
        raise WakeError("System D-Bus did not complete the Bluetooth operation.") from err
    if reply is None:
        raise WakeError("The system D-Bus connection was lost.")
    if reply.message_type == MessageType.ERROR:
        # BlueZ error names describe the failure without echoing any payload.
        raise WakeError(f"BlueZ rejected the operation: {reply.error_name}.")
    return reply


async def _connect(bus_factory: Any) -> Any:
    bus = bus_factory()
    try:
        await asyncio.wait_for(bus.connect(), DBUS_CALL_TIMEOUT)
    except asyncio.CancelledError:
        with suppress(Exception):
            bus.disconnect()
        raise
    except Exception as err:
        with suppress(Exception):
            bus.disconnect()
        raise WakeError("Cannot connect to the host system D-Bus for Bluetooth wake.") from err
    return bus


async def _adapters(bus: Any) -> list[AdvertisingAdapter]:
    reply = await _call(
        bus,
        Message(
            destination="org.bluez",
            path="/",
            interface="org.freedesktop.DBus.ObjectManager",
            member="GetManagedObjects",
        ),
    )
    if not reply.body or not isinstance(reply.body[0], dict):
        raise WakeError("BlueZ returned invalid adapter information.")
    result = []
    for path, interfaces in sorted(reply.body[0].items()):
        if ADAPTER_IFACE not in interfaces or MANAGER_IFACE not in interfaces:
            continue
        adapter = interfaces[ADAPTER_IFACE]
        manager = interfaces[MANAGER_IFACE]
        supported = _value(manager.get("SupportedInstances"))
        active = _value(manager.get("ActiveInstances", 0))
        available = None if supported is None else max(0, supported - active)
        result.append(
            AdvertisingAdapter(
                path=path,
                name=_value(adapter.get("Alias", adapter.get("Name", path))),
                address=_value(adapter.get("Address")),
                powered=bool(_value(adapter.get("Powered", False))),
                available_instances=available,
            )
        )
    return result


def _system_bus() -> MessageBus:
    return MessageBus(bus_type=BusType.SYSTEM)


async def async_discover_adapters(bus_factory: Any = _system_bus) -> list[AdvertisingAdapter]:
    """List local advertising-capable adapters without changing their power."""
    bus = await _connect(bus_factory)
    try:
        return await _adapters(bus)
    finally:
        bus.disconnect()


class _Advertisement(ServiceInterface):
    def __init__(self, payload: bytes, duration: float, released: asyncio.Event) -> None:
        super().__init__(ADVERTISEMENT_IFACE)
        self.payload = payload
        self.duration = duration
        self.released = released

    @dbus_property(access=PropertyAccess.READ, name="Type")
    def advertisement_type(self) -> "s":
        return "peripheral"

    @dbus_property(access=PropertyAccess.READ, name="ServiceUUIDs")
    def service_uuids(self) -> "as":
        return ["00001812-0000-1000-8000-00805f9b34fb"]

    @dbus_property(access=PropertyAccess.READ, name="ManufacturerData")
    def manufacturer_data(self) -> "a{qv}":
        return {COMPANY_ID: Variant("ay", self.payload)}

    @dbus_property(access=PropertyAccess.READ, name="Appearance")
    def appearance(self) -> "q":
        return 961

    @dbus_property(access=PropertyAccess.READ, name="LocalName")
    def local_name(self) -> "s":
        return "Bluetooth 4.0 RC"

    @dbus_property(access=PropertyAccess.READ, name="Timeout")
    def timeout(self) -> "q":
        # BlueZ's own expiry protects against a lost Python task/process.
        # Software cleanup normally happens earlier.
        return math.ceil(self.duration) + 2

    @method(name="Release")
    def release(self) -> None:
        self.released.set()


class LocalBlueZWake:
    """Transmit a finite advertisement, then release every owned resource."""

    def __init__(
        self,
        token: str,
        adapter_path: str,
        duration: float = 4,
        increment_counter: bool = False,
        bus_factory: Any = _system_bus,
    ) -> None:
        self._payload = normalize_token(token)
        if not 1 <= duration <= 10:
            raise WakeError("Advertisement duration must be between 1 and 10 seconds.")
        self.adapter_path = adapter_path
        self.duration = duration
        self.increment_counter = increment_counter
        self._counter = self._payload[0]
        self._bus_factory = bus_factory
        self._lock = asyncio.Lock()
        self._closed = asyncio.Event()

    async def _cleanup(self, bus: Any, path: str, advertisement: _Advertisement) -> None:
        try:
            # Attempt even if RegisterAdvertisement was cancelled: BlueZ may
            # have accepted the method before its reply reached this task.
            if bus.connected:
                with suppress(Exception):
                    await _call(
                        bus,
                        Message(
                            destination="org.bluez",
                            path=self.adapter_path,
                            interface=MANAGER_IFACE,
                            member="UnregisterAdvertisement",
                            signature="o",
                            body=[path],
                        ),
                        CLEANUP_TIMEOUT,
                    )
        finally:
            with suppress(Exception):
                bus.unexport(path, advertisement)
            bus.disconnect()

    async def async_wake(self) -> None:
        """Return after transmission only; the caller must verify network wake."""
        async with self._lock:
            if self._closed.is_set():
                raise WakeError("Bluetooth wake backend is closed.")
            bus = await _connect(self._bus_factory)
            try:
                adapters = await _adapters(bus)
                adapter = next((a for a in adapters if a.path == self.adapter_path), None)
                if adapter is None:
                    raise WakeError("The selected local adapter does not support BLE advertising.")
                if not adapter.powered:
                    raise WakeError("The selected Bluetooth adapter is powered off.")
                if adapter.available_instances == 0:
                    raise WakeError("The selected Bluetooth adapter has no free advertising slots.")
            except BaseException:
                bus.disconnect()
                raise
            payload = self._payload
            if self.increment_counter:
                payload = bytes([self._counter]) + payload[1:]
                self._counter = (self._counter + 1) % 256
            released = asyncio.Event()
            advertisement = _Advertisement(payload, self.duration, released)
            path = f"/org/homeassistant/xgimi_elfin_flip/wake_{uuid4().hex}"
            tasks = []
            try:
                bus.export(path, advertisement)
                await _call(
                    bus,
                    Message(
                        destination="org.bluez",
                        path=adapter.path,
                        interface=MANAGER_IFACE,
                        member="RegisterAdvertisement",
                        signature="oa{sv}",
                        body=[path, {}],
                    ),
                )
                duration = asyncio.create_task(asyncio.sleep(self.duration))
                closed = asyncio.create_task(self._closed.wait())
                release = asyncio.create_task(released.wait())
                disconnected = asyncio.create_task(bus.wait_for_disconnect())
                tasks = [duration, closed, release, disconnected]
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                if closed in done:
                    raise WakeError("Bluetooth wake backend closed during transmission.")
                if release in done or disconnected in done:
                    raise WakeError(
                        "The Bluetooth advertisement ended before its requested duration."
                    )
                if duration not in done or not bus.connected:
                    raise WakeError("Bluetooth transmission was interrupted.")
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                await self._cleanup(bus, path, advertisement)

    async def async_close(self) -> None:
        """Signal an active wake to stop and await its bounded cleanup."""
        self._closed.set()
        async with self._lock:
            pass
