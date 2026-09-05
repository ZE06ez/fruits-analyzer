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
    match_host_adapter_for_device_ip,
)
from serial_service import SerialDependencyError, SerialService


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

    def test_pyserial_missing_is_reported_as_serial_domain_diagnostic(self):
        service = self.make_serial_service(SerialFactory({}), [])
        service._ports_provider = lambda: (_ for _ in ()).throw(SerialDependencyError("缺少 pyserial"))
        discovery = DeviceDiscovery(
            serial_service=service,
            serial_service_factory=lambda: service,
            rgb_scanner=lambda: [
                DeviceCandidate("uvc", None, "USB RGB Camera", {"backend": "DSHOW", "deviceIndex": 2})
            ],
            dvp2_scanner=lambda: [
                DeviceCandidate("dvp2", "GP23400004963", "MGV231M-H2", {"serial": "GP23400004963"})
            ],
        )

        result = discovery.discover_all()

        self.assertEqual(result["byKind"]["serial"], [])
        self.assertEqual(result["diagnostics"]["serial"]["status"], "dependency_missing")
        self.assertEqual(len(result["byKind"]["uvc"]), 1)
        self.assertEqual(len(result["byKind"]["dvp2"]), 1)

    def test_role_kind_validation_rejects_camera_as_controller(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = DeviceRegistry(Path(tmp) / "hardware_profile.json")
            camera = DeviceCandidate("uvc", None, "USB RGB Camera", {"backend": "DSHOW", "deviceIndex": 1})

            with self.assertRaises(ValueError):
                registry.bind(DeviceRole.MAIN_CONTROLLER, camera)

    def test_rgb_inferred_windows_mapping_does_not_promote_stable_id(self):
        camera = FakeRgbDiscoveryCamera(index_open=0)
        discovery = DeviceDiscovery(
            serial_service=self.make_serial_service(SerialFactory({}), []),
            serial_service_factory=lambda: self.make_serial_service(SerialFactory({}), []),
            camera_manager=type("Manager", (), {"rgb": camera})(),
            dvp2_scanner=lambda: [],
            rgb_max_index=0,
            windows_rgb_info_provider=lambda: [{
                "friendlyName": "USB Camera",
                "vid": "1D6C",
                "pid": "0103",
                "usbSerialNumber": "RGB123",
                "mappingConfidence": "inferred",
                "identitySource": "windows-pnp-usb-serial",
            }],
        )

        candidate = discovery.discover_rgb()[0]

        self.assertIsNone(candidate.stable_id)
        self.assertEqual(candidate.metadata["potentialStableId"], "usb:VID_1D6C&PID_0103:RGB123")
        self.assertEqual(candidate.metadata["mappingConfidence"], "inferred")

    def test_rgb_exact_windows_mapping_uses_usb_serial_stable_id(self):
        camera = FakeRgbDiscoveryCamera(index_open=2)
        discovery = DeviceDiscovery(
            serial_service=self.make_serial_service(SerialFactory({}), []),
            serial_service_factory=lambda: self.make_serial_service(SerialFactory({}), []),
            camera_manager=type("Manager", (), {"rgb": camera})(),
            dvp2_scanner=lambda: [],
            rgb_max_index=2,
            windows_rgb_info_provider=lambda: [{
                "deviceIndex": 2,
                "friendlyName": "USB Camera",
                "vid": "1D6C",
                "pid": "0103",
                "usbSerialNumber": "RGB123",
                "mappingConfidence": "exact",
                "identitySource": "windows-device-path",
            }],
        )

        candidate = discovery.discover_rgb()[0]

        self.assertEqual(candidate.stable_id, "usb:VID_1D6C&PID_0103:RGB123")
        self.assertTrue(candidate.metadata["stableIdentityAvailable"])

    def test_dvp2_host_adapter_match_uses_same_ipv4_network(self):
        adapter = match_host_adapter_for_device_ip(
            "169.254.25.110",
            [{"name": "Ethernet", "ip": "169.254.25.20", "prefixLength": 16}],
        )

        self.assertEqual(adapter["name"], "Ethernet")


class FakeRgbDiscoveryCamera:
    def __init__(self, index_open):
        self.index_open = index_open
        self.device_index = 1
        self.is_open = False
        self.config = None

    def configure(self, payload):
        self.device_index = int(payload.get("deviceIndex"))

    def open(self):
        if self.device_index != self.index_open:
            raise RuntimeError("camera not present")
        self.is_open = True

    def capture_frame(self):
        return type("Frame", (), {"shape": (2160, 3840, 3)})()

    def get_status(self):
        return type("Status", (), {"to_dict": lambda self: {
            "deviceName": "USB Camera",
            "actual": {"width": 3840, "height": 2160, "fps": 25.0, "fourcc": "MJPG"},
        }})()

    def close(self):
        self.is_open = False


if __name__ == "__main__":
    unittest.main()
