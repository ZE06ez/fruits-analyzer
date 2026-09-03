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
    RgbCameraConfig,
    RgbUvcCamera,
    find_dvp2_sdk,
)
from camera_service.dvp2_binding import Dvp2ApiError, Dvp2DeviceInfo, dvpFrame


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


class FakeCv2DirectShowEncoded(FakeCv2):
    calls: list[tuple[int, int | None]] = []

    @classmethod
    def VideoCapture(cls, index, api_preference=None):
        cls.calls.append((index, api_preference))
        return FakeCapture(opened=api_preference is None and index == cls.CAP_DSHOW + 1)


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


class FakeDvp2Binding:
    def __init__(self, devices=None, frame_array=None, target_format=None):
        self.devices = devices or [
            Dvp2DeviceInfo(
                index=0,
                vendor="DO3THINK",
                manufacturer="DO3THINK",
                model="MGV231M-H2",
                friendly_name="MGV231M-H2-169.254.25.110",
                link_name="p234-169.254.25.110",
                port_info="169.254.25.110",
                serial_number="DSGP23400004963",
                camera_info="MAC B4-61-D3-14-6E-18",
                user_id="GP23400004963",
            )
        ]
        self.frame_array = frame_array if frame_array is not None else np.arange(6, dtype=np.uint16).reshape(2, 3)
        self.target_format = target_format
        self.opened_name = ""
        self.opened_index = None
        self.opened_user_id = ""
        self.closed = False
        self.started = False
        self.trigger_state = False
        self.exposure = 10000.0
        self.gain = 1.0

    def enum_devices(self):
        return list(self.devices)

    def open_by_name(self, friendly_name, auto_ip=False):
        self.opened_name = friendly_name
        return 101

    def open_by_user_id(self, user_id, auto_ip=False):
        self.opened_user_id = user_id
        return 103

    def open_by_index(self, index, auto_ip=False):
        self.opened_index = index
        return 102

    def close(self, handle):
        self.closed = True

    def start(self, handle):
        self.started = True

    def stop(self, handle):
        self.started = False

    def get_frame(self, handle, timeout_ms=3000):
        frame = dvpFrame()
        frame.format = 0
        frame.bits = 4 if self.frame_array.dtype == np.uint16 else 0
        frame.uBytes = self.frame_array.nbytes
        frame.iWidth = self.frame_array.shape[1]
        frame.iHeight = self.frame_array.shape[0]
        frame.uFrameID = 7
        frame.uTimestamp = 123456
        frame.fExposure = self.exposure
        frame.fAGain = self.gain
        return frame, self.frame_array.ctypes.data

    def get_camera_info(self, handle):
        return self.devices[0]

    def get_frame_count(self, handle):
        return {"frameCount": 1, "frameDrop": 0, "frameError": 0, "frameOk": 1, "frameRate": 25.0}

    def get_roi(self, handle):
        return 0, 0, 2048, 1200

    def get_exposure(self, handle):
        return self.exposure

    def set_exposure(self, handle, value):
        self.exposure = float(value)

    def get_exposure_descr(self, handle):
        return {"min": 1.0, "max": 1000000.0, "step": 1.0, "default": 10000.0}

    def get_analog_gain(self, handle):
        return self.gain

    def set_analog_gain(self, handle, value):
        self.gain = float(value)

    def get_analog_gain_descr(self, handle):
        return {"min": 1.0, "max": 16.0, "step": 0.1, "default": 1.0}

    def get_trigger_state(self, handle):
        return self.trigger_state

    def set_trigger_state(self, handle, enabled):
        self.trigger_state = bool(enabled)

    def set_trigger_source(self, handle, source=0):
        return None

    def trigger_fire(self, handle):
        return None

    def get_source_format(self, handle):
        return 34

    def get_target_format(self, handle):
        if self.target_format is not None:
            return self.target_format
        return 34 if self.frame_array.dtype == np.uint16 else 30


class OccupiedDvp2Binding(FakeDvp2Binding):
    def open_by_user_id(self, user_id, auto_ip=False):
        raise Dvp2ApiError("dvpOpenByUserId", -1105)

    def open_by_name(self, friendly_name, auto_ip=False):
        raise Dvp2ApiError("dvpOpenByName", -1105)


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

    def test_rgb_probe_success_then_close_still_reports_available(self):
        frame = np.zeros((2160, 3840, 3), dtype=np.uint8)
        camera = RgbUvcCamera(cv2_module=FakeCv2, capture_factory=lambda index: FakeCapture(frame=frame))

        self.assertTrue(camera.probe_available())
        status = camera.get_status().to_dict()

        self.assertFalse(camera.is_open)
        self.assertTrue(status["detected"])
        self.assertTrue(status["available"])
        self.assertTrue(status["connected"])
        self.assertFalse(status["opened"])
        self.assertFalse(status["streaming"])
        self.assertEqual(status["actual"]["lastFrameShape"], (2160, 3840, 3))

    def test_rgb_probe_failure_clears_available_state(self):
        camera = RgbUvcCamera(cv2_module=FakeCv2, capture_factory=lambda index: FakeCapture(opened=False))

        self.assertFalse(camera.probe_available())
        status = camera.get_status().to_dict()

        self.assertFalse(status["detected"])
        self.assertFalse(status["available"])
        self.assertFalse(status["connected"])
        self.assertFalse(status["opened"])
        self.assertIn("RGB 相机", status["error"])

    def test_rgb_probe_respects_configured_device_index_without_fallback(self):
        opened_indexes: list[int] = []

        def factory(index):
            opened_indexes.append(index)
            return FakeCapture(opened=index == 1)

        camera = RgbUvcCamera(
            config=RgbCameraConfig(device_index=1),
            cv2_module=FakeCv2,
            capture_factory=factory,
        )

        self.assertTrue(camera.probe_available())
        self.assertEqual(opened_indexes, [1])

    def test_rgb_probe_does_not_fallback_to_integrated_camera(self):
        opened_indexes: list[int] = []

        def factory(index):
            opened_indexes.append(index)
            return FakeCapture(opened=index == 0)

        camera = RgbUvcCamera(
            config=RgbCameraConfig(device_index=2),
            cv2_module=FakeCv2,
            capture_factory=factory,
        )

        self.assertFalse(camera.probe_available())
        self.assertEqual(opened_indexes, [2])

    def test_rgb_open_retries_directshow_encoded_index_without_scanning_other_cameras(self):
        FakeCv2DirectShowEncoded.calls = []
        camera = RgbUvcCamera(config=RgbCameraConfig(device_index=1), cv2_module=FakeCv2DirectShowEncoded)

        camera.open()

        self.assertTrue(camera.is_open)
        self.assertEqual(FakeCv2DirectShowEncoded.calls, [(1, FakeCv2.CAP_DSHOW), (701, None)])

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

    def test_dvp2_invalid_dll_symbols_are_reported(self):
        with tempfile.TemporaryDirectory(prefix="dvp2_stub_") as tmp:
            dll = Path(tmp) / "DVPCamera64.dll"
            dll.write_bytes(b"stub")
            camera = Dvp2MonoCamera(sdk_dir=tmp, loader=lambda path: object())

            with self.assertRaises(CameraSdkUnavailableError):
                camera.open()

    def test_dvp2_lists_devices_from_confirmed_binding(self):
        with tempfile.TemporaryDirectory(prefix="dvp2_fake_") as tmp:
            (Path(tmp) / "DVPCamera64.dll").write_bytes(b"stub")
            binding = FakeDvp2Binding()
            camera = Dvp2MonoCamera(sdk_dir=tmp, binding_factory=lambda path: binding)

            devices = camera.list_devices()
            status = camera.get_status().to_dict()

            self.assertEqual(len(devices), 1)
            self.assertEqual(devices[0].stable_id, "DSGP23400004963")
            self.assertEqual(devices[0].backend, "dvp2")
            self.assertTrue(status["detected"])
            self.assertTrue(status["connected"])
            self.assertEqual(status["actual"]["cameraSerial"], "DSGP23400004963")
            self.assertEqual(status["actual"]["cameraIp"], "169.254.25.110")
            self.assertEqual(status["actual"]["cameraMac"], "B4-61-D3-14-6E-18")

    def test_dvp2_open_prefers_configured_serial_over_index(self):
        with tempfile.TemporaryDirectory(prefix="dvp2_fake_") as tmp:
            (Path(tmp) / "DVPCamera64.dll").write_bytes(b"stub")
            first = Dvp2DeviceInfo(index=0, model="Other", friendly_name="OTHER", serial_number="OTHER001")
            second = Dvp2DeviceInfo(index=1, model="MGV231M-H2", friendly_name="TARGET", serial_number="GP23400004963")
            binding = FakeDvp2Binding(devices=[first, second])
            camera = Dvp2MonoCamera(sdk_dir=tmp, serial_number="GP23400004963", device_index=0, binding_factory=lambda path: binding)

            camera.open()

            self.assertEqual(binding.opened_name, "TARGET")
            self.assertIsNone(binding.opened_index)
            self.assertTrue(camera.get_status().available)

    def test_dvp2_capture_returns_uint16_mono_without_downcasting(self):
        with tempfile.TemporaryDirectory(prefix="dvp2_fake_") as tmp:
            (Path(tmp) / "DVPCamera64.dll").write_bytes(b"stub")
            mono = np.array([[0, 512, 1024], [2048, 4095, 65535]], dtype=np.uint16)
            binding = FakeDvp2Binding(frame_array=mono)
            camera = Dvp2MonoCamera(sdk_dir=tmp, binding_factory=lambda path: binding)

            camera.set_exposure(10000)
            camera.set_gain(1.0)
            frame = camera.capture_frame()
            status = camera.get_status().to_dict()

            self.assertEqual(frame.color_space, "MONO")
            self.assertEqual(frame.dtype, "uint16")
            self.assertEqual(frame.shape, (2, 3))
            self.assertEqual(frame.data.dtype, np.uint16)
            self.assertEqual(int(frame.data.max()), 65535)
            self.assertEqual(status["actual"]["width"], 3)
            self.assertEqual(status["actual"]["height"], 2)
            self.assertEqual(status["actual"]["pixelFormat"], "Mono16")
            self.assertEqual(status["actual"]["exposure"], 10000.0)
            self.assertEqual(status["actual"]["gain"], 1.0)

    def test_dvp2_capture_reports_verified_mono8_without_generic_pixel_placeholder(self):
        with tempfile.TemporaryDirectory(prefix="dvp2_fake_") as tmp:
            (Path(tmp) / "DVPCamera64.dll").write_bytes(b"stub")
            mono = np.array([[0, 10], [20, 30]], dtype=np.uint8)
            binding = FakeDvp2Binding(frame_array=mono, target_format=30)
            camera = Dvp2MonoCamera(sdk_dir=tmp, binding_factory=lambda path: binding)

            frame = camera.capture_frame()
            status = camera.get_status().to_dict()

            self.assertEqual(frame.dtype, "uint8")
            self.assertEqual(frame.metadata["pixelFormat"], "Mono8")
            self.assertEqual(status["actual"]["pixelFormat"], "Mono8")
            self.assertEqual(status["actual"]["frameDtype"], "uint8")
            self.assertNotIn("/", status["actual"]["pixelFormat"])
            self.assertEqual(status["capabilities"]["supportedPixelFormats"], ["Mono8"])

    def test_dvp2_status_splits_stream_fps_from_ethernet_link_speed(self):
        with tempfile.TemporaryDirectory(prefix="dvp2_fake_") as tmp:
            (Path(tmp) / "DVPCamera64.dll").write_bytes(b"stub")
            binding = FakeDvp2Binding()
            camera = Dvp2MonoCamera(sdk_dir=tmp, binding_factory=lambda path: binding)

            camera.open()
            status = camera.get_status().to_dict()

            self.assertEqual(status["actual"]["streamFps"], 25.0)
            self.assertIsNone(status["actual"]["linkSpeedMbps"])
            self.assertEqual(status["actual"]["linkSpeed"], "")
            self.assertNotIn("fps", status["actual"]["linkSpeed"])

    def test_dvp2_open_failure_after_enum_reports_occupied_hint(self):
        with tempfile.TemporaryDirectory(prefix="dvp2_fake_") as tmp:
            (Path(tmp) / "DVPCamera64.dll").write_bytes(b"stub")
            binding = OccupiedDvp2Binding()
            camera = Dvp2MonoCamera(sdk_dir=tmp, binding_factory=lambda path: binding)

            with self.assertRaises(CameraOpenError) as context:
                camera.open()

            self.assertIn("BasedCam3", context.exception.user_message)
            status = camera.get_status().to_dict()
            self.assertTrue(status["detected"])
            self.assertFalse(status["available"])
            self.assertIn("BasedCam3", status["error"])

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

            probed = manager.probe_rgb()
            started = manager.start_rgb_preview({"width": 320, "height": 180, "fps": 12})
            data, meta = manager.rgb_preview_jpeg()
            stopped = manager.stop_rgb_preview()

            self.assertTrue(probed["passed"])
            self.assertTrue(probed["status"]["available"])
            self.assertTrue(started["preview"]["rgb"]["running"])
            self.assertTrue(started["status"]["opened"])
            self.assertTrue(started["status"]["streaming"])
            self.assertTrue(data.startswith(b"\xff\xd8"))
            self.assertEqual(meta["previewWidth"], 320)
            self.assertEqual(meta["previewHeight"], 180)
            self.assertFalse(stopped["preview"]["rgb"]["running"])
            self.assertTrue(stopped["status"]["detected"])
            self.assertTrue(stopped["status"]["available"])
            self.assertFalse(stopped["status"]["opened"])
            with self.assertRaises(CameraError):
                manager.rgb_preview_jpeg()

    def test_camera_manager_rgb_preview_can_restart_after_stop(self):
        rgb = RgbUvcCamera(cv2_module=FakeCv2, capture_factory=lambda index: FakeCapture())
        with tempfile.TemporaryDirectory(prefix="dvp2_manager_") as tmp:
            manager = CameraManager(rgb_camera=rgb, multispectral_camera=Dvp2MonoCamera(sdk_dir=tmp))

            manager.probe_rgb()
            first = manager.start_rgb_preview()
            stopped = manager.stop_rgb_preview()
            second = manager.start_rgb_preview()

            self.assertTrue(first["preview"]["rgb"]["running"])
            self.assertFalse(stopped["preview"]["rgb"]["running"])
            self.assertTrue(stopped["status"]["available"])
            self.assertTrue(second["preview"]["rgb"]["running"])
            self.assertTrue(second["status"]["opened"])

    def test_camera_manager_rgb_preview_reports_unavailable_camera(self):
        rgb = RgbUvcCamera(cv2_module=FakeCv2, capture_factory=lambda index: FakeCapture(opened=False))
        with tempfile.TemporaryDirectory(prefix="dvp2_manager_") as tmp:
            manager = CameraManager(rgb_camera=rgb, multispectral_camera=Dvp2MonoCamera(sdk_dir=tmp))

            with self.assertRaises(CameraOpenError) as context:
                manager.start_rgb_preview()

            self.assertIn("AMCAP", context.exception.user_message)

    def test_camera_manager_multispectral_preview_uses_jpeg_without_downcasting_capture(self):
        rgb = RgbUvcCamera(cv2_module=FakeCv2, capture_factory=lambda index: FakeCapture())
        with tempfile.TemporaryDirectory(prefix="dvp2_manager_") as tmp:
            (Path(tmp) / "DVPCamera64.dll").write_bytes(b"stub")
            mono = np.array([[0, 1000], [4000, 65535]], dtype=np.uint16)
            binding = FakeDvp2Binding(frame_array=mono)
            multispectral = Dvp2MonoCamera(sdk_dir=tmp, binding_factory=lambda path: binding)
            manager = CameraManager(rgb_camera=rgb, multispectral_camera=multispectral)

            probed = manager.probe_multispectral()
            started = manager.start_multispectral_preview({"width": 320, "height": 180, "fps": 8})
            data, meta = manager.multispectral_preview_jpeg()
            raw_frame = multispectral.capture_frame()
            stopped = manager.stop_multispectral_preview()

            self.assertTrue(probed["passed"])
            self.assertTrue(started["preview"]["multispectral"]["running"])
            self.assertTrue(data.startswith(b"\xff\xd8"))
            self.assertEqual(meta["previewWidth"], 320)
            self.assertEqual(meta["sourceDtype"], "uint16")
            self.assertEqual(raw_frame.dtype, "uint16")
            self.assertEqual(raw_frame.data.dtype, np.uint16)
            self.assertFalse(stopped["preview"]["multispectral"]["running"])

    def test_camera_manager_multispectral_apply_settings_reads_back_actual_values(self):
        rgb = RgbUvcCamera(cv2_module=FakeCv2, capture_factory=lambda index: FakeCapture())
        with tempfile.TemporaryDirectory(prefix="dvp2_manager_") as tmp:
            (Path(tmp) / "DVPCamera64.dll").write_bytes(b"stub")
            binding = FakeDvp2Binding(frame_array=np.zeros((2, 2), dtype=np.uint8), target_format=30)
            multispectral = Dvp2MonoCamera(sdk_dir=tmp, binding_factory=lambda path: binding)
            manager = CameraManager(rgb_camera=rgb, multispectral_camera=multispectral)

            manager.start_multispectral_preview({"width": 320, "height": 180, "fps": 8})
            result = manager.apply_multispectral_settings({"exposure": 12000, "gain": 1.5})

            self.assertEqual(result["settingResults"]["exposure"]["actual"], 12000.0)
            self.assertEqual(result["settingResults"]["gain"]["actual"], 1.5)
            self.assertEqual(result["status"]["actual"]["exposure"], 12000.0)
            self.assertEqual(result["status"]["actual"]["gain"], 1.5)
            self.assertTrue(result["preview"]["multispectral"]["running"])

    def test_camera_manager_multispectral_preview_can_restart_without_new_adapter_instance(self):
        rgb = RgbUvcCamera(cv2_module=FakeCv2, capture_factory=lambda index: FakeCapture())
        calls = 0

        with tempfile.TemporaryDirectory(prefix="dvp2_manager_") as tmp:
            (Path(tmp) / "DVPCamera64.dll").write_bytes(b"stub")
            binding = FakeDvp2Binding(frame_array=np.zeros((2, 2), dtype=np.uint8), target_format=30)

            def factory(path):
                nonlocal calls
                calls += 1
                return binding

            multispectral = Dvp2MonoCamera(sdk_dir=tmp, binding_factory=factory)
            manager = CameraManager(rgb_camera=rgb, multispectral_camera=multispectral)

            first = manager.start_multispectral_preview()
            stopped = manager.stop_multispectral_preview()
            second = manager.start_multispectral_preview()

            self.assertTrue(first["preview"]["multispectral"]["running"])
            self.assertFalse(stopped["preview"]["multispectral"]["running"])
            self.assertTrue(second["preview"]["multispectral"]["running"])
            self.assertEqual(calls, 1)

    def test_camera_frame_allows_uint16_mono_data(self):
        mono = np.zeros((2, 3), dtype=np.uint16)

        frame = CameraFrame(data=mono, color_space="MONO", dtype=str(mono.dtype), shape=mono.shape)

        self.assertEqual(frame.dtype, "uint16")
        self.assertEqual(frame.shape, (2, 3))


if __name__ == "__main__":
    unittest.main()
