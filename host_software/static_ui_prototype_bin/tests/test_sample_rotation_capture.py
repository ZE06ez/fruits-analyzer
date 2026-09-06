from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from camera_service import CameraFrame
from capture_coordinator import CaptureCoordinator, MultispectralBandPlan
from rotation_plan import build_capture_rotation_plan
from sample_stage import SampleStagePosition
from tests.test_capture_coordinator import FakeCameraManager, FakeHardwareController


class FakeSampleStage:
    implemented = True
    mode = "simulation"

    def __init__(self):
        self.calls = []
        self.angle = 0.0
        self.fail_move_on: set[str] = set()
        self.fail_return_home = False
        self.home_count = 0

    def home_sample_stage(self):
        self.calls.append(("home_sample_stage",))
        self.home_count += 1
        if self.fail_return_home and self.home_count > 1:
            raise RuntimeError("return home jammed")
        self.angle = 0.0
        return {"homed": True, "angleDeg": self.angle}

    def move_sample_stage_to_angle(self, angle_deg, *, direction="CW"):
        self.calls.append(("move_sample_stage_to_angle", float(angle_deg), direction))
        if f"{float(angle_deg):g}" in self.fail_move_on:
            raise RuntimeError("stage move failed")
        self.angle = float(angle_deg)
        return {"targetAngleDeg": self.angle, "direction": direction, "commandAccepted": True}

    def wait_sample_stage_stable(self):
        self.calls.append(("wait_sample_stage_stable",))
        return {"stable": True}

    def get_sample_stage_position(self):
        self.calls.append(("get_sample_stage_position",))
        return SampleStagePosition(self.angle, True, status="verified")


def three_band_plan():
    return [
        MultispectralBandPlan("A520", 1, 520, exposure_us=11000.0, gain=1.1),
        MultispectralBandPlan("B610", 2, 610, exposure_us=12000.0, gain=1.2),
    ]


class SampleRotationCaptureTests(unittest.TestCase):
    def make_clock(self):
        current = {"value": 100.0}

        def tick():
            current["value"] += 0.01
            return current["value"]

        return tick

    def test_single_view_works_without_sample_stage_hardware(self):
        camera = FakeCameraManager()
        hardware = FakeHardwareController()
        with tempfile.TemporaryDirectory(prefix="sample_multiview_single_") as tmp:
            coordinator = CaptureCoordinator(
                camera_manager=camera,
                hardware_controller=hardware,
                sleep_fn=lambda seconds: None,
                capture_id_factory=lambda: "cap-single-view",
            )

            result = coordinator.run_sample_multiview_capture(
                sample_id="S-MV",
                output_dir=tmp,
                sample_rotation={"enabled": False},
                band_plan=three_band_plan(),
                settling_ms=0,
            )

        self.assertEqual(result["state"], "completed")
        self.assertEqual(camera.capture_count, 1)
        self.assertEqual(camera.multispectral_capture_count, 2)
        self.assertEqual(result["metadata"]["completedViews"], ["view_000"])
        self.assertTrue(result["metadata"]["multiViewCaptureComplete"])
        view = result["metadata"]["views"][0]
        self.assertEqual(view["sample_id"], "S-MV")
        self.assertTrue(view["viewComplete"])
        self.assertTrue(view["sampleRotation"]["verified"])
        self.assertEqual(view["sampleRotation"]["status"], "single_view_no_motion_required")
        self.assertFalse(any(call[0] == "home_sample_stage" for call in getattr(coordinator.sample_stage_controller, "calls", [])))

    def test_three_view_plan_moves_stage_and_captures_rgb_plus_multispectral_per_view(self):
        stage = FakeSampleStage()
        camera = FakeCameraManager()
        hardware = FakeHardwareController()
        sleeps = []
        plan = build_capture_rotation_plan({
            "enabled": True,
            "expectedIntervalDeg": 120,
            "direction": "CCW",
            "includeClosureView": False,
        })
        with tempfile.TemporaryDirectory(prefix="sample_multiview_three_") as tmp:
            coordinator = CaptureCoordinator(
                camera_manager=camera,
                hardware_controller=hardware,
                sample_stage_controller=stage,
                sleep_fn=lambda seconds: sleeps.append(seconds),
                capture_id_factory=lambda: "cap-three-view",
            )

            result = coordinator.run_sample_multiview_capture(
                sample_id="S-MV-3",
                output_dir=tmp,
                rotation_plan=plan,
                band_plan=three_band_plan(),
                settling_ms=0,
                sample_stage_settling_ms=25,
                sample_stage_mode="simulation",
                calibration_id="cal-shared",
            )
            root = Path(tmp)
            view000_rgb_exists = (root / "views" / "view_000" / "rgb" / "rgb_view_000.png").exists()
            view120_ms_exists = (root / "views" / "view_120" / "multispectral" / "band_01_A520.png").exists()

        self.assertEqual(result["state"], "completed")
        self.assertEqual([call for call in stage.calls if call[0] == "move_sample_stage_to_angle"], [
            ("move_sample_stage_to_angle", 0.0, "CCW"),
            ("move_sample_stage_to_angle", 120.0, "CCW"),
            ("move_sample_stage_to_angle", 240.0, "CCW"),
        ])
        self.assertEqual(camera.capture_count, 3)
        self.assertEqual(camera.multispectral_capture_count, 6)
        self.assertIn(0.025, sleeps)
        self.assertTrue(view000_rgb_exists)
        self.assertTrue(view120_ms_exists)
        self.assertEqual(result["metadata"]["calibrationId"], "cal-shared")
        self.assertTrue(all(view["sample_id"] == "S-MV-3" for view in result["metadata"]["views"]))
        self.assertTrue(all(view["rgbComplete"] and view["multispectralComplete"] for view in result["metadata"]["views"]))
        self.assertTrue(all(frame["view_id"] in {"view_000", "view_120", "view_240"} for frame in result["metadata"]["frames"]))
        self.assertEqual(result["metadata"]["completedViews"], ["view_000", "view_120", "view_240"])
        self.assertEqual(result["metadata"]["pendingViews"], [])
        self.assertIsNone(result["metadata"]["failedView"])
        self.assertTrue(result["metadata"]["multiViewCaptureComplete"])
        first_view = result["metadata"]["views"][0]
        self.assertEqual(first_view["sampleRotation"]["controlDomain"], "sample_rotation")
        self.assertEqual(first_view["filterWheel"]["controlDomain"], "filter_wheel_rotation")
        self.assertTrue(first_view["filterWheel"]["independentFromSampleRotation"])

    def test_cw_ccw_no_360_duplicate_and_explicit_closure_view(self):
        no_closure = build_capture_rotation_plan({
            "enabled": True,
            "expectedIntervalDeg": 90,
            "direction": "CW",
            "includeClosureView": False,
        })
        closure = build_capture_rotation_plan({
            "enabled": True,
            "expectedIntervalDeg": 180,
            "direction": "CCW",
            "includeClosureView": True,
        })
        self.assertEqual([view["view_id"] for view in no_closure["views"]], ["view_000", "view_090", "view_180", "view_270"])
        self.assertNotIn("view_360", [view["view_id"] for view in no_closure["views"]])
        self.assertEqual([view["view_id"] for view in closure["views"]], ["view_000", "view_180", "view_360"])
        self.assertTrue(closure["views"][-1]["closure_view"])
        self.assertEqual(closure["views"][-1]["logical_angle_deg"], 0)
        self.assertEqual(closure["views"][-1]["mechanical_angle_deg"], 360)

    def test_second_view_failure_stops_third_and_preserves_first_view_data(self):
        camera = FakeCameraManager()

        def fail_second_rgb():
            camera.capture_count += 1
            if camera.capture_count == 2:
                raise RuntimeError("rgb camera busy")
            return camera.frame, dict(camera.capture_metadata)

        camera.capture_rgb_frame = fail_second_rgb
        stage = FakeSampleStage()
        hardware = FakeHardwareController()
        plan = build_capture_rotation_plan({"enabled": True, "expectedIntervalDeg": 120})
        with tempfile.TemporaryDirectory(prefix="sample_multiview_failure_") as tmp:
            coordinator = CaptureCoordinator(
                camera_manager=camera,
                hardware_controller=hardware,
                sample_stage_controller=stage,
                sleep_fn=lambda seconds: None,
                capture_id_factory=lambda: "cap-view-fail",
            )
            result = coordinator.run_sample_multiview_capture(
                sample_id="S-MV-FAIL",
                output_dir=tmp,
                rotation_plan=plan,
                band_plan=three_band_plan(),
                settling_ms=0,
                sample_stage_settling_ms=0,
                sample_stage_mode="simulation",
            )
            root = Path(tmp)
            view000_rgb_exists = (root / "views" / "view_000" / "rgb" / "rgb_view_000.png").exists()
            view240_exists = (root / "views" / "view_240").exists()

        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["metadata"]["completedViews"], ["view_000"])
        self.assertEqual(result["metadata"]["failedView"], "view_120")
        self.assertEqual(result["metadata"]["pendingViews"], ["view_240"])
        self.assertTrue(result["metadata"]["partialCapture"])
        self.assertTrue(view000_rgb_exists)
        self.assertFalse(view240_exists)
        self.assertEqual(hardware.safe_stop_count, 1)

    def test_cancel_does_not_start_next_view_and_records_partial_capture(self):
        camera = FakeCameraManager()
        stage = FakeSampleStage()
        hardware = FakeHardwareController()
        sample_settles = {"count": 0}
        plan = build_capture_rotation_plan({"enabled": True, "expectedIntervalDeg": 120})
        with tempfile.TemporaryDirectory(prefix="sample_multiview_cancel_") as tmp:
            coordinator = CaptureCoordinator(
                camera_manager=camera,
                hardware_controller=hardware,
                sample_stage_controller=stage,
                sleep_fn=lambda seconds: (
                    sample_settles.__setitem__("count", sample_settles["count"] + 1),
                    coordinator.request_cancel() if seconds == 0.01 and sample_settles["count"] == 4 else None,
                ),
                capture_id_factory=lambda: "cap-view-cancel",
            )
            result = coordinator.run_sample_multiview_capture(
                sample_id="S-MV-CANCEL",
                output_dir=tmp,
                rotation_plan=plan,
                band_plan=three_band_plan(),
                settling_ms=0,
                sample_stage_settling_ms=10,
                sample_stage_mode="simulation",
            )

        self.assertEqual(result["state"], "cancelled")
        self.assertTrue(result["metadata"]["cancelled"])
        self.assertEqual(result["metadata"]["completedViews"], ["view_000"])
        self.assertTrue(result["metadata"]["partialCapture"])
        self.assertEqual(camera.capture_count, 1)
        self.assertEqual(hardware.safe_stop_count, 1)

    def test_timeout_safe_stop_and_return_home_failure_preserves_data(self):
        old_timeout = CaptureCoordinator.DEFAULT_STEP_TIMEOUTS_MS["sample_stage_settle"]
        CaptureCoordinator.DEFAULT_STEP_TIMEOUTS_MS["sample_stage_settle"] = 0
        try:
            with tempfile.TemporaryDirectory(prefix="sample_multiview_timeout_") as tmp:
                coordinator = CaptureCoordinator(
                    camera_manager=FakeCameraManager(),
                    hardware_controller=FakeHardwareController(),
                    sample_stage_controller=FakeSampleStage(),
                    clock=self.make_clock(),
                    sleep_fn=lambda seconds: None,
                    capture_id_factory=lambda: "cap-view-timeout",
                )
                result = coordinator.run_sample_multiview_capture(
                    sample_id="S-MV-TIMEOUT",
                    output_dir=tmp,
                    rotation_plan=build_capture_rotation_plan({"enabled": True, "expectedIntervalDeg": 180}),
                    band_plan=three_band_plan(),
                    settling_ms=0,
                    sample_stage_settling_ms=0,
                    sample_stage_mode="simulation",
                )
            self.assertEqual(result["state"], "failed")
            self.assertEqual(result["error"]["code"], "step_timeout")
            self.assertEqual(result["error"]["step"], "sample_stage_settle:view_000")
        finally:
            CaptureCoordinator.DEFAULT_STEP_TIMEOUTS_MS["sample_stage_settle"] = old_timeout

        stage = FakeSampleStage()
        stage.fail_return_home = True
        with tempfile.TemporaryDirectory(prefix="sample_multiview_return_home_") as tmp:
            coordinator = CaptureCoordinator(
                camera_manager=FakeCameraManager(),
                hardware_controller=FakeHardwareController(),
                sample_stage_controller=stage,
                sleep_fn=lambda seconds: None,
                capture_id_factory=lambda: "cap-return-home",
            )
            result = coordinator.run_sample_multiview_capture(
                sample_id="S-MV-HOME",
                output_dir=tmp,
                rotation_plan=build_capture_rotation_plan({"enabled": True, "expectedIntervalDeg": 180}),
                band_plan=three_band_plan(),
                settling_ms=0,
                sample_stage_settling_ms=0,
                sample_stage_mode="simulation",
                return_home=True,
            )
            root = Path(tmp)
            view000_rgb_exists = (root / "views" / "view_000" / "rgb" / "rgb_view_000.png").exists()
            view180_band_exists = (root / "views" / "view_180" / "multispectral" / "band_02_B610.png").exists()

        self.assertEqual(result["state"], "completed")
        self.assertFalse(result["metadata"]["returnedHome"])
        self.assertEqual(result["metadata"]["homeStatus"], "sample_stage_home_failed")
        self.assertTrue(view000_rgb_exists)
        self.assertTrue(view180_band_exists)

    def test_hardware_mode_without_sample_stage_adapter_fails_explicitly(self):
        with tempfile.TemporaryDirectory(prefix="sample_multiview_no_stage_") as tmp:
            coordinator = CaptureCoordinator(
                camera_manager=FakeCameraManager(),
                hardware_controller=FakeHardwareController(),
                sleep_fn=lambda seconds: None,
                capture_id_factory=lambda: "cap-no-stage",
            )
            result = coordinator.run_sample_multiview_capture(
                sample_id="S-MV-HW",
                output_dir=tmp,
                rotation_plan=build_capture_rotation_plan({"enabled": True, "expectedIntervalDeg": 180}),
                band_plan=three_band_plan(),
                settling_ms=0,
                sample_stage_settling_ms=0,
                sample_stage_mode="hardware",
            )

        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["error"]["code"], "hardware_not_implemented")
        self.assertEqual(result["metadata"]["completedViews"], [])
        self.assertFalse(result["metadata"]["multiViewCaptureComplete"])

    def test_uint16_formal_multispectral_single_frame_still_works(self):
        import numpy as np

        frame_data = np.array([[0, 65535], [4096, 1024]], dtype=np.uint16)
        camera = FakeCameraManager(multispectral_frame=CameraFrame(
            data=frame_data,
            color_space="MONO",
            dtype="uint16",
            shape=frame_data.shape,
            metadata={"pixelFormat": "Mono16", "exposure": 10000.0, "gain": 1.0},
        ))
        with tempfile.TemporaryDirectory(prefix="sample_multiview_u16_regression_") as tmp:
            coordinator = CaptureCoordinator(
                camera_manager=camera,
                hardware_controller=FakeHardwareController(),
                sleep_fn=lambda seconds: None,
                capture_id_factory=lambda: "cap-u16-regression",
            )
            result = coordinator.run_multispectral_sequence(
                sample_id="S-U16",
                output_dir=tmp,
                band_plan=[MultispectralBandPlan("NIR", 1, 850)],
                settling_ms=0,
            )
        self.assertEqual(result["state"], "completed")
        self.assertEqual(result["metadata"]["frames"][0]["dtype"], "uint16")
        self.assertEqual(result["metadata"]["frames"][0]["captureType"], "sample")


if __name__ == "__main__":
    unittest.main()
