from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np

from camera_service import (
    CameraCaptureError,
    CameraError,
    CameraFrame,
    CameraManager,
    CameraOpenError,
    CameraSdkUnavailableError,
    CameraSettingUnsupported,
    Dvp2MonoCamera,
    RgbUvcCamera,
    find_dvp2_sdk,
)


class FakeCv2:
    CAP_DSHOW = 700
    CAP_PROP_FRAME_WIDTH = 3
    CAP_PROP_FRAME_HEIGHT = 4
    CAP_PROP_FPS = 5
    CAP_PROP_FOURCC = 6
    CAP_PROP_AUTO_EXPOSURE = 21
    CAP_PROP_EXPOSURE = 15
    CAP_PROP_GAIN = 14
    CAP_PROP_AUTO_WB = 44
    CAP_PROP_WB_TEMPERATURE = 45
    COLOR_BGR2RGB = 4

    @staticmethod
    def VideoWriter_fourcc(*value):
        return sum(ord(value[index]) << (8 * index) for index in range(len(value)))

    @staticmethod
    def cvtColor(frame, code):
        if code != FakeCv2.COLOR_BGR2RGB:
            raise ValueError("unexpected conversion")
        return frame[:, :, ::-1].copy()


class FakeCapture:
    def __init__(self, *, opened=True, frame=None, read_ok=True, set_ok=True):
        self.opened = opened
        self.frame = frame
        self.read_ok = read_ok
        self.set_ok = set_ok
        self.released = False
        self.properties = {
            FakeCv2.CAP_PROP_FRAME_WIDTH: 640.0,
            FakeCv2.CAP_PROP_FRAME_HEIGHT: 480.0,
            FakeCv2.CAP_PROP_FPS: 30.0,
            FakeCv2.CAP_PROP_FOURCC: float(FakeCv2.VideoWriter_fourcc(*"YUY2")),
            FakeCv2.CAP_PROP_AUTO_EXPOSURE: 0.75,
            FakeCv2.CAP_PROP_EXPOSURE: -5.0,
            FakeCv2.CAP_PROP_GAIN: 1.0,
            FakeCv2.CAP_PROP_AUTO_WB: 1.0,
            FakeCv2.CAP_PROP_WB_TEMPERATURE: 4600.0,
        }

    def isOpened(self):
        return self.opened and not self.released

    def read(self):
        if not self.read_ok:
            return False, None
        frame = self.frame
        if frame is None:
            frame = np.array([[[10, 20, 30]]], dtype=np.uint8)
        return True, frame

    def release(self):
        self.released = True

    def set(self, prop, value):
        if not self.set_ok:
            return False
        self.properties[prop] = float(value)
        return True

    def get(self, prop):
        return self.properties.get(prop, 0.0)


class CameraServiceTests(unittest.TestCase):
    def test_rgb_default_config_uses_verified_development_machine_settings(self):
        camera = RgbUvcCamera(cv2_module=FakeCv2, capture_factory=lambda index: FakeCapture())

        self.assertEqual(camera.config.device_index, 1)
        self.assertEqual(camera.config.width, 3840)
        self.assertEqual(camera.config.height, 2160)
        self.assertEqual(camera.config.fps, 25.0)
        self.assertEqual(camera.config.fourcc, "MJPG")

    def test_rgb_requested_and_actual_configuration_are_separate(self):
        capture = FakeCapture()
        camera = RgbUvcCamera(cv2_module=FakeCv2, capture_factory=lambda index: capture)

        camera.open()
        status = camera.get_status().to_dict()

        self.assertEqual(status["transport"], "UVC/DirectShow")
        self.assertEqual(status["requested"]["deviceIndex"], 1)
        self.assertEqual(status["requested"]["width"], 3840)
        self.assertEqual(status["actual"]["width"], 3840)
        self.assertEqual(status["actual"]["height"], 2160)
        self.assertEqual(status["actual"]["fps"], 25.0)
        self.assertEqual(status["actual"]["fourcc"], "MJPG")
        self.assertTrue(status["actual"]["matchesRequested"])

    def test_rgb_actual_configuration_reports_driver_fallback(self):
        capture = FakeCapture(set_ok=False)
        camera = RgbUvcCamera(cv2_module=FakeCv2, capture_factory=lambda index: capture)

        camera.open()
        status = camera.get_status().to_dict()

        self.assertEqual(status["requested"]["width"], 3840)
        self.assertEqual(status["actual"]["width"], 640)
        self.assertEqual(status["actual"]["height"], 480)
        self.assertEqual(status["actual"]["fps"], 30.0)
        self.assertEqual(status["actual"]["fourcc"], "YUY2")
        self.assertFalse(status["actual"]["matchesRequested"])

    def test_rgb_capability_probe_marks_unsupported_setting(self):
        capture = FakeCapture(set_ok=False)
        camera = RgbUvcCamera(cv2_module=FakeCv2, capture_factory=lambda index: capture)

        camera.open()
        capabilities = camera.get_status().to_dict()["capabilities"]

        self.assertTrue(capabilities["exposure"]["supported"])
        self.assertFalse(capabilities["exposure"]["settable"])
        self.assertEqual(capabilities["whiteBalance"]["current"], 4600.0)

    def test_rgb_open_failure_reports_camera_open_error(self):
        camera = RgbUvcCamera(cv2_module=FakeCv2, capture_factory=lambda index: FakeCapture(opened=False))

        with self.assertRaises(CameraOpenError):
            camera.open()

        status = camera.get_status()
        self.assertFalse(status.connected)
        self.assertIn("RGB 相机", status.error)

    def test_rgb_capture_returns_rgb_uint8_frame(self):
        bgr = np.array([[[1, 2, 3], [4, 5, 6]]], dtype=np.uint8)
        camera = RgbUvcCamera(cv2_module=FakeCv2, capture_factory=lambda index: FakeCapture(frame=bgr))

        frame = camera.capture_frame()

        self.assertEqual(frame.color_space, "RGB")
        self.assertEqual(frame.dtype, "uint8")
        self.assertEqual(frame.shape, (1, 2, 3))
        self.assertEqual(frame.data.tolist(), [[[3, 2, 1], [6, 5, 4]]])

    def test_rgb_capture_failure_raises_capture_error(self):
        camera = RgbUvcCamera(cv2_module=FakeCv2, capture_factory=lambda index: FakeCapture(read_ok=False))

        with self.assertRaises(CameraCaptureError):
            camera.capture_frame()

    def test_rgb_close_is_idempotent(self):
        capture = FakeCapture()
        camera = RgbUvcCamera(cv2_module=FakeCv2, capture_factory=lambda index: capture)

        camera.open()
        camera.close()
        camera.close()

        self.assertFalse(camera.is_open)
        self.assertTrue(capture.released)

    def test_rgb_exposure_unsupported_when_driver_rejects_property(self):
        camera = RgbUvcCamera(cv2_module=FakeCv2, capture_factory=lambda index: FakeCapture(set_ok=False))

        with self.assertRaises(CameraSettingUnsupported):
            camera.set_exposure(-6)

    def test_dvp2_sdk_missing_is_reported_without_crashing(self):
        with tempfile.TemporaryDirectory(prefix="dvp2_missing_") as tmp:
            camera = Dvp2MonoCamera(sdk_dir=tmp)
            status = camera.get_status()

            self.assertFalse(status.sdk_available)
            self.assertEqual(status.transport, "GigE/DVP2")
            self.assertEqual(status.requested["vendor"], "DO3THINK")
            self.assertIn("SDK", status.error)
            with self.assertRaises(CameraSdkUnavailableError):
                camera.open()

    def test_dvp2_dll_load_failure_is_reported(self):
        with tempfile.TemporaryDirectory(prefix="dvp2_bad_dll_") as tmp:
            dll = Path(tmp) / "DVPCamera64.dll"
            dll.write_bytes(b"not a real dll")
            camera = Dvp2MonoCamera(
                sdk_dir=tmp,
                loader=lambda path: (_ for _ in ()).throw(OSError("bad dll")),
            )

            with self.assertRaises(CameraSdkUnavailableError):
                camera.list_devices()

    def test_dvp2_unconfirmed_api_does_not_guess_function_names(self):
        with tempfile.TemporaryDirectory(prefix="dvp2_stub_") as tmp:
            dll = Path(tmp) / "DVPCamera64.dll"
            dll.write_bytes(b"stub")
            camera = Dvp2MonoCamera(sdk_dir=tmp, loader=lambda path: object())

            with self.assertRaises(CameraSettingUnsupported):
                camera.open()

    def test_find_dvp2_sdk_uses_configured_directory(self):
        with tempfile.TemporaryDirectory(prefix="dvp2_find_") as tmp:
            dll = Path(tmp) / "DVPCamera64.dll"
            dll.write_bytes(b"stub")

            info = find_dvp2_sdk(tmp)

            self.assertTrue(info.sdk_available)
            self.assertEqual(info.dll_path, dll)

    def test_camera_manager_status_and_checks(self):
        rgb = RgbUvcCamera(cv2_module=FakeCv2, capture_factory=lambda index: FakeCapture())
        with tempfile.TemporaryDirectory(prefix="dvp2_manager_") as tmp:
            manager = CameraManager(rgb_camera=rgb, multispectral_camera=Dvp2MonoCamera(sdk_dir=tmp))

            status = manager.status()
            checks = manager.checks(probe_rgb=True)

            self.assertIn("rgb", status)
            self.assertIn("multispectral", status)
            self.assertEqual(status["rgb"]["transport"], "UVC/DirectShow")
            self.assertEqual(status["multispectral"]["transport"], "GigE/DVP2")
            self.assertEqual(checks["rgbCamera"]["status"], "passed")
            self.assertEqual(checks["multispectralCamera"]["status"], "sdk_missing")

    def test_camera_manager_applies_dynamic_rgb_settings_without_restart(self):
        capture = FakeCapture()
        rgb = RgbUvcCamera(cv2_module=FakeCv2, capture_factory=lambda index: capture)
        rgb.open()
        with tempfile.TemporaryDirectory(prefix="dvp2_manager_") as tmp:
            manager = CameraManager(rgb_camera=rgb, multispectral_camera=Dvp2MonoCamera(sdk_dir=tmp))

            result = manager.apply_rgb_settings({
                "deviceIndex": 1,
                "width": 3840,
                "height": 2160,
                "fps": 25,
                "fourcc": "MJPG",
                "autoExposure": 0.25,
                "exposure": -6,
                "gain": 2,
                "autoWhiteBalance": 0,
                "whiteBalance": 5000,
            })

            self.assertFalse(result["restartRequired"])
            self.assertFalse(capture.released)
            self.assertEqual(result["status"]["actual"]["exposure"], -6.0)
            self.assertEqual(result["status"]["actual"]["gain"], 2.0)
            self.assertEqual(result["status"]["actual"]["whiteBalance"], 5000.0)
            self.assertTrue(result["status"]["capabilities"]["lastApply"]["exposure"]["accepted"])

    def test_camera_manager_restarts_rgb_for_stream_configuration_change(self):
        captures: list[FakeCapture] = []

        def factory(index):
            capture = FakeCapture()
            captures.append(capture)
            return capture

        rgb = RgbUvcCamera(cv2_module=FakeCv2, capture_factory=factory)
        with tempfile.TemporaryDirectory(prefix="dvp2_manager_") as tmp:
            manager = CameraManager(rgb_camera=rgb, multispectral_camera=Dvp2MonoCamera(sdk_dir=tmp))
            manager.start_rgb_preview({"width": 320, "height": 180, "fps": 5})

            result = manager.apply_rgb_settings({
                "deviceIndex": 1,
                "width": 1920,
                "height": 1080,
                "fps": 25,
                "fourcc": "MJPG",
            })

            self.assertTrue(result["restartRequired"])
            self.assertTrue(result["previewRestarted"])
            self.assertEqual(len(captures), 2)
            self.assertTrue(captures[0].released)
            self.assertTrue(rgb.is_open)

    def test_camera_manager_rgb_preview_start_frame_stop(self):
        frame = np.zeros((20, 30, 3), dtype=np.uint8)
        rgb = RgbUvcCamera(cv2_module=FakeCv2, capture_factory=lambda index: FakeCapture(frame=frame))
        with tempfile.TemporaryDirectory(prefix="dvp2_manager_") as tmp:
            manager = CameraManager(rgb_camera=rgb, multispectral_camera=Dvp2MonoCamera(sdk_dir=tmp))

            started = manager.start_rgb_preview({"width": 320, "height": 180, "fps": 12})
            data, meta = manager.rgb_preview_jpeg()
            stopped = manager.stop_rgb_preview()

            self.assertTrue(started["preview"]["rgb"]["running"])
            self.assertTrue(data.startswith(b"\xff\xd8"))
            self.assertEqual(meta["previewWidth"], 320)
            self.assertEqual(meta["previewHeight"], 180)
            self.assertFalse(stopped["preview"]["rgb"]["running"])
            with self.assertRaises(CameraError):
                manager.rgb_preview_jpeg()

    def test_camera_manager_rgb_preview_reports_unavailable_camera(self):
        rgb = RgbUvcCamera(cv2_module=FakeCv2, capture_factory=lambda index: FakeCapture(opened=False))
        with tempfile.TemporaryDirectory(prefix="dvp2_manager_") as tmp:
            manager = CameraManager(rgb_camera=rgb, multispectral_camera=Dvp2MonoCamera(sdk_dir=tmp))

            with self.assertRaises(CameraOpenError) as context:
                manager.start_rgb_preview()

            self.assertIn("AMCAP", context.exception.user_message)

    def test_camera_frame_allows_uint16_mono_data(self):
        mono = np.zeros((2, 3), dtype=np.uint16)

        frame = CameraFrame(data=mono, color_space="MONO", dtype=str(mono.dtype), shape=mono.shape)

        self.assertEqual(frame.dtype, "uint16")
        self.assertEqual(frame.shape, (2, 3))


if __name__ == "__main__":
    unittest.main()
