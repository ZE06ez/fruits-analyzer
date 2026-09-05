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
)
from hardware_controller import DoorState, OutputStatus


class FakeCameraManager:
    def __init__(self, frame=None, *, fail_capture: Exception | None = None, capture_metadata=None):
        self.frame = frame if frame is not None else CameraFrame(
            data=np.zeros((4, 5, 3), dtype=np.uint8),
            color_space="RGB",
            dtype="uint8",
            shape=(4, 5, 3),
            metadata={"sourceColorSpace": "BGR", "deviceIndex": 1},
        )
        self.fail_capture = fail_capture
        self.capture_metadata = capture_metadata or {
            "previewWasRunning": False,
            "openedForCapture": True,
            "device": {"deviceIndex": 1, "transport": "UVC/DirectShow", "backend": "opencv"},
            "requestedSettings": {"deviceIndex": 1, "width": 3840, "height": 2160, "fps": 25, "fourcc": "MJPG"},
            "actualSettings": {"width": 3840, "height": 2160, "fps": 25.0, "fourcc": "MJPG"},
            "status": {"available": True, "streaming": False},
        }
        self.capture_count = 0

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


if __name__ == "__main__":
    unittest.main()
