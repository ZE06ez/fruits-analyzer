from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace

from device_discovery import DeviceCandidate, DeviceDiscovery, DeviceRegistry, DeviceRole
from device_manager import CameraIntegrationRequired, DeviceManager, UnsupportedCapabilityError
from hardware_controller import DoorState, OutputStatus
from sample_stage import SimulatedSampleStage
from serial_service import SerialDependencyError
from stm32_protocol import Stm32ProtocolProfile
from tests.test_capture_coordinator import FakeCameraManager as ReadyCameraManager
from tests.test_capture_coordinator import FakeHardwareController as ReadyHardwareController


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
            raw=0x04,
            fan_on=True,
            rgb_led_1_on=True,
            rgb_led_2_on=True,
            tungsten_1_on=False,
            tungsten_2_on=False,
            rgb_led_3_on=False,
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

    def probe_rgb(self):
        return {"passed": False, "status": self.status()["rgb"]}

    def probe_multispectral(self):
        return {"passed": False, "status": self.status()["multispectral"]}

    def _rgb_check(self, status):
        return {
            "status": "passed" if status.get("available") else "not_connected",
            "label": "RGB 相机",
            "message": status.get("error") or "RGB 相机已连接",
            "cameraStatus": status,
        }

    def _multispectral_check(self, status):
        return {
            "status": "sdk_missing" if not status.get("sdkAvailable") else "passed" if status.get("available") else "warning",
            "label": "多光谱相机",
            "message": status.get("error") or "多光谱相机状态",
            "cameraStatus": status,
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


class ConfigurableFakeRgb:
    def __init__(self):
        self.config = type("Config", (), {"to_dict": lambda self: {"deviceIndex": 1, "width": 3840, "height": 2160, "fps": 25, "fourcc": "MJPG"}})()
        self.applied = []

    def configure(self, payload):
        self.applied.append(dict(payload))
        self.config = type("Config", (), {"to_dict": lambda self: dict(payload)})()


class LossyScientificCameraManager(ReadyCameraManager):
    def rgb_scientific_status(self):
        return {
            "requestedFourcc": "MJPG",
            "actualFourcc": "MJPG",
            "sourcePixelFormat": "MJPG",
            "sourceCompression": "lossy",
            "scientificStrictLossless": False,
            "reason": "lossy_transport",
            "outputFormat": "PNG",
            "outputLossless": True,
        }


class ApprovedYuy2ScientificCameraManager(ReadyCameraManager):
    def rgb_scientific_status(self):
        return {
            "requestedFourcc": "YUY2",
            "actualFourcc": "YUY2",
            "sourcePixelFormat": "YUY2",
            "sourceCompression": "uncompressed_but_chroma_subsampled",
            "scientificStrictLossless": False,
            "scientificCaptureApproved": True,
            "scientificQualityClass": "uncompressed_422",
            "chromaSubsampling": "4:2:2",
            "reason": "chroma_subsampled_4_2_2",
            "outputFormat": "PNG",
            "outputLossless": True,
        }


class BindingCameraManager(FakeCameraManager):
    def __init__(self):
        super().__init__()
        self.rgb = ConfigurableFakeRgb()
        self.multispectral = type("Multi", (), {})()


class FakeLightStatus:
    def __init__(self, *, led_mask=0, fan_duty=100, error_code=0):
        self.led_mask = led_mask
        self.fan_duty = fan_duty
        self.fan_on = fan_duty > 0
        self.error_code = error_code
        self.led1_duty = 100 if led_mask & 0x01 else 0
        self.led2_duty = 100 if led_mask & 0x02 else 0
        self.led3_duty = 100 if led_mask & 0x04 else 0
        self.position_deg = 0.0
        self.target_deg = 0.0
        self.motor_state = "idle"

    def to_dict(self):
        return {
            "ledMask": self.led_mask,
            "fanDuty": self.fan_duty,
            "errorCode": self.error_code,
            "led1Duty": self.led1_duty,
            "led2Duty": self.led2_duty,
            "led3Duty": self.led3_duty,
        }


class FakeLightAdapter:
    def __init__(self, *, led_mask=0, fan_duty=100, fail_fresh_after_set=False):
        self.led_mask = led_mask
        self.fan_duty = fan_duty
        self.fail_fresh_after_set = fail_fresh_after_set
        self.revision = 0
        self.calls = []
        self.current_firmware_profile_validated = True
        self.tungsten_supported = True
        self.door_endpoint_feedback_supported = False
        self.automatic_homing_supported = False
        self.physical_encoder_verified = False
        self.info_cache = None

    @property
    def status_snapshot(self):
        return SimpleNamespace(revision=self.revision, received_monotonic=123.0, status=FakeLightStatus(led_mask=self.led_mask, fan_duty=self.fan_duty))

    def query_status(self, *, require_fresh=True, after_revision=None, timeout_s=None):
        if self.fail_fresh_after_set and after_revision is not None:
            raise RuntimeError("no fresh status")
        self.revision += 1
        return FakeLightStatus(led_mask=self.led_mask, fan_duty=self.fan_duty)

    def set_led_mask(self, mask, *, timeout_s=None):
        self.calls.append(("set_led_mask", int(mask)))
        self.led_mask = int(mask)
        return SimpleNamespace(ok=lambda: True)

    def validate_profile_consistency(self):
        return {}


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
        self.assertFalse(status["rgbLed3On"])
        self.assertTrue(status["stm32FirmwareProfile"]["enabled"])
        self.assertFalse(status["stm32FirmwareProfile"]["currentFirmwareProfileValidated"])
        self.assertTrue(status["stm32FirmwareProfile"]["tungstenSupported"])
        self.assertIn("cameras", status)
        self.assertFalse(status["cameras"]["rgb"]["connected"])
        self.assertEqual(status["cameras"]["rgb"]["transport"], "UVC/DirectShow")
        self.assertEqual(status["cameras"]["multispectral"]["transport"], "GigE/DVP2")
        self.assertEqual(status["sampleStage"]["lastError"], "SAMPLE_STAGE_PROTOCOL_UNKNOWN")
        self.assertFalse(status["sampleStage"]["protocolKnown"])

    def make_light_manager(self, adapter):
        manager, serial = self.make_manager()
        manager.connect("COM3")
        manager.stm32_adapter = adapter
        return manager, adapter

    def test_led3_bit_update_preserves_tungsten_bits(self):
        manager, adapter = self.make_light_manager(FakeLightAdapter(led_mask=0x03))

        result = manager.set_led3(True)

        self.assertEqual(adapter.calls[-1], ("set_led_mask", 0x07))
        self.assertEqual(result["ledMask"], 0x07)

    def test_tungsten_channel_updates_preserve_other_bits(self):
        manager, adapter = self.make_light_manager(FakeLightAdapter(led_mask=0x00))

        on = manager.set_tungsten(1, True, duration_ms=1000, operator_confirmed_safety=True)
        adapter.led_mask = 0x07
        off = manager.set_tungsten(2, False)

        self.assertEqual(on["requestedMask"], 0x01)
        self.assertEqual(off["requestedMask"], 0x05)
        self.assertIn(("set_led_mask", 0x01), adapter.calls)
        self.assertIn(("set_led_mask", 0x05), adapter.calls)

    def test_tungsten_all_off_preserves_led3(self):
        manager, adapter = self.make_light_manager(FakeLightAdapter(led_mask=0x07))

        result = manager.tungsten_all_off()

        self.assertEqual(adapter.calls[-1], ("set_led_mask", 0x04))
        self.assertEqual(result["confirmedMask"], 0x04)

    def test_tungsten_emergency_all_off_sends_zero(self):
        manager, adapter = self.make_light_manager(FakeLightAdapter(led_mask=0x07))

        result = manager.tungsten_all_off(emergency=True)

        self.assertEqual(adapter.calls[-1], ("set_led_mask", 0x00))
        self.assertEqual(result["confirmedMask"], 0x00)

    def test_tungsten_open_requires_connection_fan_and_operator_confirmation(self):
        manager, adapter = self.make_light_manager(FakeLightAdapter(led_mask=0x00, fan_duty=0))

        with self.assertRaises(Exception):
            manager.set_tungsten(1, True, duration_ms=1000, operator_confirmed_safety=True)
        adapter.fan_duty = 100
        with self.assertRaises(Exception):
            manager.set_tungsten(1, True, duration_ms=1000, operator_confirmed_safety=False)

    def test_tungsten_open_refuses_dual_channel_when_not_accepted(self):
        manager, _ = self.make_light_manager(FakeLightAdapter(led_mask=0x02))

        with self.assertRaises(Exception):
            manager.set_tungsten(1, True, duration_ms=1000, operator_confirmed_safety=True)

    def test_ack_without_fresh_status_does_not_report_confirmed(self):
        manager, _ = self.make_light_manager(FakeLightAdapter(led_mask=0x00, fail_fresh_after_set=True))

        with self.assertRaises(Exception):
            manager.set_tungsten(1, True, duration_ms=1000, operator_confirmed_safety=True)

    def test_tungsten_auto_off_closes_without_reopening(self):
        manager, adapter = self.make_light_manager(FakeLightAdapter(led_mask=0x00))

        manager.set_tungsten(1, True, duration_ms=1000, operator_confirmed_safety=True)
        token = manager._tungsten_tokens[1]
        manager._auto_off_tungsten(1, token)

        self.assertEqual(adapter.calls[-1], ("set_led_mask", 0x00))
        self.assertEqual(adapter.calls.count(("set_led_mask", 0x01)), 1)

    def test_current_firmware_profile_wraps_same_serial_owner_in_adapter(self):
        serial = FakeSerialService()
        profile = Stm32ProtocolProfile(
            ack_cmd=0x80,
            status_cmd=0x81,
            info_cmd=0x82,
            query_status_cmd=0x20,
            move_abs_cmd=0x21,
            move_rel_cmd=0x22,
            stop_cmd=0x23,
            set_profile_cmd=0x24,
            set_origin_cmd=0x25,
        )
        manager = DeviceManager(
            serial_service=serial,
            controller_factory=FakeHardwareController,
            camera_manager=FakeCameraManager(),
            stm32_protocol_profile=profile,
        )

        manager.connect("COM3")

        self.assertIsNotNone(manager.stm32_adapter)
        self.assertIs(manager.stm32_adapter.transport, serial)
        self.assertIs(manager.controller.serial, manager.stm32_adapter)
        self.assertTrue(manager.status()["stm32FirmwareProfile"]["enabled"])

    def test_self_test_does_not_move_wheel_by_default(self):
        manager, _ = self.make_manager()
        manager.connect("COM3")

        result = manager.self_test(include_motion=False)

        self.assertTrue(result["passed"])
        self.assertEqual(result["checks"]["controller"]["status"], "passed")
        self.assertEqual(result["checks"]["rgbCamera"]["status"], "not_connected")
        self.assertEqual(result["checks"]["multispectralCamera"]["status"], "sdk_missing")
        self.assertEqual(result["checks"]["calibration"]["status"], "manual_required")
        self.assertEqual(manager.controller.fan_on_count, 0)
        self.assertEqual(manager.controller.wheel_home_count, 0)
        self.assertEqual(manager.camera_manager.probe_requests[-1], False)

    def test_self_test_does_not_move_wheel_when_motion_requested(self):
        manager, _ = self.make_manager()
        manager.connect("COM3")

        result = manager.self_test(include_motion=True)

        self.assertFalse(result["includeMotion"])
        self.assertTrue(result["motionRequestedIgnored"])
        self.assertEqual(result["checks"]["filterWheel"]["status"], "manual_required")
        self.assertEqual(manager.controller.fan_on_count, 0)
        self.assertEqual(manager.controller.wheel_home_count, 0)
        self.assertEqual(manager.camera_manager.probe_requests[-1], False)

    def test_independent_device_check_runs_cameras_when_stm32_is_not_connected(self):
        manager, _ = self.make_manager()

        result = manager.independent_device_check()

        self.assertFalse(result["passed"])
        self.assertTrue(result["independentDomains"])
        self.assertEqual(result["checks"]["controller"]["status"], "not_connected")
        self.assertEqual(result["checks"]["rgbCamera"]["status"], "not_connected")
        self.assertEqual(result["checks"]["multispectralCamera"]["status"], "sdk_missing")
        self.assertFalse(result["status"]["connected"])

    def test_rgb_binding_updates_runtime_camera_device_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            cameras = BindingCameraManager()
            candidate = DeviceCandidate(
                "uvc",
                None,
                "USB RGB Camera",
                {"backend": "DSHOW", "deviceIndex": 2},
                status="available",
            )
            discovery = DeviceDiscovery(
                serial_service=FakeSerialService(),
                serial_service_factory=FakeSerialService,
                rgb_scanner=lambda: [candidate],
                dvp2_scanner=lambda: [],
            )
            manager = DeviceManager(
                serial_service=FakeSerialService(),
                controller_factory=FakeHardwareController,
                camera_manager=cameras,
                discovery=discovery,
                registry=DeviceRegistry(Path(tmp) / "hardware_profile.json"),
            )

            manager.bind_device({
                "role": DeviceRole.RGB_CAMERA,
                "kind": "uvc",
                "connection": {"backend": "DSHOW", "deviceIndex": 2},
            })

        self.assertEqual(cameras.rgb.applied[-1]["deviceIndex"], 2)

    def test_independent_check_reports_pyserial_missing_without_blocking_cameras(self):
        serial = FakeSerialService()
        serial.list_ports = lambda: (_ for _ in ()).throw(SerialDependencyError("缺少 pyserial"))
        manager = DeviceManager(
            serial_service=serial,
            controller_factory=FakeHardwareController,
            camera_manager=FakeCameraManager(),
        )

        result = manager.independent_device_check()

        self.assertEqual(result["checks"]["controller"]["status"], "dependency_missing")
        self.assertEqual(result["checks"]["rgbCamera"]["status"], "not_connected")
        self.assertEqual(result["checks"]["multispectralCamera"]["status"], "sdk_missing")

    def test_start_capture_is_rejected_until_camera_service_exists(self):
        manager, _ = self.make_manager()
        manager.connect("COM3")

        with self.assertRaises(CameraIntegrationRequired):
            manager.start_capture("sample-001")

        self.assertEqual(manager.capture_status()["status"], "idle")
        self.assertEqual(manager.capture_status()["progress"], 0)

    def test_true_capture_readiness_and_single_view_start_use_coordinator(self):
        hardware = ReadyHardwareController()
        hardware.wheel_position = 0
        camera = ReadyCameraManager()
        serial = FakeSerialService()
        manager = DeviceManager(
            serial_service=serial,
            controller_factory=lambda _transport: hardware,
            camera_manager=camera,
            stm32_protocol_profile=None,
        )
        manager.connect("COM3")

        with tempfile.TemporaryDirectory(prefix="dm_true_capture_") as tmp:
            payload = {
                "sampleId": "S-DM-TRUE",
                "outputDir": tmp,
                "captureMode": "single_view",
                "calibrationMode": "none",
                "requireCalibration": False,
                "settlingMs": 0,
                "rgbLedMask": 0x04,
                "tungstenMask": 0x01,
                "bandPlan": [{"bandId": "A520", "wheelPosition": 1, "wavelengthNm": 520}],
            }
            readiness = manager.capture_readiness(payload)
            capture = manager.start_capture("S-DM-TRUE", payload=payload)

            self.assertTrue(readiness["ready"])
            self.assertTrue(readiness["trueCapturePrepared"])
            self.assertEqual(capture["state"], "completed")
            self.assertTrue((Path(tmp) / "metadata.json").exists())
            self.assertTrue((Path(tmp) / "views" / "view_000" / "rgb" / "rgb_view_000.png").exists())
            self.assertEqual(camera.capture_count, 1)
            self.assertEqual(camera.multispectral_capture_count, 1)

    def test_true_capture_readiness_blocks_legacy_light_masks(self):
        hardware = ReadyHardwareController()
        hardware.wheel_position = 0
        serial = FakeSerialService()
        manager = DeviceManager(
            serial_service=serial,
            controller_factory=lambda _transport: hardware,
            camera_manager=ReadyCameraManager(),
            stm32_protocol_profile=None,
        )
        manager.connect("COM3")

        readiness = manager.capture_readiness({
            "sampleId": "S-DM-LIGHT",
            "outputDir": str(Path(tempfile.gettempdir()) / "dm_light_block"),
            "captureMode": "single_view",
            "calibrationMode": "none",
            "requireCalibration": False,
            "rgbLedMask": 0x03,
            "tungstenMask": 0x03,
            "bandPlan": [{"bandId": "A520", "wheelPosition": 1, "wavelengthNm": 520}],
        })

        codes = [item["code"] for item in readiness["blockingReasons"]]
        self.assertIn("RGB_LIGHT_MAPPING_NOT_CONFIRMED", codes)
        self.assertIn("DUAL_TUNGSTEN_NOT_ACCEPTED", codes)

    def test_true_capture_multiview_blocks_on_sample_stage_protocol_unknown(self):
        hardware = ReadyHardwareController()
        hardware.wheel_position = 0
        serial = FakeSerialService()
        manager = DeviceManager(
            serial_service=serial,
            controller_factory=lambda _transport: hardware,
            camera_manager=ReadyCameraManager(),
            stm32_protocol_profile=None,
        )
        manager.connect("COM3")
        payload = {
            "sampleId": "S-DM-MV",
            "outputDir": str(Path(tempfile.gettempdir()) / "dm_true_capture_mv"),
            "captureMode": "multi_view",
            "calibrationMode": "none",
            "requireCalibration": False,
            "rotationPlan": {
                "enabled": True,
                "views": [
                    {"viewId": "view_000", "logicalAngleDeg": 0, "mechanicalAngleDeg": 0, "captureOrder": 1},
                    {"viewId": "view_090", "logicalAngleDeg": 90, "mechanicalAngleDeg": 90, "captureOrder": 2},
                ],
            },
            "bandPlan": [{"bandId": "A520", "wheelPosition": 1, "wavelengthNm": 520}],
        }

        readiness = manager.capture_readiness(payload)

        self.assertFalse(readiness["ready"])
        self.assertIn("SAMPLE_STAGE_NOT_READY", [item["code"] for item in readiness["blockingReasons"]])
        self.assertIn("SAMPLE_STAGE_PROTOCOL_UNKNOWN", [item.get("detailCode") for item in readiness["blockingReasons"]])
        with self.assertRaises(CameraIntegrationRequired):
            manager.start_capture("S-DM-MV", payload=payload)

    def test_true_capture_readiness_blocks_lossy_rgb_scientific_transport(self):
        hardware = ReadyHardwareController()
        hardware.wheel_position = 0
        serial = FakeSerialService()
        manager = DeviceManager(
            serial_service=serial,
            controller_factory=lambda _transport: hardware,
            camera_manager=LossyScientificCameraManager(),
            stm32_protocol_profile=None,
        )
        manager.connect("COM3")

        readiness = manager.capture_readiness({
            "sampleId": "S-DM-RGB-LOSSY",
            "outputDir": str(Path(tempfile.gettempdir()) / "dm_rgb_lossy"),
            "captureMode": "single_view",
            "calibrationMode": "none",
            "requireCalibration": False,
            "settlingMs": 0,
            "bandPlan": [{"bandId": "A520", "wheelPosition": 1, "wavelengthNm": 520}],
        })

        self.assertFalse(readiness["ready"])
        self.assertIn("CAMERA_NOT_READY", [item["code"] for item in readiness["blockingReasons"]])
        self.assertIn("RGB_SCIENTIFIC_TRANSPORT_LOSSY", [item.get("detailCode") for item in readiness["blockingReasons"]])
        self.assertFalse(readiness["capabilities"]["rgbScientificStrictLossless"])
        self.assertFalse(readiness["capabilities"]["rgbScientificCaptureApproved"])

    def test_true_capture_readiness_allows_yuy2_scientific_transport(self):
        hardware = ReadyHardwareController()
        hardware.wheel_position = 0
        serial = FakeSerialService()
        manager = DeviceManager(
            serial_service=serial,
            controller_factory=lambda _transport: hardware,
            camera_manager=ApprovedYuy2ScientificCameraManager(),
            stm32_protocol_profile=None,
        )
        manager.connect("COM3")

        readiness = manager.capture_readiness({
            "sampleId": "S-DM-RGB-YUY2",
            "outputDir": str(Path(tempfile.gettempdir()) / "dm_rgb_yuy2"),
            "captureMode": "single_view",
            "calibrationMode": "none",
            "requireCalibration": False,
            "settlingMs": 0,
            "rgbEnabled": True,
            "multispectralEnabled": False,
        })

        self.assertTrue(readiness["ready"])
        self.assertFalse(readiness["capabilities"]["rgbScientificStrictLossless"])
        self.assertTrue(readiness["capabilities"]["rgbScientificCaptureApproved"])

    def test_emergency_stop_and_fault_clear_update_state(self):
        manager, _ = self.make_manager()
        manager.connect("COM3")

        stopped = manager.emergency_stop()
        self.assertTrue(stopped["emergencyStopped"])
        self.assertEqual(manager.controller.safe_stop_count, 1)

        with self.assertRaises(UnsupportedCapabilityError):
            manager.fault_clear()
        self.assertEqual(manager.controller.fault_clear_count, 0)

    def test_sample_stage_default_boundary_reports_protocol_unknown(self):
        manager, _ = self.make_manager()

        status = manager.sample_stage_status()

        self.assertFalse(status["available"])
        self.assertFalse(status["protocolKnown"])
        self.assertFalse(status["positionFeedbackSupported"])
        self.assertEqual(status["lastError"], "SAMPLE_STAGE_PROTOCOL_UNKNOWN")
        with self.assertRaises(UnsupportedCapabilityError):
            manager.sample_stage_home()

    def test_sample_stage_adapter_commands_stay_independent_from_filter_wheel(self):
        stage = SimulatedSampleStage()
        manager = DeviceManager(
            serial_service=FakeSerialService(),
            controller_factory=FakeHardwareController,
            camera_manager=FakeCameraManager(),
            sample_stage_controller=stage,
        )

        home = manager.sample_stage_home()
        move = manager.sample_stage_move_absolute(90)
        relative = manager.sample_stage_move_relative(30)
        stopped = manager.sample_stage_stop()

        self.assertTrue(home["status"]["homed"])
        self.assertEqual(move["status"]["currentAngleDeg"], 90.0)
        self.assertEqual(relative["status"]["currentAngleDeg"], 120.0)
        self.assertTrue(stopped["result"]["stopped"])
        self.assertIn(("move_sample_stage_to_angle", 90.0, "CW"), stage.calls)
        self.assertFalse(any(call[0] == "wheel_home" for call in getattr(manager.controller, "calls", [])))

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
