from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from backend_server import SessionState
from camera_service import CameraFrame
from capture_coordinator import (
    CaptureCoordinator,
    CaptureCoordinatorError,
    CaptureState,
    CaptureStepPlan,
    MultispectralBandPlan,
    MultispectralCapturePlan,
    validate_calibration_compatibility,
)
from hardware_controller import DoorState, OutputStatus


class FakeCameraManager:
    def __init__(
        self,
        frame=None,
        *,
        fail_capture: Exception | None = None,
        capture_metadata=None,
        multispectral_frame=None,
        fail_multispectral_capture: Exception | None = None,
        multispectral_capture_metadata=None,
    ):
        self.frame = frame if frame is not None else CameraFrame(
            data=np.zeros((4, 5, 3), dtype=np.uint8),
            color_space="RGB",
            dtype="uint8",
            shape=(4, 5, 3),
            metadata={"sourceColorSpace": "BGR", "deviceIndex": 1},
        )
        self.multispectral_frame = multispectral_frame if multispectral_frame is not None else CameraFrame(
            data=np.zeros((4, 5), dtype=np.uint8),
            color_space="MONO",
            dtype="uint8",
            shape=(4, 5),
            metadata={
                "pixelFormat": "Mono8",
                "exposure": 10000.0,
                "gain": 1.0,
                "frameId": 7,
            },
        )
        self.fail_capture = fail_capture
        self.fail_multispectral_capture = fail_multispectral_capture
        self.capture_metadata = capture_metadata or {
            "previewWasRunning": False,
            "openedForCapture": True,
            "device": {"deviceIndex": 1, "transport": "UVC/DirectShow", "backend": "opencv"},
            "requestedSettings": {"deviceIndex": 1, "width": 3840, "height": 2160, "fps": 25, "fourcc": "MJPG"},
            "actualSettings": {"width": 3840, "height": 2160, "fps": 25.0, "fourcc": "MJPG"},
            "status": {"available": True, "streaming": False},
        }
        self.multispectral_capture_metadata = multispectral_capture_metadata or {
            "previewWasRunning": False,
            "openedForCapture": True,
            "pixelFormat": "Mono8",
            "dtype": "uint8",
            "shape": (4, 5),
            "width": 5,
            "height": 4,
            "exposure": 10000.0,
            "gain": 1.0,
            "streaming": True,
            "device": {
                "model": "MGV231M-H2",
                "serial": "DSGP23400004963",
                "userId": "GP23400004963",
                "ip": "169.254.25.110",
                "mac": "B4-61-D3-14-6E-18",
                "transport": "GigE/DVP2",
                "backend": "dvp2",
            },
            "requestedSettings": {"serialNumber": "GP23400004963", "transport": "GigE/RJ45 Ethernet", "sdk": "DVP2"},
            "actualSettings": {
                "model": "MGV231M-H2",
                "cameraSerial": "DSGP23400004963",
                "userId": "GP23400004963",
                "cameraIp": "169.254.25.110",
                "cameraMac": "B4-61-D3-14-6E-18",
                "width": 5,
                "height": 4,
                "pixelFormat": "Mono8",
                "frameDtype": "uint8",
                "exposure": 10000.0,
                "gain": 1.0,
            },
            "status": {"available": True, "streaming": True},
        }
        self.capture_count = 0
        self.multispectral_capture_count = 0
        self.multispectral_settings_payloads = []

    def status(self):
        return {
            "rgb": {"available": True, "streaming": False},
            "multispectral": {"available": True, "streaming": False},
        }

    def capture_rgb_frame(self):
        self.capture_count += 1
        if self.fail_capture is not None:
            raise self.fail_capture
        return self.frame, dict(self.capture_metadata)

    def capture_multispectral_frame(self):
        self.multispectral_capture_count += 1
        if self.fail_multispectral_capture is not None:
            raise self.fail_multispectral_capture
        return self.multispectral_frame, dict(self.multispectral_capture_metadata)

    def apply_multispectral_settings(self, payload):
        self.multispectral_settings_payloads.append(dict(payload))
        actual = dict(self.multispectral_capture_metadata.get("actualSettings") or {})
        setting_results = {}
        if "exposure" in payload:
            actual["exposure"] = float(payload["exposure"])
            setting_results["exposure"] = {"requested": float(payload["exposure"]), "actual": actual["exposure"], "accepted": True}
        if "gain" in payload:
            actual["gain"] = float(payload["gain"])
            setting_results["gain"] = {"requested": float(payload["gain"]), "actual": actual["gain"], "accepted": True}
        self.multispectral_capture_metadata["actualSettings"] = actual
        return {"settingResults": setting_results, "status": {"actual": actual}, "summary": {}}


class RaisingFocusEvaluator:
    def evaluate(self, *args, **kwargs):
        raise RuntimeError("focus metric failed")


class FakeSafeStop:
    def __init__(self):
        self.count = 0

    def __call__(self):
        self.count += 1


class DirectSerialTrap:
    def __init__(self):
        self.send_command_count = 0

    def send_command(self, *args, **kwargs):
        self.send_command_count += 1
        raise AssertionError("CaptureCoordinator must not send serial commands directly")


class FakeHardwareController:
    def __init__(self):
        self.calls = []
        self.serial = DirectSerialTrap()
        self.door_state = DoorState.CLOSED
        self.fan_on_state = False
        self.rgb_mask = 0x00
        self.tungsten_mask = 0x00
        self.fault_code = 0x00
        self.fail_on = set()
        self.safe_stop_count = 0
        self.wheel_position = 0x7F
        self.wheel_home_count = 0
        self.wheel_move_count = 0

    def _record(self, name, *args):
        self.calls.append((name, *args))
        if name in self.fail_on:
            raise RuntimeError(f"{name} failed")

    def ping(self):
        self._record("ping")
        return True

    def get_error_status(self):
        self._record("get_error_status")
        return self.fault_code

    def door_close(self):
        self._record("door_close")
        if self.door_state == DoorState.OPEN:
            self.door_state = DoorState.CLOSED

    def get_door_status(self):
        self._record("get_door_status")
        return self.door_state

    def fan_on(self):
        self._record("fan_on")
        self.fan_on_state = True

    def get_output_status(self):
        self._record("get_output_status")
        return OutputStatus(
            raw=0,
            fan_on=self.fan_on_state,
            rgb_led_1_on=bool(self.rgb_mask & 0x01),
            rgb_led_2_on=bool(self.rgb_mask & 0x02),
            tungsten_1_on=bool(self.tungsten_mask & 0x01),
            tungsten_2_on=bool(self.tungsten_mask & 0x02),
        )

    def rgb_led_set(self, mask):
        self._record("rgb_led_set", mask)
        self.rgb_mask = mask

    def tungsten_set(self, mask):
        self._record("tungsten_set", mask)
        self.tungsten_mask = mask

    def wheel_home(self):
        self._record("wheel_home")
        self.wheel_home_count += 1
        self.wheel_position = 0

    def wheel_move_relative(self, steps):
        self._record("wheel_move_relative", steps)
        self.wheel_move_count += 1
        self.wheel_position += int(steps)

    def get_wheel_status(self):
        self._record("get_wheel_status")
        return self.wheel_position

    def ensure_rgb_capture_ready(self):
        self._record("ensure_rgb_capture_ready")
        if not self.fan_on_state or self.rgb_mask == 0x00 or self.tungsten_mask != 0x00:
            raise RuntimeError("RGB interlock failed")

    def ensure_multispectral_capture_ready(self):
        self._record("ensure_multispectral_capture_ready")
        if not self.fan_on_state or self.rgb_mask != 0x00 or self.tungsten_mask == 0x00:
            raise RuntimeError("multispectral interlock failed")

    def safe_stop(self):
        self._record("safe_stop")
        self.safe_stop_count += 1
        self.rgb_mask = 0x00
        self.tungsten_mask = 0x00


class CaptureCoordinatorTests(unittest.TestCase):
    def make_clock(self):
        current = {"value": 100.0}

        def tick():
            current["value"] += 0.01
            return current["value"]

        return tick

    def test_initial_state_is_idle(self):
        coordinator = CaptureCoordinator(capture_id_factory=lambda: "cap-1")

        snapshot = coordinator.snapshot()

        self.assertEqual(snapshot["state"], "idle")
        self.assertEqual(snapshot["status"], "idle")
        self.assertEqual(snapshot["progress"], 0)
        self.assertIsNone(snapshot["error"])
        self.assertEqual(snapshot["steps"], [])
        json.dumps(snapshot)

    def test_dry_run_lifecycle_completes_and_writes_metadata_skeleton(self):
        with tempfile.TemporaryDirectory(prefix="capture_coordinator_") as tmp:
            coordinator = CaptureCoordinator(
                camera_manager=FakeCameraManager(),
                clock=self.make_clock(),
                capture_id_factory=lambda: "cap-2",
            )

            result = coordinator.run_dry_run(sample_id="S001", output_dir=tmp)

            self.assertEqual(result["captureId"], "cap-2")
            self.assertEqual(result["sampleId"], "S001")
            self.assertEqual(result["state"], "completed")
            self.assertEqual(result["progress"], 100)
            self.assertIsNone(result["error"])
            self.assertEqual([step["status"] for step in result["steps"]], ["completed"] * 5)
            metadata_path = Path(tmp) / "capture_metadata_skeleton.json"
            self.assertTrue(metadata_path.exists())
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            self.assertEqual(metadata["capture_id"], "cap-2")
            self.assertEqual(metadata["sample_id"], "S001")
            self.assertEqual(metadata["state"], "completed")
            self.assertEqual(metadata["camera_settings"]["rgb"]["available"], True)

    def test_step_exception_fails_and_calls_safe_stop(self):
        stopper = FakeSafeStop()

        def fail():
            raise RuntimeError("boom")

        coordinator = CaptureCoordinator(
            safe_stop_callback=stopper,
            clock=self.make_clock(),
            capture_id_factory=lambda: "cap-3",
        )
        steps = [
            CaptureStepPlan("prepare", "prepare", CaptureState.PREPARING, action=None),
            CaptureStepPlan("rgb_capture", "rgb capture", CaptureState.CAPTURING, action=fail),
        ]

        result = coordinator.run_dry_run(sample_id="S002", steps=steps)

        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["error"]["step"], "rgb_capture")
        self.assertEqual(result["error"]["code"], "step_error")
        self.assertEqual(stopper.count, 1)
        self.assertEqual(result["steps"][1]["status"], "failed")

    def test_cancel_between_steps_calls_safe_stop(self):
        stopper = FakeSafeStop()
        coordinator = CaptureCoordinator(
            safe_stop_callback=stopper,
            clock=self.make_clock(),
            capture_id_factory=lambda: "cap-4",
        )

        def cancel():
            coordinator.request_cancel()

        steps = [
            CaptureStepPlan("prepare", "prepare", CaptureState.PREPARING, action=cancel),
            CaptureStepPlan("rgb_capture", "rgb capture", CaptureState.CAPTURING, action=None),
        ]

        result = coordinator.run_dry_run(sample_id="S003", steps=steps)

        self.assertEqual(result["state"], "cancelled")
        self.assertTrue(result["cancelRequested"])
        self.assertEqual(stopper.count, 1)
        self.assertEqual(result["steps"][0]["status"], "cancelled")
        self.assertEqual(result["steps"][1]["status"], "skipped")

    def test_step_timeout_fails_with_timeout_metadata(self):
        times = iter([10.0, 10.0, 10.2, 10.3, 10.4])
        coordinator = CaptureCoordinator(
            safe_stop_callback=FakeSafeStop(),
            clock=lambda: next(times),
            capture_id_factory=lambda: "cap-5",
        )
        steps = [CaptureStepPlan("filter_home", "filter home", CaptureState.CAPTURING, timeout_ms=50)]

        result = coordinator.run_dry_run(sample_id="S004", steps=steps)

        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["error"]["code"], "step_timeout")
        self.assertEqual(result["error"]["timeoutMs"], 50)
        self.assertGreater(result["error"]["durationMs"], 50)

    def test_snapshot_is_json_friendly(self):
        coordinator = CaptureCoordinator(capture_id_factory=lambda: "cap-json")
        result = coordinator.run_dry_run(sample_id="S005")

        json.dumps(result)

    def test_session_true_capture_prepared_remains_false(self):
        session = SessionState()

        result = session.update_device_preparation({
            "connect": True,
            "motor": True,
            "light": True,
            "camera": True,
            "calibration": True,
        })

        self.assertFalse(result["trueCapturePrepared"])
        self.assertFalse(session.snapshot()["trueCapturePrepared"])

    def test_preparation_runs_hardware_steps_in_order(self):
        hardware = FakeHardwareController()
        coordinator = CaptureCoordinator(
            hardware_controller=hardware,
            clock=self.make_clock(),
            capture_id_factory=lambda: "cap-prep-rgb",
        )

        result = coordinator.run_preparation(mode="rgb", sample_id="S006")

        self.assertEqual(result["state"], "completed")
        self.assertEqual(
            [step["id"] for step in result["steps"]],
            [
                "hardware_precheck",
                "door_close",
                "fan_on",
                "rgb_light_prepare",
                "capture_safety_check",
                "lighting_shutdown",
            ],
        )
        call_names = [call[0] for call in hardware.calls]
        self.assertLess(call_names.index("ping"), call_names.index("door_close"))
        self.assertLess(call_names.index("door_close"), call_names.index("fan_on"))
        self.assertLess(call_names.index("fan_on"), call_names.index("rgb_led_set"))
        self.assertEqual(result["steps"][1]["result"]["physicalDoorConfirmed"], True)
        self.assertEqual(result["steps"][2]["result"]["actualState"], True)

    def test_rgb_preparation_uses_rgb_lighting_safety_api(self):
        hardware = FakeHardwareController()
        coordinator = CaptureCoordinator(hardware_controller=hardware, capture_id_factory=lambda: "cap-rgb")

        result = coordinator.run_preparation(mode="rgb", rgb_led_mask=0x02)

        self.assertEqual(result["state"], "completed")
        self.assertIn(("tungsten_set", 0x00), hardware.calls)
        self.assertIn(("rgb_led_set", 0x02), hardware.calls)
        self.assertIn(("ensure_rgb_capture_ready",), hardware.calls)
        self.assertEqual(hardware.serial.send_command_count, 0)

    def test_multispectral_preparation_uses_multispectral_safety_api(self):
        hardware = FakeHardwareController()
        coordinator = CaptureCoordinator(hardware_controller=hardware, capture_id_factory=lambda: "cap-ms")

        result = coordinator.run_preparation(mode="multispectral", tungsten_mask=0x01)

        self.assertEqual(result["state"], "completed")
        self.assertIn(("rgb_led_set", 0x00), hardware.calls)
        self.assertIn(("tungsten_set", 0x01), hardware.calls)
        self.assertIn(("ensure_multispectral_capture_ready",), hardware.calls)
        self.assertEqual(hardware.serial.send_command_count, 0)

    def test_fan_failure_fails_and_calls_safe_stop_once(self):
        hardware = FakeHardwareController()
        hardware.fail_on.add("fan_on")
        coordinator = CaptureCoordinator(hardware_controller=hardware, capture_id_factory=lambda: "cap-fan-fail")

        result = coordinator.run_preparation(mode="rgb")

        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["error"]["step"], "fan_on")
        self.assertEqual(result["error"]["code"], "safety_error")
        self.assertEqual(hardware.safe_stop_count, 1)
        self.assertEqual(result["steps"][2]["status"], "failed")

    def test_lighting_failure_fails_and_calls_safe_stop_once(self):
        hardware = FakeHardwareController()
        hardware.fail_on.add("ensure_rgb_capture_ready")
        coordinator = CaptureCoordinator(hardware_controller=hardware, capture_id_factory=lambda: "cap-light-fail")

        result = coordinator.run_preparation(mode="rgb")

        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["error"]["step"], "rgb_light_prepare")
        self.assertEqual(result["error"]["code"], "safety_error")
        self.assertEqual(hardware.safe_stop_count, 1)

    def test_cancel_during_preparation_skips_remaining_steps(self):
        hardware = FakeHardwareController()
        coordinator = CaptureCoordinator(
            hardware_controller=hardware,
            clock=self.make_clock(),
            capture_id_factory=lambda: "cap-prep-cancel",
        )
        plans = coordinator.hardware_preparation_steps(mode="rgb")
        original_fan_action = plans[2].action

        def cancel_after_fan():
            result = original_fan_action()
            coordinator.request_cancel()
            return result

        plans[2] = CaptureStepPlan(
            plans[2].id,
            plans[2].name,
            plans[2].state,
            plans[2].timeout_ms,
            cancel_after_fan,
        )

        result = coordinator.run_preparation(mode="rgb", steps=plans)

        self.assertEqual(result["state"], "cancelled")
        self.assertEqual(hardware.safe_stop_count, 1)
        self.assertEqual(result["steps"][2]["status"], "cancelled")
        self.assertEqual(result["steps"][3]["status"], "skipped")
        self.assertNotIn(("rgb_led_set", 0x03), hardware.calls)

    def test_rgb_capture_saves_png_and_records_metadata(self):
        frame_data = np.zeros((3, 4, 3), dtype=np.uint8)
        frame_data[0, 0] = [255, 10, 20]
        camera = FakeCameraManager(frame=CameraFrame(
            data=frame_data,
            color_space="RGB",
            dtype="uint8",
            shape=frame_data.shape,
            metadata={"sourceColorSpace": "BGR", "deviceIndex": 1},
        ))
        hardware = FakeHardwareController()
        with tempfile.TemporaryDirectory(prefix="capture_rgb_") as tmp:
            coordinator = CaptureCoordinator(
                camera_manager=camera,
                hardware_controller=hardware,
                clock=self.make_clock(),
                capture_id_factory=lambda: "cap-rgb-save",
            )

            result = coordinator.run_rgb_capture(sample_id="S-RGB", output_dir=tmp)

            target = Path(tmp) / "rgb" / "rgb_view_000.png"
            self.assertEqual(result["state"], "completed")
            self.assertTrue(target.exists())
            self.assertGreater(target.stat().st_size, 0)
            self.assertEqual(camera.capture_count, 1)
            self.assertEqual(
                [step["id"] for step in result["steps"]],
                [
                    "hardware_precheck",
                    "door_close",
                    "fan_on",
                    "rgb_light_prepare",
                    "capture_safety_check",
                    "rgb_capture",
                    "lighting_shutdown",
                ],
            )
            frame_meta = result["metadata"]["frames"][0]
            self.assertEqual(frame_meta["relativePath"], "rgb/rgb_view_000.png")
            self.assertEqual(frame_meta["width"], 4)
            self.assertEqual(frame_meta["height"], 3)
            self.assertEqual(frame_meta["channels"], 3)
            self.assertEqual(frame_meta["dtype"], "uint8")
            self.assertEqual(frame_meta["pixelOrder"], "RGB")
            self.assertEqual(frame_meta["sourcePixelOrder"], "BGR")
            self.assertEqual(frame_meta["device"]["deviceIndex"], 1)

    def test_rgb_capture_rejects_empty_frame_and_safe_stops(self):
        camera = FakeCameraManager(frame=CameraFrame(
            data=np.zeros((0, 4, 3), dtype=np.uint8),
            color_space="RGB",
            dtype="uint8",
            shape=(0, 4, 3),
        ))
        hardware = FakeHardwareController()
        with tempfile.TemporaryDirectory(prefix="capture_rgb_empty_") as tmp:
            coordinator = CaptureCoordinator(
                camera_manager=camera,
                hardware_controller=hardware,
                capture_id_factory=lambda: "cap-rgb-empty",
            )

            result = coordinator.run_rgb_capture(sample_id="S-RGB", output_dir=tmp)

            self.assertEqual(result["state"], "failed")
            self.assertEqual(result["error"]["step"], "rgb_capture")
            self.assertEqual(result["error"]["code"], "rgb_frame_empty")
            self.assertEqual(hardware.safe_stop_count, 1)

    def test_rgb_capture_rejects_invalid_shape(self):
        camera = FakeCameraManager(frame=CameraFrame(
            data=np.zeros((4, 5), dtype=np.uint8),
            color_space="RGB",
            dtype="uint8",
            shape=(4, 5),
        ))
        hardware = FakeHardwareController()
        with tempfile.TemporaryDirectory(prefix="capture_rgb_bad_shape_") as tmp:
            coordinator = CaptureCoordinator(
                camera_manager=camera,
                hardware_controller=hardware,
                capture_id_factory=lambda: "cap-rgb-shape",
            )

            result = coordinator.run_rgb_capture(sample_id="S-RGB", output_dir=tmp)

            self.assertEqual(result["state"], "failed")
            self.assertEqual(result["error"]["code"], "rgb_frame_invalid")
            self.assertEqual(hardware.safe_stop_count, 1)
            self.assertFalse((Path(tmp) / "rgb" / "rgb_view_000.png").exists())

    def test_rgb_capture_write_failure_fails_without_fake_success(self):
        hardware = FakeHardwareController()
        with tempfile.TemporaryDirectory(prefix="capture_rgb_write_") as tmp:
            coordinator = CaptureCoordinator(
                camera_manager=FakeCameraManager(),
                hardware_controller=hardware,
                capture_id_factory=lambda: "cap-rgb-write",
            )

            def fail_write(data, target):
                raise CaptureCoordinatorError("disk full", step="rgb_capture", code="rgb_save_failed")

            coordinator._write_rgb_png = fail_write
            result = coordinator.run_rgb_capture(sample_id="S-RGB", output_dir=tmp)

            self.assertEqual(result["state"], "failed")
            self.assertEqual(result["error"]["code"], "rgb_save_failed")
            self.assertEqual(hardware.safe_stop_count, 1)
            self.assertEqual(result["metadata"]["frames"], [])

    def test_rgb_capture_refuses_to_overwrite_existing_file(self):
        hardware = FakeHardwareController()
        with tempfile.TemporaryDirectory(prefix="capture_rgb_collision_") as tmp:
            target = Path(tmp) / "rgb" / "rgb_view_000.png"
            target.parent.mkdir(parents=True)
            target.write_bytes(b"existing")
            coordinator = CaptureCoordinator(
                camera_manager=FakeCameraManager(),
                hardware_controller=hardware,
                capture_id_factory=lambda: "cap-rgb-collision",
            )

            result = coordinator.run_rgb_capture(sample_id="S-RGB", output_dir=tmp)

            self.assertEqual(result["state"], "failed")
            self.assertEqual(result["error"]["code"], "rgb_file_exists")
            self.assertEqual(target.read_bytes(), b"existing")
            self.assertEqual(hardware.safe_stop_count, 1)

    def test_rgb_capture_cancel_after_frame_before_save_does_not_write_file(self):
        hardware = FakeHardwareController()
        with tempfile.TemporaryDirectory(prefix="capture_rgb_cancel_") as tmp:
            coordinator = CaptureCoordinator(
                camera_manager=FakeCameraManager(),
                hardware_controller=hardware,
                capture_id_factory=lambda: "cap-rgb-cancel",
            )
            original_capture = coordinator.camera_manager.capture_rgb_frame

            def capture_then_cancel():
                result = original_capture()
                coordinator.request_cancel()
                return result

            coordinator.camera_manager.capture_rgb_frame = capture_then_cancel
            result = coordinator.run_rgb_capture(sample_id="S-RGB", output_dir=tmp)

            self.assertEqual(result["state"], "cancelled")
            self.assertEqual(hardware.safe_stop_count, 1)
            self.assertFalse((Path(tmp) / "rgb" / "rgb_view_000.png").exists())
            self.assertEqual(result["metadata"]["frames"], [])

    def test_multispectral_capture_saves_mono8_png_and_records_unassigned_metadata(self):
        frame_data = np.array([[0, 10, 20], [30, 40, 50]], dtype=np.uint8)
        camera = FakeCameraManager(multispectral_frame=CameraFrame(
            data=frame_data,
            color_space="MONO",
            dtype="uint8",
            shape=frame_data.shape,
            metadata={"pixelFormat": "Mono8", "exposure": 12000.0, "gain": 1.5, "frameId": 11},
        ))
        hardware = FakeHardwareController()
        with tempfile.TemporaryDirectory(prefix="capture_ms_u8_") as tmp:
            coordinator = CaptureCoordinator(
                camera_manager=camera,
                hardware_controller=hardware,
                clock=self.make_clock(),
                capture_id_factory=lambda: "cap-ms-u8",
            )

            result = coordinator.run_multispectral_capture(sample_id="S-MS", output_dir=tmp)

            target = Path(tmp) / "multispectral" / "multispectral_frame_000.png"
            self.assertEqual(result["state"], "completed")
            self.assertTrue(target.exists())
            self.assertEqual(camera.multispectral_capture_count, 1)
            self.assertEqual(
                [step["id"] for step in result["steps"]],
                [
                    "hardware_precheck",
                    "door_close",
                    "fan_on",
                    "multispectral_light_prepare",
                    "capture_safety_check",
                    "multispectral_capture",
                    "lighting_shutdown",
                ],
            )
            from PIL import Image

            with Image.open(target) as image:
                saved = np.asarray(image)
            self.assertEqual(saved.dtype, np.uint8)
            self.assertEqual(saved.shape, (2, 3))
            frame_meta = result["metadata"]["frames"][0]
            self.assertEqual(frame_meta["relativePath"], "multispectral/multispectral_frame_000.png")
            self.assertEqual(frame_meta["role"], "multispectral")
            self.assertEqual(frame_meta["width"], 3)
            self.assertEqual(frame_meta["height"], 2)
            self.assertEqual(frame_meta["channels"], 1)
            self.assertEqual(frame_meta["dtype"], "uint8")
            self.assertEqual(frame_meta["pixelFormat"], "Mono8")
            self.assertEqual(frame_meta["exposureUs"], 12000.0)
            self.assertEqual(frame_meta["gain"], 1.5)
            self.assertIsNone(frame_meta["wavelengthNm"])
            self.assertEqual(frame_meta["bandAssignment"], "unassigned")
            self.assertFalse(frame_meta["filterWheelSynchronized"])
            self.assertEqual(frame_meta["device"]["serial"], "DSGP23400004963")
            self.assertEqual(frame_meta["device"]["ip"], "169.254.25.110")

    def test_multispectral_capture_preserves_uint16_png_depth(self):
        frame_data = np.array([[0, 512, 65535], [1000, 4095, 32768]], dtype=np.uint16)
        camera = FakeCameraManager(
            multispectral_frame=CameraFrame(
                data=frame_data,
                color_space="MONO",
                dtype="uint16",
                shape=frame_data.shape,
                metadata={"pixelFormat": "Mono16", "exposure": 10000.0, "gain": 1.0},
            ),
            multispectral_capture_metadata={
                "previewWasRunning": True,
                "openedForCapture": False,
                "pixelFormat": "Mono16",
                "dtype": "uint16",
                "shape": frame_data.shape,
                "width": 3,
                "height": 2,
                "exposure": 10000.0,
                "gain": 1.0,
                "streaming": True,
                "device": {"serial": "DSGP23400004963", "transport": "GigE/DVP2"},
                "requestedSettings": {},
                "actualSettings": {"pixelFormat": "Mono16", "frameDtype": "uint16"},
                "status": {"streaming": True},
            },
        )
        hardware = FakeHardwareController()
        with tempfile.TemporaryDirectory(prefix="capture_ms_u16_") as tmp:
            coordinator = CaptureCoordinator(
                camera_manager=camera,
                hardware_controller=hardware,
                capture_id_factory=lambda: "cap-ms-u16",
            )

            result = coordinator.run_multispectral_capture(sample_id="S-MS", output_dir=tmp)

            target = Path(tmp) / "multispectral" / "multispectral_frame_000.png"
            from PIL import Image

            with Image.open(target) as image:
                saved = np.asarray(image)
            self.assertEqual(result["state"], "completed")
            self.assertEqual(saved.dtype, np.uint16)
            self.assertEqual(int(saved.max()), 65535)
            frame_meta = result["metadata"]["frames"][0]
            self.assertEqual(frame_meta["dtype"], "uint16")
            self.assertEqual(frame_meta["pixelFormat"], "Mono16")
            self.assertTrue(frame_meta["previewWasRunning"])
            self.assertFalse(frame_meta["openedForCapture"])

    def test_multispectral_capture_rejects_empty_frame_and_safe_stops(self):
        camera = FakeCameraManager(multispectral_frame=CameraFrame(
            data=np.zeros((0, 4), dtype=np.uint8),
            color_space="MONO",
            dtype="uint8",
            shape=(0, 4),
        ))
        hardware = FakeHardwareController()
        with tempfile.TemporaryDirectory(prefix="capture_ms_empty_") as tmp:
            coordinator = CaptureCoordinator(
                camera_manager=camera,
                hardware_controller=hardware,
                capture_id_factory=lambda: "cap-ms-empty",
            )

            result = coordinator.run_multispectral_capture(sample_id="S-MS", output_dir=tmp)

            self.assertEqual(result["state"], "failed")
            self.assertEqual(result["error"]["code"], "multispectral_frame_empty")
            self.assertEqual(hardware.safe_stop_count, 1)

    def test_multispectral_capture_rejects_unsupported_dtype_and_shape(self):
        cases = [
            (
                CameraFrame(
                    data=np.zeros((2, 3), dtype=np.float32),
                    color_space="MONO",
                    dtype="float32",
                    shape=(2, 3),
                ),
                "dtype",
            ),
            (
                CameraFrame(
                    data=np.zeros((2, 3, 3), dtype=np.uint8),
                    color_space="MONO",
                    dtype="uint8",
                    shape=(2, 3, 3),
                ),
                "shape",
            ),
            (
                CameraFrame(
                    data=np.zeros((2, 3), dtype=np.uint8),
                    color_space="RGB",
                    dtype="uint8",
                    shape=(2, 3),
                ),
                "color",
            ),
        ]
        for frame, label in cases:
            with self.subTest(label=label):
                hardware = FakeHardwareController()
                with tempfile.TemporaryDirectory(prefix="capture_ms_invalid_") as tmp:
                    coordinator = CaptureCoordinator(
                        camera_manager=FakeCameraManager(multispectral_frame=frame),
                        hardware_controller=hardware,
                        capture_id_factory=lambda: f"cap-ms-invalid-{label}",
                    )

                    result = coordinator.run_multispectral_capture(sample_id="S-MS", output_dir=tmp)

                    self.assertEqual(result["state"], "failed")
                    self.assertEqual(result["error"]["code"], "multispectral_frame_invalid")
                    self.assertEqual(hardware.safe_stop_count, 1)
                    self.assertFalse((Path(tmp) / "multispectral" / "multispectral_frame_000.png").exists())

    def test_multispectral_capture_refuses_to_overwrite_existing_file(self):
        hardware = FakeHardwareController()
        with tempfile.TemporaryDirectory(prefix="capture_ms_collision_") as tmp:
            target = Path(tmp) / "multispectral" / "multispectral_frame_000.png"
            target.parent.mkdir(parents=True)
            target.write_bytes(b"existing")
            coordinator = CaptureCoordinator(
                camera_manager=FakeCameraManager(),
                hardware_controller=hardware,
                capture_id_factory=lambda: "cap-ms-collision",
            )

            result = coordinator.run_multispectral_capture(sample_id="S-MS", output_dir=tmp)

            self.assertEqual(result["state"], "failed")
            self.assertEqual(result["error"]["code"], "multispectral_file_exists")
            self.assertEqual(target.read_bytes(), b"existing")
            self.assertEqual(hardware.safe_stop_count, 1)

    def test_multispectral_capture_failure_cancel_and_timeout_safe_stop(self):
        hardware = FakeHardwareController()
        with tempfile.TemporaryDirectory(prefix="capture_ms_fail_") as tmp:
            coordinator = CaptureCoordinator(
                camera_manager=FakeCameraManager(fail_multispectral_capture=RuntimeError("camera busy")),
                hardware_controller=hardware,
                capture_id_factory=lambda: "cap-ms-fail",
            )
            result = coordinator.run_multispectral_capture(sample_id="S-MS", output_dir=tmp)
            self.assertEqual(result["state"], "failed")
            self.assertEqual(result["error"]["code"], "multispectral_capture_failed")
            self.assertEqual(hardware.safe_stop_count, 1)

        hardware = FakeHardwareController()
        with tempfile.TemporaryDirectory(prefix="capture_ms_cancel_") as tmp:
            coordinator = CaptureCoordinator(
                camera_manager=FakeCameraManager(),
                hardware_controller=hardware,
                capture_id_factory=lambda: "cap-ms-cancel",
            )
            original_capture = coordinator.camera_manager.capture_multispectral_frame

            def capture_then_cancel():
                result = original_capture()
                coordinator.request_cancel()
                return result

            coordinator.camera_manager.capture_multispectral_frame = capture_then_cancel
            result = coordinator.run_multispectral_capture(sample_id="S-MS", output_dir=tmp)
            self.assertEqual(result["state"], "cancelled")
            self.assertEqual(hardware.safe_stop_count, 1)
            self.assertFalse((Path(tmp) / "multispectral" / "multispectral_frame_000.png").exists())

        hardware = FakeHardwareController()
        times = iter([10.0, 10.0, 21.0, 21.1])
        with tempfile.TemporaryDirectory(prefix="capture_ms_timeout_") as tmp:
            coordinator = CaptureCoordinator(
                camera_manager=FakeCameraManager(),
                hardware_controller=hardware,
                clock=lambda: next(times),
                capture_id_factory=lambda: "cap-ms-timeout",
            )
            result = coordinator.run_multispectral_capture(sample_id="S-MS", output_dir=tmp)
            self.assertEqual(result["state"], "failed")
            self.assertEqual(result["error"]["code"], "step_timeout")
            self.assertEqual(result["error"]["step"], "hardware_precheck")
            self.assertEqual(hardware.safe_stop_count, 1)

    def test_multispectral_sequence_moves_wheel_captures_enabled_bands_and_records_metadata(self):
        plan = MultispectralCapturePlan(
            bands=[
                MultispectralBandPlan("A520", 2, 520, exposure_us=11000.0, gain=1.1),
                MultispectralBandPlan("B610", 4, 610, exposure_us=12000.0, gain=1.2),
                MultispectralBandPlan("SKIP", 6, 700, enabled=False),
            ],
            filter_config_source="unit-test",
            filter_config_version="test-profile",
            development_config=False,
            settling_ms=17,
        )
        sleeps = []
        camera = FakeCameraManager()
        hardware = FakeHardwareController()
        with tempfile.TemporaryDirectory(prefix="capture_ms_sequence_") as tmp:
            coordinator = CaptureCoordinator(
                camera_manager=camera,
                hardware_controller=hardware,
                sleep_fn=lambda seconds: sleeps.append(seconds),
                capture_id_factory=lambda: "cap-ms-sequence",
            )

            result = coordinator.run_multispectral_sequence(sample_id="S-MS", output_dir=tmp, band_plan=plan)

            self.assertEqual(result["state"], "completed")
            self.assertEqual(camera.multispectral_capture_count, 2)
            self.assertEqual(camera.multispectral_settings_payloads, [
                {"exposure": 11000.0, "gain": 1.1},
                {"exposure": 12000.0, "gain": 1.2},
            ])
            self.assertEqual(sleeps, [0.017, 0.017])
            self.assertIn(("wheel_home",), hardware.calls)
            self.assertIn(("wheel_move_relative", 2), hardware.calls)
            self.assertIn(("wheel_move_relative", 2), hardware.calls)
            step_ids = [step["id"] for step in result["steps"]]
            self.assertIn("filter_wheel_move:A520", step_ids)
            self.assertIn("multispectral_capture:B610", step_ids)
            self.assertNotIn("multispectral_capture:SKIP", step_ids)
            self.assertTrue((Path(tmp) / "multispectral" / "band_01_A520.png").exists())
            self.assertTrue((Path(tmp) / "multispectral" / "band_02_B610.png").exists())
            sequence = result["metadata"]["multispectralSequence"]
            self.assertTrue(result["metadata"]["multispectralSequenceComplete"])
            self.assertEqual(sequence["status"], "completed")
            self.assertEqual(sequence["completedBands"], ["A520", "B610"])
            self.assertEqual(sequence["pendingBands"], [])
            self.assertEqual(sequence["disabledBandIds"], ["SKIP"])
            self.assertFalse(sequence["partialCapture"])
            frame_meta = result["metadata"]["frames"][0]
            self.assertEqual(frame_meta["bandId"], "A520")
            self.assertEqual(frame_meta["wavelengthNm"], 520)
            self.assertTrue(frame_meta["filterWheelSynchronized"])
            self.assertEqual(frame_meta["filterWheel"]["position"], 2)
            self.assertEqual(frame_meta["filterWheel"]["settlingMs"], 17)
            self.assertEqual(frame_meta["relativePath"], "multispectral/band_01_A520.png")
            self.assertEqual(frame_meta["focus"]["status"], "ok")
            self.assertEqual(frame_meta["focus"]["classification"], "unknown")
            self.assertEqual(frame_meta["focus"]["bandId"], "A520")
            self.assertEqual(frame_meta["focus"]["wavelengthNm"], 520)
            self.assertIn("tenengrad", frame_meta["focus"])

    def test_multispectral_sequence_preserves_uint16_band_png_depth(self):
        frame_data = np.array([[0, 65535], [4096, 1024]], dtype=np.uint16)
        camera = FakeCameraManager(multispectral_frame=CameraFrame(
            data=frame_data,
            color_space="MONO",
            dtype="uint16",
            shape=frame_data.shape,
            metadata={"pixelFormat": "Mono16", "exposure": 10000.0, "gain": 1.0},
        ))
        plan = [MultispectralBandPlan("NIR", 1, None, bandwidth_nm=20)]
        with tempfile.TemporaryDirectory(prefix="capture_ms_sequence_u16_") as tmp:
            coordinator = CaptureCoordinator(
                camera_manager=camera,
                hardware_controller=FakeHardwareController(),
                sleep_fn=lambda seconds: None,
                capture_id_factory=lambda: "cap-ms-sequence-u16",
            )

            result = coordinator.run_multispectral_sequence(sample_id="S-MS", output_dir=tmp, band_plan=plan, settling_ms=0)

            from PIL import Image

            with Image.open(Path(tmp) / "multispectral" / "band_01_NIR.png") as image:
                saved = np.asarray(image)
            self.assertEqual(result["state"], "completed")
            self.assertEqual(saved.dtype, np.uint16)
            self.assertEqual(int(saved.max()), 65535)
            frame_meta = result["metadata"]["frames"][0]
            self.assertIsNone(frame_meta["wavelengthNm"])
            self.assertEqual(frame_meta["bandAssignment"], "NIR")

    def test_multispectral_sequence_reads_band_plan_from_filter_config_file(self):
        config = {
            "profile": "unit_profile",
            "filters": [
                {"filter_position": 3, "wavelength_nm": 515, "bandwidth_nm": 12, "exposure_ms": 7.5, "gain": 1.7, "enabled": True},
                {"filter_position": 5, "wavelength_nm": 735, "bandwidth_nm": 20, "enabled": False},
            ],
        }
        with tempfile.TemporaryDirectory(prefix="capture_ms_filter_config_") as tmp:
            config_path = Path(tmp) / "unit_filter_config.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            coordinator = CaptureCoordinator(
                camera_manager=FakeCameraManager(),
                hardware_controller=FakeHardwareController(),
                sleep_fn=lambda seconds: None,
                capture_id_factory=lambda: "cap-ms-config",
            )

            result = coordinator.run_multispectral_sequence(
                sample_id="S-MS",
                output_dir=Path(tmp) / "out",
                filter_config_path=config_path,
                settling_ms=0,
            )

            sequence = result["metadata"]["multispectralSequence"]
            self.assertEqual(result["state"], "completed")
            self.assertEqual(sequence["filterConfigSource"], str(config_path))
            self.assertEqual(sequence["filterConfigVersion"], "unit_profile")
            self.assertFalse(sequence["developmentConfig"])
            self.assertEqual(sequence["enabledBandIds"], ["R515"])
            self.assertEqual(sequence["disabledBandIds"], ["R735"])
            frame_meta = result["metadata"]["frames"][0]
            self.assertEqual(frame_meta["bandId"], "R515")
            self.assertEqual(frame_meta["wavelengthNm"], 515)
            self.assertEqual(frame_meta["bandwidthNm"], 12.0)

    def test_multispectral_sequence_home_unknown_and_position_mismatch_fail_safely(self):
        plan = [MultispectralBandPlan("A", 1, 510)]
        with tempfile.TemporaryDirectory(prefix="capture_ms_home_unknown_") as tmp:
            hardware = FakeHardwareController()

            def home_without_position():
                hardware._record("wheel_home")
                hardware.wheel_home_count += 1
                hardware.wheel_position = 0x7F

            hardware.wheel_home = home_without_position
            coordinator = CaptureCoordinator(
                camera_manager=FakeCameraManager(),
                hardware_controller=hardware,
                sleep_fn=lambda seconds: None,
                capture_id_factory=lambda: "cap-ms-home-unknown",
            )
            result = coordinator.run_multispectral_sequence(sample_id="S-MS", output_dir=tmp, band_plan=plan)
            self.assertEqual(result["state"], "failed")
            self.assertEqual(result["error"]["code"], "filter_wheel_position_unknown")
            self.assertEqual(hardware.safe_stop_count, 1)

        with tempfile.TemporaryDirectory(prefix="capture_ms_mismatch_") as tmp:
            hardware = FakeHardwareController()

            def move_without_motion(steps):
                hardware._record("wheel_move_relative", steps)
                hardware.wheel_move_count += 1

            hardware.wheel_move_relative = move_without_motion
            coordinator = CaptureCoordinator(
                camera_manager=FakeCameraManager(),
                hardware_controller=hardware,
                sleep_fn=lambda seconds: None,
                capture_id_factory=lambda: "cap-ms-mismatch",
            )
            result = coordinator.run_multispectral_sequence(sample_id="S-MS", output_dir=tmp, band_plan=plan)
            self.assertEqual(result["state"], "failed")
            self.assertEqual(result["error"]["code"], "filter_wheel_position_mismatch")
            self.assertEqual(result["metadata"]["multispectralSequence"]["failedBand"], "A")
            self.assertEqual(hardware.safe_stop_count, 1)

    def test_multispectral_sequence_second_band_failure_stops_pending_and_keeps_partial_data(self):
        plan = [
            MultispectralBandPlan("FIRST", 1, 510),
            MultispectralBandPlan("SECOND", 2, 620),
            MultispectralBandPlan("THIRD", 3, 730),
        ]
        camera = FakeCameraManager()

        def fail_second_capture():
            camera.multispectral_capture_count += 1
            if camera.multispectral_capture_count == 2:
                raise RuntimeError("camera busy")
            return camera.multispectral_frame, dict(camera.multispectral_capture_metadata)

        camera.capture_multispectral_frame = fail_second_capture
        hardware = FakeHardwareController()
        with tempfile.TemporaryDirectory(prefix="capture_ms_partial_") as tmp:
            coordinator = CaptureCoordinator(
                camera_manager=camera,
                hardware_controller=hardware,
                sleep_fn=lambda seconds: None,
                capture_id_factory=lambda: "cap-ms-partial",
            )

            result = coordinator.run_multispectral_sequence(sample_id="S-MS", output_dir=tmp, band_plan=plan)

            self.assertEqual(result["state"], "failed")
            self.assertEqual(camera.multispectral_capture_count, 2)
            self.assertTrue((Path(tmp) / "multispectral" / "band_01_FIRST.png").exists())
            self.assertFalse((Path(tmp) / "multispectral" / "band_02_SECOND.png").exists())
            self.assertFalse((Path(tmp) / "multispectral" / "band_03_THIRD.png").exists())
            sequence = result["metadata"]["multispectralSequence"]
            self.assertFalse(result["metadata"]["multispectralSequenceComplete"])
            self.assertTrue(sequence["partialCapture"])
            self.assertEqual(sequence["completedBands"], ["FIRST"])
            self.assertEqual(sequence["failedBand"], "SECOND")
            self.assertEqual(sequence["pendingBands"], ["THIRD"])
            self.assertEqual(hardware.safe_stop_count, 1)

    def test_multispectral_sequence_cancel_and_settle_timeout_call_safe_stop(self):
        plan = [
            MultispectralBandPlan("FIRST", 1, 510),
            MultispectralBandPlan("SECOND", 2, 620),
        ]
        camera = FakeCameraManager()
        hardware = FakeHardwareController()
        with tempfile.TemporaryDirectory(prefix="capture_ms_cancel_") as tmp:
            sleep_calls = {"count": 0}
            coordinator = CaptureCoordinator(
                camera_manager=camera,
                hardware_controller=hardware,
                sleep_fn=lambda seconds: (
                    sleep_calls.__setitem__("count", sleep_calls["count"] + 1),
                    coordinator.request_cancel() if sleep_calls["count"] == 2 else None,
                ),
                capture_id_factory=lambda: "cap-ms-sequence-cancel",
            )
            result = coordinator.run_multispectral_sequence(sample_id="S-MS", output_dir=tmp, band_plan=plan)
            self.assertEqual(result["state"], "cancelled")
            self.assertEqual(camera.multispectral_capture_count, 1)
            self.assertTrue((Path(tmp) / "multispectral" / "band_01_FIRST.png").exists())
            sequence = result["metadata"]["multispectralSequence"]
            self.assertTrue(sequence["cancelled"])
            self.assertTrue(sequence["partialCapture"])
            self.assertEqual(hardware.safe_stop_count, 1)

        old_timeout = CaptureCoordinator.DEFAULT_STEP_TIMEOUTS_MS["filter_wheel_settle"]
        CaptureCoordinator.DEFAULT_STEP_TIMEOUTS_MS["filter_wheel_settle"] = 0
        try:
            hardware = FakeHardwareController()
            with tempfile.TemporaryDirectory(prefix="capture_ms_timeout_") as tmp:
                coordinator = CaptureCoordinator(
                    camera_manager=FakeCameraManager(),
                    hardware_controller=hardware,
                    clock=self.make_clock(),
                    sleep_fn=lambda seconds: None,
                    capture_id_factory=lambda: "cap-ms-sequence-timeout",
                )
                result = coordinator.run_multispectral_sequence(sample_id="S-MS", output_dir=tmp, band_plan=[plan[0]])
                self.assertEqual(result["state"], "failed")
                self.assertEqual(result["error"]["code"], "step_timeout")
                self.assertEqual(result["error"]["step"], "filter_wheel_settle:FIRST")
                self.assertEqual(hardware.safe_stop_count, 1)
        finally:
            CaptureCoordinator.DEFAULT_STEP_TIMEOUTS_MS["filter_wheel_settle"] = old_timeout

    def test_multispectral_focus_failure_records_metadata_without_deleting_raw_frame(self):
        camera = FakeCameraManager()
        camera.focus_evaluator = RaisingFocusEvaluator()
        plan = [MultispectralBandPlan("A520", 2, 520)]
        with tempfile.TemporaryDirectory(prefix="capture_ms_focus_fail_") as tmp:
            coordinator = CaptureCoordinator(
                camera_manager=camera,
                hardware_controller=FakeHardwareController(),
                sleep_fn=lambda seconds: None,
                capture_id_factory=lambda: "cap-ms-focus-fail",
            )

            result = coordinator.run_multispectral_sequence(sample_id="S-MS", output_dir=tmp, band_plan=plan, settling_ms=0)

            target = Path(tmp) / "multispectral" / "band_01_A520.png"
            self.assertEqual(result["state"], "completed")
            self.assertTrue(target.exists())
            frame_meta = result["metadata"]["frames"][0]
            self.assertEqual(frame_meta["focus"]["status"], "evaluation_failed")
            self.assertEqual(frame_meta["focus"]["classification"], "unknown")
            self.assertIsNone(frame_meta["focus"]["score"])
            self.assertEqual(frame_meta["bandId"], "A520")
            self.assertEqual(result["metadata"]["multispectralSequence"]["completedBands"], ["A520"])

    def test_dark_reference_capture_saves_raw_bands_lights_off_and_records_calibration_set(self):
        plan = MultispectralCapturePlan(
            bands=[
                MultispectralBandPlan("A520", 2, 520, exposure_us=11000.0, gain=1.1),
                MultispectralBandPlan("B610", 4, 610, exposure_us=12000.0, gain=1.2),
            ],
            filter_config_source="unit-test",
            filter_config_version="test-profile",
            development_config=False,
            settling_ms=0,
        )
        frame_data = np.array([[1, 2, 3], [4, 5, 6]], dtype=np.uint8)
        camera = FakeCameraManager(multispectral_frame=CameraFrame(
            data=frame_data,
            color_space="MONO",
            dtype="uint8",
            shape=frame_data.shape,
            metadata={"pixelFormat": "Mono8", "exposure": 11000.0, "gain": 1.1, "frameId": 101},
        ))
        hardware = FakeHardwareController()
        with tempfile.TemporaryDirectory(prefix="capture_dark_reference_") as tmp:
            coordinator = CaptureCoordinator(
                camera_manager=camera,
                hardware_controller=hardware,
                sleep_fn=lambda seconds: None,
                capture_id_factory=lambda: "cap-dark-ref",
            )

            result = coordinator.run_dark_reference_capture(
                sample_id="S-CAL",
                output_dir=tmp,
                band_plan=plan,
                calibration_id="cal-unit",
                operator_confirmed=True,
            )

            self.assertEqual(result["state"], "completed")
            self.assertEqual(result["metadata"]["captureType"], "dark")
            self.assertNotIn("multispectral_light_prepare", [step["id"] for step in result["steps"]])
            self.assertIn("dark_lighting_shutdown", [step["id"] for step in result["steps"]])
            self.assertIn("lighting_off_verify", [step["id"] for step in result["steps"]])
            self.assertIn(("tungsten_set", 0x00), hardware.calls)
            self.assertIn(("rgb_led_set", 0x00), hardware.calls)
            self.assertEqual(camera.multispectral_settings_payloads, [
                {"exposure": 11000.0, "gain": 1.1},
                {"exposure": 12000.0, "gain": 1.2},
            ])
            self.assertTrue((Path(tmp) / "calibration" / "dark" / "band_01_A520.png").exists())
            self.assertTrue((Path(tmp) / "calibration" / "dark" / "band_02_B610.png").exists())
            frame_meta = result["metadata"]["frames"][0]
            self.assertEqual(frame_meta["captureType"], "dark")
            self.assertEqual(frame_meta["relativePath"], "calibration/dark/band_01_A520.png")
            self.assertEqual(frame_meta["frameStats"]["std"], float(np.std(frame_data)))
            self.assertEqual(frame_meta["darkQuality"]["status"], "unvalidated")
            self.assertNotIn("focus", frame_meta)
            calibration = result["metadata"]["calibrationSet"]
            self.assertEqual(calibration["calibrationId"], "cal-unit")
            self.assertFalse(calibration["calibrationComplete"])
            self.assertEqual(calibration["completedDarkBands"], ["A520", "B610"])
            self.assertEqual(calibration["missingWhiteBands"], ["A520", "B610"])
            self.assertTrue((Path(tmp) / "calibration" / "calibration_set_cal-unit.json").exists())

    def test_white_reference_capture_uses_multispectral_lighting_and_completes_existing_set(self):
        plan = MultispectralCapturePlan(
            bands=[
                MultispectralBandPlan("A520", 2, 520, exposure_us=11000.0, gain=1.1),
                MultispectralBandPlan("B610", 4, 610, exposure_us=12000.0, gain=1.2),
            ],
            filter_config_source="unit-test",
            filter_config_version="test-profile",
            development_config=False,
            settling_ms=0,
        )
        with tempfile.TemporaryDirectory(prefix="capture_white_reference_") as tmp:
            coordinator = CaptureCoordinator(
                camera_manager=FakeCameraManager(),
                hardware_controller=FakeHardwareController(),
                sleep_fn=lambda seconds: None,
                capture_id_factory=lambda: "cap-dark-for-white",
            )
            dark = coordinator.run_dark_reference_capture(
                sample_id="S-CAL",
                output_dir=tmp,
                band_plan=plan,
                calibration_id="cal-complete",
                operator_confirmed=True,
            )
            self.assertEqual(dark["state"], "completed")

            white_data = np.array([[200, 201, 202], [203, 204, 205]], dtype=np.uint8)
            camera = FakeCameraManager(multispectral_frame=CameraFrame(
                data=white_data,
                color_space="MONO",
                dtype="uint8",
                shape=white_data.shape,
                metadata={"pixelFormat": "Mono8", "exposure": 11000.0, "gain": 1.1},
            ))
            hardware = FakeHardwareController()
            coordinator = CaptureCoordinator(
                camera_manager=camera,
                hardware_controller=hardware,
                sleep_fn=lambda seconds: None,
                capture_id_factory=lambda: "cap-white-ref",
            )

            result = coordinator.run_white_reference_capture(
                sample_id="S-CAL",
                output_dir=tmp,
                band_plan=plan,
                calibration_id="cal-complete",
                operator_confirmed=True,
                tungsten_mask=0x01,
            )

            self.assertEqual(result["state"], "completed")
            self.assertIn(("rgb_led_set", 0x00), hardware.calls)
            self.assertIn(("tungsten_set", 0x01), hardware.calls)
            self.assertIn(("ensure_multispectral_capture_ready",), hardware.calls)
            self.assertTrue((Path(tmp) / "calibration" / "white" / "band_01_A520.png").exists())
            self.assertTrue((Path(tmp) / "calibration" / "white" / "band_02_B610.png").exists())
            frame_meta = result["metadata"]["frames"][0]
            self.assertEqual(frame_meta["captureType"], "white")
            self.assertEqual(frame_meta["relativePath"], "calibration/white/band_01_A520.png")
            self.assertEqual(frame_meta["saturationDiagnostics"]["saturationValue"], 255)
            self.assertEqual(frame_meta["saturationDiagnostics"]["bitDepthStatus"], "unknown")
            self.assertEqual(frame_meta["whiteUniformity"]["status"], "unvalidated")
            calibration = result["metadata"]["calibrationSet"]
            self.assertTrue(calibration["calibrationComplete"])
            self.assertEqual(calibration["completedDarkBands"], ["A520", "B610"])
            self.assertEqual(calibration["completedWhiteBands"], ["A520", "B610"])
            self.assertFalse(calibration["missingDarkBands"])
            self.assertFalse(calibration["missingWhiteBands"])
            self.assertTrue(calibration["sameBandSettingsMatched"])

    def test_calibration_reference_uint16_depth_and_saturation_bits_are_preserved(self):
        frame_data = np.array([[0, 4095], [2048, 4095]], dtype=np.uint16)
        camera = FakeCameraManager(multispectral_frame=CameraFrame(
            data=frame_data,
            color_space="MONO",
            dtype="uint16",
            shape=frame_data.shape,
            metadata={"pixelFormat": "Mono16", "bits": 12, "exposure": 10000.0, "gain": 1.0},
        ))
        with tempfile.TemporaryDirectory(prefix="capture_white_u16_") as tmp:
            coordinator = CaptureCoordinator(
                camera_manager=camera,
                hardware_controller=FakeHardwareController(),
                sleep_fn=lambda seconds: None,
                capture_id_factory=lambda: "cap-white-u16",
            )

            result = coordinator.run_white_reference_capture(
                sample_id="S-CAL",
                output_dir=tmp,
                band_plan=[MultispectralBandPlan("NIR", 1, 850, exposure_us=10000.0, gain=1.0)],
                calibration_id="cal-u16",
                operator_confirmed=True,
            )

            from PIL import Image

            target = Path(tmp) / "calibration" / "white" / "band_01_NIR.png"
            with Image.open(target) as image:
                saved = np.asarray(image)
            self.assertEqual(result["state"], "completed")
            self.assertEqual(saved.dtype, np.uint16)
            self.assertEqual(int(saved.max()), 4095)
            saturation = result["metadata"]["frames"][0]["saturationDiagnostics"]
            self.assertEqual(saturation["bitDepth"], 12)
            self.assertEqual(saturation["saturationValue"], 4095)
            self.assertEqual(saturation["saturatedPixelCount"], 2)
            self.assertEqual(saturation["saturatedPixelRatio"], 0.5)

        dark_data = np.array([[0, 10], [20, 30]], dtype=np.uint16)
        camera = FakeCameraManager(multispectral_frame=CameraFrame(
            data=dark_data,
            color_space="MONO",
            dtype="uint16",
            shape=dark_data.shape,
            metadata={"pixelFormat": "Mono16", "bits": 12, "exposure": 10000.0, "gain": 1.0},
        ))
        with tempfile.TemporaryDirectory(prefix="capture_dark_u16_") as tmp:
            coordinator = CaptureCoordinator(
                camera_manager=camera,
                hardware_controller=FakeHardwareController(),
                sleep_fn=lambda seconds: None,
                capture_id_factory=lambda: "cap-dark-u16",
            )
            result = coordinator.run_dark_reference_capture(
                sample_id="S-CAL",
                output_dir=tmp,
                band_plan=[MultispectralBandPlan("NIR", 1, 850, exposure_us=10000.0, gain=1.0)],
                calibration_id="cal-dark-u16",
                operator_confirmed=True,
            )
            target = Path(tmp) / "calibration" / "dark" / "band_01_NIR.png"
            with Image.open(target) as image:
                saved = np.asarray(image)
            self.assertEqual(result["state"], "completed")
            self.assertEqual(saved.dtype, np.uint16)
            self.assertEqual(result["metadata"]["frames"][0]["darkQuality"]["max"], 30.0)

    def test_reference_capture_requires_operator_confirmation_without_blocking_tests(self):
        hardware = FakeHardwareController()
        with tempfile.TemporaryDirectory(prefix="capture_ref_confirm_") as tmp:
            coordinator = CaptureCoordinator(
                camera_manager=FakeCameraManager(),
                hardware_controller=hardware,
                sleep_fn=lambda seconds: None,
                capture_id_factory=lambda: "cap-dark-confirm",
            )

            result = coordinator.run_dark_reference_capture(
                sample_id="S-CAL",
                output_dir=tmp,
                band_plan=[MultispectralBandPlan("A", 1, 510)],
                calibration_id="cal-confirm",
                operator_confirmed=False,
            )

            self.assertEqual(result["state"], "failed")
            self.assertEqual(result["error"]["code"], "operator_confirmation_required")
            self.assertEqual(result["error"]["step"], "operator_confirmation:dark")
            self.assertEqual(hardware.safe_stop_count, 1)
            self.assertEqual(result["metadata"]["frames"], [])
            self.assertFalse((Path(tmp) / "calibration" / "dark" / "band_01_A.png").exists())

        with tempfile.TemporaryDirectory(prefix="capture_ref_callback_") as tmp:
            coordinator = CaptureCoordinator(
                camera_manager=FakeCameraManager(),
                hardware_controller=FakeHardwareController(),
                sleep_fn=lambda seconds: None,
                capture_id_factory=lambda: "cap-white-confirm",
            )
            result = coordinator.run_white_reference_capture(
                sample_id="S-CAL",
                output_dir=tmp,
                band_plan=[MultispectralBandPlan("A", 1, 510)],
                calibration_id="cal-callback",
                confirmation_callback=lambda capture_type, context: capture_type == "white" and context["blockingUiState"] == "waiting_for_white_reference",
            )
            self.assertEqual(result["state"], "completed")

    def test_reference_partial_failure_keeps_saved_frame_stops_pending_and_safe_stops(self):
        plan = [
            MultispectralBandPlan("FIRST", 1, 510),
            MultispectralBandPlan("SECOND", 2, 620),
            MultispectralBandPlan("THIRD", 3, 730),
        ]
        camera = FakeCameraManager()

        def fail_second_capture():
            camera.multispectral_capture_count += 1
            if camera.multispectral_capture_count == 2:
                raise RuntimeError("camera busy")
            return camera.multispectral_frame, dict(camera.multispectral_capture_metadata)

        camera.capture_multispectral_frame = fail_second_capture
        hardware = FakeHardwareController()
        with tempfile.TemporaryDirectory(prefix="capture_dark_partial_") as tmp:
            coordinator = CaptureCoordinator(
                camera_manager=camera,
                hardware_controller=hardware,
                sleep_fn=lambda seconds: None,
                capture_id_factory=lambda: "cap-dark-partial",
            )

            result = coordinator.run_dark_reference_capture(
                sample_id="S-CAL",
                output_dir=tmp,
                band_plan=plan,
                calibration_id="cal-partial",
                operator_confirmed=True,
            )

            self.assertEqual(result["state"], "failed")
            self.assertTrue((Path(tmp) / "calibration" / "dark" / "band_01_FIRST.png").exists())
            self.assertFalse((Path(tmp) / "calibration" / "dark" / "band_02_SECOND.png").exists())
            self.assertFalse((Path(tmp) / "calibration" / "dark" / "band_03_THIRD.png").exists())
            sequence = result["metadata"]["multispectralSequence"]
            self.assertTrue(sequence["partialCapture"])
            self.assertEqual(sequence["completedBands"], ["FIRST"])
            self.assertEqual(sequence["failedBand"], "SECOND")
            self.assertEqual(sequence["pendingBands"], ["THIRD"])
            calibration = result["metadata"]["calibrationSet"]
            self.assertFalse(calibration["calibrationComplete"])
            self.assertEqual(calibration["status"], "failed")
            self.assertTrue(calibration["partialCapture"])
            self.assertEqual(hardware.safe_stop_count, 1)

    def test_reference_capture_refuses_overwrite_and_cancel_timeout_follow_sequence_rules(self):
        with tempfile.TemporaryDirectory(prefix="capture_ref_overwrite_") as tmp:
            target = Path(tmp) / "calibration" / "dark" / "band_01_A.png"
            target.parent.mkdir(parents=True)
            target.write_bytes(b"existing")
            hardware = FakeHardwareController()
            coordinator = CaptureCoordinator(
                camera_manager=FakeCameraManager(),
                hardware_controller=hardware,
                sleep_fn=lambda seconds: None,
                capture_id_factory=lambda: "cap-dark-overwrite",
            )
            result = coordinator.run_dark_reference_capture(
                sample_id="S-CAL",
                output_dir=tmp,
                band_plan=[MultispectralBandPlan("A", 1, 510)],
                calibration_id="cal-overwrite",
                operator_confirmed=True,
            )
            self.assertEqual(result["state"], "failed")
            self.assertEqual(result["error"]["code"], "multispectral_file_exists")
            self.assertEqual(target.read_bytes(), b"existing")
            self.assertEqual(hardware.safe_stop_count, 1)

        with tempfile.TemporaryDirectory(prefix="capture_ref_cancel_") as tmp:
            sleep_calls = {"count": 0}
            hardware = FakeHardwareController()
            coordinator = CaptureCoordinator(
                camera_manager=FakeCameraManager(),
                hardware_controller=hardware,
                sleep_fn=lambda seconds: (
                    sleep_calls.__setitem__("count", sleep_calls["count"] + 1),
                    coordinator.request_cancel(),
                ),
                capture_id_factory=lambda: "cap-dark-cancel",
            )
            result = coordinator.run_dark_reference_capture(
                sample_id="S-CAL",
                output_dir=tmp,
                band_plan=[MultispectralBandPlan("A", 1, 510)],
                calibration_id="cal-cancel",
                operator_confirmed=True,
            )
            self.assertEqual(result["state"], "cancelled")
            self.assertEqual(hardware.safe_stop_count, 1)

        old_timeout = CaptureCoordinator.DEFAULT_STEP_TIMEOUTS_MS["filter_wheel_settle"]
        CaptureCoordinator.DEFAULT_STEP_TIMEOUTS_MS["filter_wheel_settle"] = 0
        try:
            hardware = FakeHardwareController()
            with tempfile.TemporaryDirectory(prefix="capture_ref_timeout_") as tmp:
                coordinator = CaptureCoordinator(
                    camera_manager=FakeCameraManager(),
                    hardware_controller=hardware,
                    clock=self.make_clock(),
                    sleep_fn=lambda seconds: None,
                    capture_id_factory=lambda: "cap-white-timeout",
                )
                result = coordinator.run_white_reference_capture(
                    sample_id="S-CAL",
                    output_dir=tmp,
                    band_plan=[MultispectralBandPlan("A", 1, 510)],
                    calibration_id="cal-timeout",
                    operator_confirmed=True,
                )
                self.assertEqual(result["state"], "failed")
                self.assertEqual(result["error"]["code"], "step_timeout")
                self.assertEqual(result["error"]["step"], "filter_wheel_settle:A")
                self.assertEqual(hardware.safe_stop_count, 1)
        finally:
            CaptureCoordinator.DEFAULT_STEP_TIMEOUTS_MS["filter_wheel_settle"] = old_timeout

    def test_calibration_compatibility_detects_camera_band_and_settings_mismatches(self):
        plan = MultispectralCapturePlan(
            bands=[MultispectralBandPlan("A520", 2, 520, exposure_us=11000.0, gain=1.1)],
            filter_config_source="unit-test",
            filter_config_version="test-profile",
            development_config=False,
            settling_ms=0,
        )
        with tempfile.TemporaryDirectory(prefix="capture_cal_compat_") as tmp:
            coordinator = CaptureCoordinator(
                camera_manager=FakeCameraManager(),
                hardware_controller=FakeHardwareController(),
                sleep_fn=lambda seconds: None,
                capture_id_factory=lambda: "cap-cal-dark",
            )
            coordinator.run_dark_reference_capture(
                sample_id="S-CAL",
                output_dir=tmp,
                band_plan=plan,
                calibration_id="cal-compat",
                operator_confirmed=True,
            )
            coordinator = CaptureCoordinator(
                camera_manager=FakeCameraManager(),
                hardware_controller=FakeHardwareController(),
                sleep_fn=lambda seconds: None,
                capture_id_factory=lambda: "cap-cal-white",
            )
            white = coordinator.run_white_reference_capture(
                sample_id="S-CAL",
                output_dir=tmp,
                band_plan=plan,
                calibration_id="cal-compat",
                operator_confirmed=True,
            )
            coordinator = CaptureCoordinator(
                camera_manager=FakeCameraManager(),
                hardware_controller=FakeHardwareController(),
                sleep_fn=lambda seconds: None,
                capture_id_factory=lambda: "cap-cal-sample",
            )
            sample = coordinator.run_multispectral_sequence(sample_id="S-CAL", output_dir=Path(tmp) / "sample", band_plan=plan)

            compatible = validate_calibration_compatibility(white["metadata"]["calibrationSet"], sample["metadata"])
            self.assertEqual(compatible["status"], "compatible")
            self.assertTrue(compatible["sameBandSettingsMatched"])

            sample["metadata"]["frames"][0]["actualExposureUs"] = 9999.0
            warning = validate_calibration_compatibility(white["metadata"]["calibrationSet"], sample["metadata"])
            self.assertEqual(warning["status"], "warning")
            self.assertFalse(warning["sameBandSettingsMatched"])

            sample["metadata"]["frames"][0]["device"]["serial"] = "OTHER"
            incompatible = validate_calibration_compatibility(white["metadata"]["calibrationSet"], sample["metadata"])
            self.assertEqual(incompatible["status"], "incompatible")

            sample["metadata"]["frames"][0]["device"]["serial"] = "DSGP23400004963"
            sample["metadata"]["multispectralSequence"]["bands"][0]["bandId"] = "OTHER"
            band_mismatch = validate_calibration_compatibility(white["metadata"]["calibrationSet"], sample["metadata"])
            self.assertEqual(band_mismatch["status"], "incompatible")

    def test_multispectral_sequence_regressions_keep_single_frame_and_rgb_paths(self):
        with tempfile.TemporaryDirectory(prefix="capture_ms_regression_") as tmp:
            coordinator = CaptureCoordinator(
                camera_manager=FakeCameraManager(),
                hardware_controller=FakeHardwareController(),
                sleep_fn=lambda seconds: None,
                capture_id_factory=lambda: "cap-ms-single-regression",
            )
            single = coordinator.run_multispectral_capture(sample_id="S-MS", output_dir=Path(tmp) / "single")
            self.assertEqual(single["state"], "completed")
            self.assertFalse(single["metadata"]["frames"][0]["filterWheelSynchronized"])
            self.assertEqual(single["metadata"]["frames"][0]["focus"]["status"], "ok")

            coordinator = CaptureCoordinator(
                camera_manager=FakeCameraManager(),
                hardware_controller=FakeHardwareController(),
                capture_id_factory=lambda: "cap-rgb-regression",
            )
            rgb = coordinator.run_rgb_capture(sample_id="S-RGB", output_dir=Path(tmp) / "rgb")
            self.assertEqual(rgb["state"], "completed")
            self.assertEqual(rgb["metadata"]["frames"][0]["role"], "rgb")
            self.assertNotIn("focus", rgb["metadata"]["frames"][0])


if __name__ == "__main__":
    unittest.main()
