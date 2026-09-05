from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from device_discovery import (
    DeviceBinding,
    DeviceCandidate,
    DeviceDiscovery,
    DeviceRegistry,
    DeviceRole,
)
from serial_service import SerialService


class FakePort:
    def __init__(
        self,
        device,
        description="",
        hwid="",
        *,
        vid=None,
        pid=None,
        serial_number=None,
        manufacturer=None,
        product=None,
        location=None,
    ):
        self.device = device
        self.description = description
        self.hwid = hwid
        self.vid = vid
        self.pid = pid
        self.serial_number = serial_number
        self.manufacturer = manufacturer
        self.product = product
        self.location = location


class FakeSerialPort:
    def __init__(self, responses=None, **kwargs):
        self.responses = responses or {}
        self.kwargs = kwargs
        self.timeout = kwargs.get("timeout", 0.05)
        self.is_open = True
        self.writes = []
        self.pending = bytearray()

    def reset_input_buffer(self):
        self.pending.clear()

    def reset_output_buffer(self):
        pass

    def write(self, data):
        packet = bytes(data)
        self.writes.append(packet)
        self.pending.extend(self.responses.get(packet, b""))
        return len(packet)

    def flush(self):
        pass

    def read(self, size):
        chunk = bytes(self.pending[:size])
        del self.pending[:size]
        return chunk

    def close(self):
        self.is_open = False


class SerialFactory:
    def __init__(self, responses_by_port):
        self.responses_by_port = responses_by_port
        self.instances = []

    def __call__(self, **kwargs):
        port = kwargs.get("port")
        serial = FakeSerialPort(self.responses_by_port.get(port, {}), **kwargs)
        self.instances.append(serial)
        return serial


class DeviceDiscoveryTests(unittest.TestCase):
    def make_serial_service(self, factory, ports):
        return SerialService(
            serial_factory=factory,
            ports_provider=lambda: ports,
            default_timeout_s=0.01,
        )

    def test_serial_discovery_records_usb_metadata_and_only_pings(self):
        ports = [
            FakePort(
                "COM3",
                "STM32 Virtual COM Port",
                "USB VID:PID=0483:5740 SER=CTRL-A",
                vid=0x0483,
                pid=0x5740,
                serial_number="CTRL-A",
                manufacturer="STMicroelectronics",
                product="STM32 Virtual COM Port",
                location="Port_#0003.Hub_#0001",
            ),
            FakePort("COM4", "Other Serial", "USB VID:PID=1234:5678", vid=0x1234, pid=0x5678),
        ]
        factory = SerialFactory({"COM3": {b"\x01\x5A": b"\x81\x5A"}})
        service = self.make_serial_service(factory, ports)
        discovery = DeviceDiscovery(
            serial_service=service,
            serial_service_factory=lambda: self.make_serial_service(factory, ports),
            rgb_scanner=lambda: [],
            dvp2_scanner=lambda: [],
        )

        candidates = discovery.discover_serial()

        self.assertEqual([candidate.connection for candidate in candidates], ["COM3", "COM4"])
        self.assertEqual(candidates[0].stable_id, "usb:VID_0483&PID_5740:CTRL-A")
        self.assertTrue(candidates[0].metadata["protocolMatched"])
        self.assertIsNone(candidates[0].metadata["deviceType"])
        self.assertIsNone(candidates[0].metadata["firmwareVersion"])
        self.assertEqual(candidates[0].metadata["manufacturer"], "STMicroelectronics")
        self.assertFalse(candidates[1].metadata["protocolMatched"])
        writes = [write for instance in factory.instances for write in instance.writes]
        self.assertEqual(writes, [b"\x01\x5A", b"\x01\x5A"])

    def test_serial_discovery_marks_connected_port_in_use_without_second_open(self):
        ports = [FakePort("COM7", "STM32", "USB VID:PID=0483:5740", vid=0x0483, pid=0x5740, serial_number="CTRL")]
        factory = SerialFactory({"COM7": {b"\x01\x5A": b"\x81\x5A"}})
        service = self.make_serial_service(factory, ports)
        service.connect("COM7")
        discovery = DeviceDiscovery(
            serial_service=service,
            serial_service_factory=lambda: self.make_serial_service(factory, ports),
            rgb_scanner=lambda: [],
            dvp2_scanner=lambda: [],
        )

        candidates = discovery.discover_serial()

        self.assertEqual(candidates[0].status, "in_use")
        self.assertTrue(candidates[0].metadata["inUse"])
        self.assertTrue(candidates[0].metadata["protocolMatched"])
        self.assertEqual(len(factory.instances), 1)

    def test_binding_saves_profile_and_matches_by_stable_id_after_port_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile = Path(tmp) / "runtime" / "hardware_profile.json"
            registry = DeviceRegistry(profile)
            first = DeviceCandidate(
                kind="serial",
                stable_id="usb:VID_0483&PID_5740:CTRL-A",
                display_name="STM32 Controller",
                connection="COM5",
                metadata={"protocolMatched": True},
            )

            binding = registry.bind(DeviceRole.MAIN_CONTROLLER, first)
            reloaded = DeviceRegistry(profile)
            moved = DeviceCandidate(
                kind="serial",
                stable_id="usb:VID_0483&PID_5740:CTRL-A",
                display_name="STM32 Controller",
                connection="COM7",
                metadata={"protocolMatched": True},
            )

            self.assertEqual(binding.last_port, "COM5")
            self.assertEqual(reloaded.bindings()[DeviceRole.MAIN_CONTROLLER].stable_id, first.stable_id)
            match = reloaded.snapshot([moved])["matches"][DeviceRole.MAIN_CONTROLLER]
            self.assertEqual(match["state"], "matched")
            self.assertEqual(match["method"], "stableId")
            self.assertEqual(match["candidate"]["connection"], "COM7")

    def test_binding_stays_unbound_when_candidate_missing(self):
        binding = DeviceBinding(
            role=DeviceRole.MAIN_CONTROLLER,
            kind="serial",
            stable_id="usb:VID_0483&PID_5740:MISSING",
            last_port="COM5",
        )
        with tempfile.TemporaryDirectory() as tmp:
            registry = DeviceRegistry(Path(tmp) / "hardware_profile.json")
            registry._bindings[DeviceRole.MAIN_CONTROLLER] = binding

            match = registry.snapshot([])["matches"][DeviceRole.MAIN_CONTROLLER]

        self.assertEqual(match["state"], "unbound")
        self.assertIsNone(match["candidate"])

    def test_rgb_discovery_does_not_auto_select_first_candidate(self):
        candidates = [
            DeviceCandidate("uvc", None, "Laptop Camera", {"backend": "DSHOW", "deviceIndex": 0}, metadata={"frameReadable": True}),
            DeviceCandidate("uvc", None, "USB Camera", {"backend": "DSHOW", "deviceIndex": 2}, metadata={"frameReadable": True}),
        ]
        discovery = DeviceDiscovery(
            serial_service=self.make_serial_service(SerialFactory({}), []),
            serial_service_factory=lambda: self.make_serial_service(SerialFactory({}), []),
            rgb_scanner=lambda: candidates,
            dvp2_scanner=lambda: [],
        )
        with tempfile.TemporaryDirectory() as tmp:
            registry = DeviceRegistry(Path(tmp) / "hardware_profile.json")

            discovered = discovery.discover_all()
            binding = registry.bind(DeviceRole.RGB_CAMERA, candidates[1])

        self.assertEqual(len(discovered["byKind"]["uvc"]), 2)
        self.assertEqual(binding.last_device_index, 2)
        self.assertIsNone(binding.stable_id)

    def test_dvp2_profile_matches_by_serial(self):
        first = DeviceCandidate(
            "dvp2",
            "GP23400004963",
            "MGV231M-H2",
            {"serial": "GP23400004963", "index": 0},
            metadata={"friendlyName": "MGV231M-H2", "mac": "B4-61-D3-14-6E-18", "ip": "169.254.25.110"},
        )
        second = DeviceCandidate(
            "dvp2",
            "OTHER001",
            "Other DVP2",
            {"serial": "OTHER001", "index": 1},
        )
        with tempfile.TemporaryDirectory() as tmp:
            registry = DeviceRegistry(Path(tmp) / "hardware_profile.json")
            registry.bind(DeviceRole.MULTISPECTRAL_CAMERA, first)

            match = registry.snapshot([second, first])["matches"][DeviceRole.MULTISPECTRAL_CAMERA]

        self.assertEqual(match["state"], "matched")
        self.assertEqual(match["method"], "stableId")
        self.assertEqual(match["candidate"]["stableId"], "GP23400004963")

    def test_device_not_plugged_in_returns_empty_candidate_lists(self):
        discovery = DeviceDiscovery(
            serial_service=self.make_serial_service(SerialFactory({}), []),
            serial_service_factory=lambda: self.make_serial_service(SerialFactory({}), []),
            rgb_scanner=lambda: [],
            dvp2_scanner=lambda: [],
        )

        result = discovery.discover_all()

        self.assertTrue(result["ok"])
        self.assertEqual(result["byKind"]["serial"], [])
        self.assertEqual(result["byKind"]["uvc"], [])
        self.assertEqual(result["byKind"]["dvp2"], [])


if __name__ == "__main__":
    unittest.main()
