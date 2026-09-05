from __future__ import annotations

import unittest

from device_manager import CameraIntegrationRequired, DeviceManager
from hardware_controller import DoorState, OutputStatus


class FakePort:
    def to_dict(self):
        return {
            "device": "COM3",
            "description": "STM32 Virtual COM Port",
            "hwid": "USB VID:PID=0483:5740",
        }


class FakeSerialService:
    def __init__(self):
        self.is_connected = False
        self.port_name = ""
        self.disconnect_count = 0

    def list_ports(self):
        return [FakePort()]

    def connect(self, port):
        self.is_connected = True
        self.port_name = port

    def disconnect(self):
        self.disconnect_count += 1
        self.is_connected = False
        self.port_name = ""


class FakeHardwareController:
    def __init__(self, serial_service):
        self.serial = serial_service
        self.ping_count = 0
        self.fan_on_count = 0
        self.wheel_home_count = 0
        self.safe_stop_count = 0
        self.fault_clear_count = 0

    def ping(self):
        self.ping_count += 1
        return True

    def get_output_status(self):
        return OutputStatus(
            raw=0b00000111,
            fan_on=True,
            rgb_led_1_on=True,
            rgb_led_2_on=True,
            tungsten_1_on=False,
            tungsten_2_on=False,
        )

    def get_door_status(self):
        return DoorState.CLOSED

    def get_wheel_status(self):
        return 2

    def get_error_status(self):
        return 0

    def fan_on(self):
        self.fan_on_count += 1

    def wheel_home(self):
        self.wheel_home_count += 1

    def safe_stop(self):
        self.safe_stop_count += 1

    def fault_clear(self):
        self.fault_clear_count += 1


class FakeCameraManager:
    def __init__(self):
        self.probe_requests = []

    def status(self):
        return {
            "rgb": {
                "available": False,
                "connected": False,
                "transport": "UVC/DirectShow",
                "error": "RGB 相机未连接",
            },
            "multispectral": {
                "sdkAvailable": False,
                "available": False,
                "connected": False,
                "transport": "GigE/DVP2",
                "error": "多光谱 GigE 相机 DVP2 SDK 尚未安装",
            },
        }

    def checks(self, *, probe_rgb=False):
        self.probe_requests.append(probe_rgb)
        return {
            "rgbCamera": {
                "status": "not_connected",
                "label": "RGB 相机",
                "message": "RGB 相机未连接",
            },
            "multispectralCamera": {
                "status": "sdk_missing",
                "label": "多光谱相机",
                "message": "多光谱 GigE 相机 DVP2 SDK 尚未安装",
            },
        }


class DeviceManagerTests(unittest.TestCase):
    def make_manager(self):
        serial = FakeSerialService()
        cameras = FakeCameraManager()
        manager = DeviceManager(
            serial_service=serial,
            controller_factory=FakeHardwareController,
            camera_manager=cameras,
        )
        return manager, serial

    def test_lists_ports_as_json_ready_dictionaries(self):
        manager, _ = self.make_manager()

        ports = manager.list_ports()

        self.assertEqual(ports[0]["device"], "COM3")
        self.assertEqual(ports[0]["description"], "STM32 Virtual COM Port")

    def test_connect_pings_and_returns_complete_status(self):
        manager, _ = self.make_manager()

        status = manager.connect("COM3")

        self.assertTrue(status["connected"])
        self.assertEqual(status["port"], "COM3")
        self.assertEqual(status["door"], "closed")
        self.assertEqual(status["wheelPosition"], 2)
        self.assertTrue(status["wheelHomed"])
        self.assertTrue(status["fanOn"])
        self.assertIn("cameras", status)
        self.assertFalse(status["cameras"]["rgb"]["connected"])
        self.assertEqual(status["cameras"]["rgb"]["transport"], "UVC/DirectShow")
        self.assertEqual(status["cameras"]["multispectral"]["transport"], "GigE/DVP2")

    def test_self_test_does_not_move_wheel_by_default(self):
        manager, _ = self.make_manager()
        manager.connect("COM3")

        result = manager.self_test(include_motion=False)

        self.assertTrue(result["passed"])
        self.assertEqual(result["checks"]["controller"]["status"], "passed")
        self.assertEqual(result["checks"]["rgbCamera"]["status"], "not_connected")
        self.assertEqual(result["checks"]["multispectralCamera"]["status"], "sdk_missing")
        self.assertEqual(result["checks"]["calibration"]["status"], "manual_required")
        self.assertEqual(manager.controller.fan_on_count, 1)
        self.assertEqual(manager.controller.wheel_home_count, 0)
        self.assertEqual(manager.camera_manager.probe_requests[-1], False)

    def test_self_test_moves_wheel_only_when_requested(self):
        manager, _ = self.make_manager()
        manager.connect("COM3")

        result = manager.self_test(include_motion=True)

        self.assertEqual(result["checks"]["filterWheel"]["status"], "passed")
        self.assertEqual(manager.controller.wheel_home_count, 1)
        self.assertEqual(manager.camera_manager.probe_requests[-1], True)

    def test_start_capture_is_rejected_until_camera_service_exists(self):
        manager, _ = self.make_manager()
        manager.connect("COM3")

        with self.assertRaises(CameraIntegrationRequired):
            manager.start_capture("sample-001")

        self.assertEqual(manager.capture_status()["status"], "idle")
        self.assertEqual(manager.capture_status()["progress"], 0)

    def test_emergency_stop_and_fault_clear_update_state(self):
        manager, _ = self.make_manager()
        manager.connect("COM3")

        stopped = manager.emergency_stop()
        self.assertTrue(stopped["emergencyStopped"])
        self.assertEqual(manager.controller.safe_stop_count, 1)

        cleared = manager.fault_clear()
        self.assertFalse(cleared["emergencyStopped"])
        self.assertEqual(manager.controller.fault_clear_count, 1)

    def test_disconnect_safely_stops_then_closes_serial(self):
        manager, serial = self.make_manager()
        manager.connect("COM3")
        controller = manager.controller

        manager.disconnect()

        self.assertEqual(controller.safe_stop_count, 1)
        self.assertFalse(serial.is_connected)
        self.assertIsNone(manager.controller)


if __name__ == "__main__":
    unittest.main()
