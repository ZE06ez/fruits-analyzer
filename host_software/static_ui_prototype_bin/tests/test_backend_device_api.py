from __future__ import annotations

import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path

from backend_server import JobStore, SessionState, create_handler
from device_discovery import DeviceCandidate, DeviceRegistry, DeviceRole
from device_manager import CameraIntegrationRequired, UnsupportedCapabilityError

try:
    from .http_test_utils import InProcessHttpClient
except ImportError:
    from http_test_utils import InProcessHttpClient


class _BufferedResponse:
    def __init__(self, body: bytes, status: int, reason: str, headers):
        self._body = body
        self.status = status
        self.reason = reason
        self.headers = headers

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self) -> bytes:
        return self._body


class FakeCameraManager:
    def __init__(self):
        self.applied_payload = None
        self.multispectral_applied_payload = None
        self.preview_running = False
        self.multispectral_preview_running = False
        self.saved_settings_payload = None
        self.reset_settings_payload = None
        self.restore_settings_payload = None
        self.legacy_settings_payload = None

    def status(self):
        return {
            "rgb": {
                "detected": True,
                "available": True,
                "connected": True,
                "opened": self.preview_running,
                "streaming": self.preview_running,
                "transport": "UVC/DirectShow",
                "requested": {"deviceIndex": 1, "width": 3840, "height": 2160, "fps": 25, "fourcc": "MJPG"},
                "actual": {"width": 3840, "height": 2160, "fps": 25, "fourcc": "MJPG", "matchesRequested": True},
                "capabilities": {"exposure": {"supported": True, "settable": True, "current": -5}},
            },
            "multispectral": {
                "sdkAvailable": True,
                "detected": True,
                "available": True,
                "connected": True,
                "opened": self.multispectral_preview_running,
                "streaming": self.multispectral_preview_running,
                "transport": "GigE/DVP2",
                "pixelFormat": "Mono16",
                "frameDtype": "uint16",
                "actual": {
                    "cameraIp": "169.254.25.110",
                    "cameraMac": "B4-61-D3-14-6E-18",
                    "cameraSerial": "GP23400004963",
                    "width": 2048,
                    "height": 1200,
                    "pixelFormat": "Mono16",
                    "frameDtype": "uint16",
                    "exposure": 10000.0,
                    "gain": 1.0,
                    "streamFps": 25.0,
                    "linkSpeedMbps": None,
                    "linkSpeed": "",
                },
                "capabilities": {
                    "triggerMode": "continuous",
                    "exposure": {"min": 1.0, "max": 1000000.0, "step": 1.0, "default": 10000.0},
                    "gain": {"min": 1.0, "max": 16.0, "step": 0.1, "default": 1.0},
                    "supportedPixelFormats": ["Mono8"],
                },
            },
            "preview": {
                "rgb": {"running": self.preview_running, "width": 960, "height": 540, "fps": 12},
                "multispectral": {"running": self.multispectral_preview_running, "width": 960, "height": 540, "fps": 8},
            },
            "settings": self.camera_settings(),
        }

    def camera_settings(self):
        return {
            "version": 1,
            "path": "config/camera_settings.json",
            "exists": True,
            "rgb": {
                "deviceIndex": 1,
                "width": 3840,
                "height": 2160,
                "fps": 25,
                "fourcc": "MJPG",
                "settingsSource": "persistent",
            },
            "multispectral": {
                "deviceStableId": "DSGP23400004963",
                "exposure": 10000.0,
                "gain": 1.0,
                "settingsSource": "persistent",
            },
            "restoreState": {
                "rgb": {"state": "restored", "settingsSource": "persistent"},
                "multispectral": {"state": "restored", "settingsSource": "persistent"},
            },
            "hasCustom": {"rgb": True, "multispectral": True},
            "warnings": [],
        }

    def probe_rgb(self):
        return {
            "passed": True,
            "status": self.status()["rgb"],
            "preview": {"rgb": self.status()["preview"]["rgb"]},
        }

    def apply_rgb_settings(self, payload):
        self.applied_payload = dict(payload)
        return {
            "restartRequired": payload.get("width") != 3840,
            "previewRestarted": False,
            "settingResults": {"exposure": {"accepted": True}},
            "status": self.status()["rgb"],
            "summary": {
                "requestedResolution": f"{payload.get('width')}x{payload.get('height')}",
                "actualResolution": "3840x2160",
                "requestedFps": payload.get("fps"),
                "actualFps": 25,
                "requestedFourcc": payload.get("fourcc"),
                "actualFourcc": "MJPG",
                "matchesRequested": payload.get("width") == 3840,
            },
            "preview": {"rgb": self.status()["preview"]["rgb"]},
        }

    def start_rgb_preview(self, payload=None):
        self.preview_running = True
        return {"status": self.status()["rgb"], "preview": {"rgb": self.status()["preview"]["rgb"]}}

    def stop_rgb_preview(self):
        self.preview_running = False
        return {"status": self.status()["rgb"], "preview": {"rgb": self.status()["preview"]["rgb"]}}

    def rgb_preview_jpeg(self):
        return b"\xff\xd8fake-jpeg\xff\xd9", {
            "contentType": "image/jpeg",
            "previewWidth": 960,
            "previewHeight": 540,
            "sourceShape": (2160, 3840, 3),
        }

    def probe_multispectral(self):
        return {
            "passed": True,
            "status": self.status()["multispectral"],
            "preview": self.status()["preview"],
        }

    def apply_multispectral_settings(self, payload):
        self.multispectral_applied_payload = dict(payload)
        return {
            "settingResults": {
                "exposure": {"requested": payload.get("exposure"), "actual": payload.get("exposure"), "accepted": True},
                "gain": {"requested": payload.get("gain"), "actual": payload.get("gain"), "accepted": True},
            },
            "status": self.status()["multispectral"],
            "summary": {
                "requestedExposure": payload.get("exposure"),
                "actualExposure": payload.get("exposure"),
                "requestedGain": payload.get("gain"),
                "actualGain": payload.get("gain"),
                "pixelFormat": "Mono16",
                "frameDtype": "uint16",
                "matchesRequested": True,
            },
            "preview": self.status()["preview"],
        }

    def save_camera_settings(self, payload):
        self.saved_settings_payload = dict(payload)
        return {"settings": self.camera_settings()}

    def reset_camera_settings(self, payload):
        self.reset_settings_payload = dict(payload)
        return {"settings": self.camera_settings(), "applied": {}}

    def restore_camera_settings(self, payload):
        self.restore_settings_payload = dict(payload)
        return {"restored": {"rgb": self.status()["rgb"], "multispectral": self.status()["multispectral"]}, "settings": self.camera_settings()}

    def migrate_legacy_camera_settings(self, payload):
        self.legacy_settings_payload = dict(payload)
        return {"settings": self.camera_settings(), "migration": {"migrated": False, "reason": "backend_settings_exists"}}

    def start_multispectral_preview(self, payload=None):
        self.multispectral_preview_running = True
        return {"status": self.status()["multispectral"], "preview": self.status()["preview"]}

    def stop_multispectral_preview(self):
        self.multispectral_preview_running = False
        return {"status": self.status()["multispectral"], "preview": self.status()["preview"]}

    def multispectral_preview_jpeg(self):
        return b"\xff\xd8fake-mono-jpeg\xff\xd9", {
            "contentType": "image/jpeg",
            "previewWidth": 960,
            "previewHeight": 540,
            "sourceShape": (1200, 2048),
            "sourceDtype": "uint16",
            "pixelFormat": "Mono16",
            "frameMin": 0.0,
            "frameMax": 4095.0,
            "frameMean": 32.5,
            "frameId": 7,
            "sourceTimestamp": 123456,
            "captureDurationMs": 4.5,
            "resizeDurationMs": 2.0,
            "jpegEncodeDurationMs": 3.0,
            "serverTotalMs": 9.5,
            "measuredPreviewFps": 12.0,
            "droppedFrames": 1,
            "lowLatency": True,
            "previewEncoder": "opencv",
        }

    def evaluate_multispectral_focus(self, payload=None):
        payload = payload or {}
        return {
            "status": "ok",
            "classification": "unknown",
            "focusScore": 123.5,
            "metrics": {
                "tenengrad": 123.5,
                "laplacianVariance": 42.0,
                "edgeDensity": 0.25,
            },
            "roi": {"mode": payload.get("roiMode") or "center", "x": 10, "y": 10, "width": 100, "height": 100},
            "frame": {"width": 2048, "height": 1200, "dtype": "uint16", "pixelFormat": "Mono16"},
            "thresholds": {"blurryBelow": None, "sharpAbove": None, "provisional": True},
            "bandId": payload.get("bandId"),
            "wavelengthNm": payload.get("wavelengthNm"),
            "error": "",
            "capture": {"previewWasRunning": self.multispectral_preview_running, "openedForCapture": not self.multispectral_preview_running, "streaming": True},
            "preview": self.status()["preview"],
        }


class FakeCalibrationCoordinator:
    def __init__(self):
        self.calls = []

    def snapshot(self):
        return {"state": "idle", "status": "idle", "progress": 0, "metadata": {}}

    def run_dark_reference_capture(self, **kwargs):
        self.calls.append(("dark", kwargs))
        return {
            "state": "completed",
            "mode": "dark_reference",
            "metadata": {
                "captureType": "dark",
                "calibrationId": kwargs.get("calibration_id") or "cal-api",
                "calibrationComplete": False,
            },
        }

    def run_white_reference_capture(self, **kwargs):
        self.calls.append(("white", kwargs))
        return {
            "state": "completed",
            "mode": "white_reference",
            "metadata": {
                "captureType": "white",
                "calibrationId": kwargs.get("calibration_id") or "cal-api",
                "calibrationComplete": False,
            },
        }

    def run_sample_multiview_capture(self, **kwargs):
        self.calls.append(("sample_multiview", kwargs))
        return {
            "state": "completed",
            "mode": "sample_multiview",
            "metadata": {
                "captureType": "sample",
                "calibrationId": kwargs.get("calibration_id"),
                "multiViewCaptureComplete": True,
                "views": [{"viewId": "view_000", "sample_id": kwargs.get("sample_id"), "viewComplete": True}],
            },
        }

    def request_cancel(self):
        return {"state": "cancelled", "status": "cancelled", "progress": 0}


class FakeDeviceManager:
    def __init__(self):
        self.connected = False
        self.port = ""
        self.self_test_motion = None
        self.emergency_stopped = False
        self.fan_on = False
        self.led3_on = False
        self.tungsten1_on = False
        self.tungsten2_on = False
        self.actuator_busy = False
        self.wheel_moves = []
        self.camera_manager = FakeCameraManager()
        self.capture_coordinator = FakeCalibrationCoordinator()
        self.discovery_candidates = [
            DeviceCandidate(
                "serial",
                "usb:VID_0483&PID_5740:CTRL-A",
                "STM32 Controller",
                "COM3",
                metadata={"protocolMatched": True, "vid": "0483", "pid": "5740", "serialNumber": "CTRL-A"},
            ),
            DeviceCandidate(
                "uvc",
                None,
                "USB RGB Camera",
                {"backend": "DSHOW", "deviceIndex": 2},
                metadata={"frameReadable": True, "width": 3840, "height": 2160, "fps": 25, "fourcc": "MJPG"},
            ),
            DeviceCandidate(
                "dvp2",
                "GP23400004963",
                "MGV231M-H2",
                {"serial": "GP23400004963", "index": 0},
                metadata={"mac": "B4-61-D3-14-6E-18", "ip": "169.254.25.110"},
            ),
        ]
        self.registry_path = Path(tempfile.mkdtemp(prefix="fta_device_registry_")) / "hardware_profile.json"
        self.registry = DeviceRegistry(self.registry_path)

    def _status(self):
        return {
            "connected": self.connected,
            "port": self.port,
            "fanOn": self.fan_on,
            "fanDuty": 100 if self.fan_on else 0,
            "door": "closed" if self.connected else "unknown",
            "wheelPosition": 0 if self.connected else None,
            "wheelHomed": self.connected,
            "rgbLed1On": False,
            "rgbLed2On": False,
            "rgbLed3On": self.led3_on,
            "ledMask": (0x01 if self.tungsten1_on else 0) | (0x02 if self.tungsten2_on else 0) | (0x04 if self.led3_on else 0),
            "led3Duty": 100 if self.led3_on else 0,
            "tungsten1On": self.tungsten1_on,
            "tungsten2On": self.tungsten2_on,
            "tungsten1Duty": 100 if self.tungsten1_on else 0,
            "tungsten2Duty": 100 if self.tungsten2_on else 0,
            "tungstenAutoOff": {},
            "tungstenLastError": "",
            "wheelPositionDeg": 0.0 if self.connected else None,
            "wheelTargetDeg": 0.0 if self.connected else None,
            "wheelMotorState": "idle" if self.connected else "unknown",
            "statusRevision": 1 if self.connected else None,
            "statusReceivedMonotonic": 1.0 if self.connected else None,
            "actuatorBusy": self.actuator_busy,
            "errorCode": 0 if self.connected else None,
            "emergencyStopped": self.emergency_stopped,
            "sampleStage": {
                "connected": False,
                "available": False,
                "homed": False,
                "currentAngleDeg": None,
                "targetAngleDeg": None,
                "moving": False,
                "lastCommand": "",
                "lastError": "SAMPLE_STAGE_PROTOCOL_UNKNOWN",
                "fault": "SAMPLE_STAGE_PROTOCOL_UNKNOWN",
                "hardwareMode": "hardware",
                "implemented": False,
                "protocolKnown": False,
                "positionFeedbackSupported": False,
            },
            "cameras": self.camera_manager.status(),
        }

    def list_ports(self):
        return [{"device": "COM3", "description": "STM32", "hwid": "USB"}]

    def discover_devices(self):
        return {
            "ok": True,
            "candidates": [candidate.to_dict() for candidate in self.discovery_candidates],
            "byKind": {
                "serial": [self.discovery_candidates[0].to_dict()],
                "uvc": [self.discovery_candidates[1].to_dict()],
                "dvp2": [self.discovery_candidates[2].to_dict()],
            },
        }

    def device_bindings(self, discovery=None):
        return self.registry.snapshot(self.discovery_candidates)

    def bind_device(self, payload):
        binding = self.registry.bind_from_payload(payload, self.discovery_candidates)
        return {
            "binding": binding.to_dict(),
            "bindings": self.registry.snapshot(self.discovery_candidates),
        }

    def connect(self, port):
        self.connected = True
        self.port = port
        return self._status()

    def disconnect(self):
        self.connected = False
        self.port = ""
        self.emergency_stopped = False

    def status(self):
        return self._status()

    def self_test(self, include_motion=False):
        self.self_test_motion = include_motion
        return {
            "passed": True,
            "includeMotion": False,
            "motionRequestedIgnored": bool(include_motion),
            "status": self._status(),
            "checks": {
                "controller": {"status": "passed", "label": "STM32 控制器", "message": "PING 通过"},
                "filterWheel": {"status": "passed", "label": "滤光轮", "message": "位置: 0"},
                "rgbCamera": {"status": "not_connected", "label": "RGB 相机", "message": "RGB 相机未连接"},
                "multispectralCamera": {"status": "passed", "label": "多光谱相机", "message": "已连接 2048x1200"},
                "calibration": {"status": "manual_required", "label": "标定状态", "message": "当前需要操作员人工确认"},
            },
        }

    def independent_device_check(self):
        return {
            "passed": False,
            "includeMotion": False,
            "independentDomains": True,
            "status": self._status(),
            "checks": {
                "controller": {"status": "not_connected", "label": "STM32 控制器", "message": "请选择串口并连接 STM32"},
                "door": {"status": "not_connected", "label": "升降门", "message": "STM32 未连接，未读取门状态"},
                "fan": {"status": "not_connected", "label": "风扇", "message": "风扇未开启"},
                "filterWheel": {"status": "not_connected", "label": "滤光轮", "message": "尚未确认 HOME"},
                "rgbCamera": {"status": "passed", "label": "RGB 相机", "message": "已连接 3840x2160", "cameraStatus": self.camera_manager.status()["rgb"]},
                "multispectralCamera": {"status": "passed", "label": "多光谱相机", "message": "已连接 2048x1200", "cameraStatus": self.camera_manager.status()["multispectral"]},
                "light": {"status": "not_connected", "label": "光源控制", "message": "STM32 未连接，未读取光源输出"},
                "calibration": {"status": "manual_required", "label": "标定状态", "message": "当前需要操作员人工确认"},
            },
        }

    def emergency_stop(self):
        self.emergency_stopped = True
        return self._status()

    def fault_clear(self):
        raise RuntimeError("当前 STM32 firmware 不支持远程 Fault Clear")

    def set_fan(self, enabled):
        self.fan_on = bool(enabled)
        return {"commandAccepted": True, "fanOn": self.fan_on, "fanDuty": 100 if self.fan_on else 0, "status": self._status()}

    def set_led3(self, enabled):
        self.led3_on = bool(enabled)
        return {"commandAccepted": True, "ledMask": self._status()["ledMask"], "led3Duty": 100 if self.led3_on else 0, "status": self._status()}

    def set_tungsten(self, channel, enabled, duration_ms=None, operator_confirmed_safety=False):
        if bool(enabled) and operator_confirmed_safety is not True:
            raise RuntimeError("operator_confirmation_required")
        if int(channel) == 1:
            self.tungsten1_on = bool(enabled)
        elif int(channel) == 2:
            self.tungsten2_on = bool(enabled)
        else:
            raise ValueError("channel must be 1 or 2")
        status = self._status()
        return {
            "ok": True,
            "commandAccepted": True,
            "channel": int(channel),
            "enabled": bool(enabled),
            "durationMs": duration_ms if enabled else None,
            "autoOffScheduled": bool(enabled),
            "requestedMask": status["ledMask"],
            "confirmedMask": status["ledMask"],
            "statusFresh": True,
            "errorCode": status["errorCode"],
            "message": "ok",
            "status": status,
        }

    def tungsten_all_off(self, emergency=False):
        self.tungsten1_on = False
        self.tungsten2_on = False
        status = self._status()
        return {
            "ok": True,
            "commandAccepted": True,
            "emergency": bool(emergency),
            "requestedMask": status["ledMask"],
            "confirmedMask": status["ledMask"],
            "statusFresh": True,
            "safetyWarning": "",
            "status": status,
        }

    def actuator_extend(self, duration_ms=None):
        self.actuator_busy = True
        return {"commandAccepted": True, "action": "extend", "durationMs": duration_ms or 1000, "autoStopScheduled": True, "motionCompleted": False, "endpointFeedbackVerified": False, "status": self._status()}

    def actuator_retract(self, duration_ms=None):
        self.actuator_busy = True
        return {"commandAccepted": True, "action": "retract", "durationMs": duration_ms or 1000, "autoStopScheduled": True, "motionCompleted": False, "endpointFeedbackVerified": False, "status": self._status()}

    def actuator_stop(self):
        self.actuator_busy = False
        return {"commandAccepted": True, "action": "stop", "motionCompleted": False, "endpointFeedbackVerified": False, "status": self._status()}

    def move_filter_wheel(self, direction, slots):
        self.wheel_moves.append((direction, slots))
        return {
            "direction": direction,
            "slots": slots,
            "slotDelta": slots if direction == "counterclockwise" else -slots,
            "degrees": (slots if direction == "counterclockwise" else -slots) * 22.5,
            "motionCompleted": True,
            "failureReason": "",
            "command": {"ok": True, "motionCompleted": True, "failureReason": "", "statusFresh": True},
            "status": self._status(),
        }

    def stop_filter_wheel(self):
        return {"commandAccepted": True, "motionCompleted": False, "status": self._status()}

    def set_filter_wheel_origin(self, operator_confirmed_aligned):
        if not operator_confirmed_aligned:
            raise ValueError("operatorConfirmedAligned must be true")
        return {"commandAccepted": True, "originEstablished": True, "automaticHoming": False, "message": "逻辑零点已建立", "status": self._status()}

    def sample_stage_status(self):
        return self._status()["sampleStage"]

    def sample_stage_home(self):
        raise UnsupportedCapabilityError("SAMPLE_STAGE_PROTOCOL_UNKNOWN: sample stage hardware adapter is not implemented")

    def sample_stage_move_absolute(self, angle_deg, *, direction="CW"):
        raise UnsupportedCapabilityError("SAMPLE_STAGE_PROTOCOL_UNKNOWN: sample stage hardware adapter is not implemented")

    def sample_stage_move_relative(self, delta_deg, *, direction="CW"):
        raise UnsupportedCapabilityError("SAMPLE_STAGE_PROTOCOL_UNKNOWN: sample stage hardware adapter is not implemented")

    def sample_stage_stop(self):
        raise UnsupportedCapabilityError("SAMPLE_STAGE_PROTOCOL_UNKNOWN: sample stage hardware adapter is not implemented")

    def capture_status(self):
        return {"status": "not_ready", "progress": 0, "message": "完整真实采集协调器尚未接入"}

    def capture_readiness(self, payload=None):
        payload = payload or {}
        return {
            "ready": False,
            "trueCapturePrepared": False,
            "captureMode": payload.get("captureMode") or "single_view",
            "blockingReasons": [{"code": "FAKE_TRUE_CAPTURE_NOT_READY", "message": "fake manager keeps true capture guarded"}],
            "warnings": [],
            "capabilities": {
                "singleViewReady": False,
                "multiViewReady": False,
                "rgbReady": True,
                "multispectralReady": True,
                "filterWheelReady": True,
                "sampleStageReady": False,
            },
            "singleView": {"ready": False, "blockingReasons": []},
            "multiView": {"ready": False, "blockingReasons": [{"code": "SAMPLE_STAGE_PROTOCOL_UNKNOWN", "message": "样品台协议未知"}]},
            "plan": dict(payload),
        }

    def start_capture(self, sample_id="", payload=None):
        raise CameraIntegrationRequired("完整真实采集协调器尚未接入，不能开始真实采集")

    def cancel_capture(self):
        return {"status": "cancelled", "progress": 0, "message": "采集已取消"}


class BackendDeviceApiTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="fta_device_api_"))
        self.static_dir = self.root / "static"
        self.app_dir = self.root / "app"
        self.outputs_dir = self.root / "outputs"
        self.static_dir.mkdir()
        self.app_dir.mkdir()
        self.outputs_dir.mkdir()
        self.device = FakeDeviceManager()
        handler = create_handler(
            self.static_dir,
            self.outputs_dir,
            self.app_dir,
            JobStore(),
            SessionState(),
            device_manager=self.device,
        )
        self.client = InProcessHttpClient(handler)
        self.base_url = "http://127.0.0.1"

    def tearDown(self):
        return None

    def get_json(self, path: str) -> dict:
        return self.request("GET", path, parse_json=True)["json"]

    def get_response(self, path: str):
        return self.request("GET", path, parse_json=False)["response"]

    def post_json(self, path: str, payload: dict | None = None) -> dict:
        data = json.dumps(payload or {}).encode("utf-8")
        return self.request("POST", path, body=data, headers={"Content-Type": "application/json"}, parse_json=True)["json"]

    def request(
        self,
        method: str,
        path: str,
        body: bytes | None = None,
        headers: dict | None = None,
        parse_json: bool = True,
    ) -> dict:
        response = self.client.request(method, path, body=body, headers=headers)
        try:
            raw = response.read()
            wrapped = _BufferedResponse(raw, response.status, response.reason, response.headers)
            if response.status >= 400:
                raise urllib.error.HTTPError(
                    self.base_url + path,
                    response.status,
                    response.reason,
                    response.headers,
                    io.BytesIO(raw),
                )
            parsed = json.loads(raw.decode("utf-8")) if parse_json else None
            return {"json": parsed, "response": wrapped}
        finally:
            response.close()

    def test_status_and_connect_expose_hardware_state(self):
        status = self.get_json("/api/status")
        self.assertIn("device", status)
        self.assertIn("cameras", status)
        self.assertFalse(status["device"]["connected"])
        self.assertEqual(status["cameras"]["rgb"]["transport"], "UVC/DirectShow")
        self.assertEqual(status["cameras"]["multispectral"]["transport"], "GigE/DVP2")

        ports = self.get_json("/api/device/ports")
        self.assertEqual(ports["ports"][0]["device"], "COM3")

        connected = self.post_json("/api/device/connect", {"port": "COM3"})
        self.assertTrue(connected["device"]["connected"])
        self.assertEqual(connected["device"]["port"], "COM3")

        self_test = self.post_json("/api/device/self-test", {"includeMotion": True})
        self.assertTrue(self_test["result"]["passed"])
        self.assertTrue(self.device.self_test_motion)
        self.assertFalse(self_test["result"]["includeMotion"])
        self.assertTrue(self_test["result"]["motionRequestedIgnored"])
        self.assertEqual(self_test["result"]["checks"]["controller"]["status"], "passed")
        self.assertEqual(self_test["result"]["checks"]["rgbCamera"]["status"], "not_connected")
        self.assertEqual(self_test["result"]["checks"]["multispectralCamera"]["status"], "passed")
        self.assertEqual(self_test["result"]["checks"]["calibration"]["status"], "manual_required")

        independent = self.post_json("/api/device/check", {})
        self.assertTrue(independent["result"]["independentDomains"])
        self.assertEqual(independent["result"]["checks"]["controller"]["status"], "not_connected")
        self.assertEqual(independent["result"]["checks"]["rgbCamera"]["status"], "passed")
        self.assertEqual(independent["result"]["checks"]["multispectralCamera"]["status"], "passed")

    def test_stm32_web_hardware_control_apis(self):
        self.post_json("/api/device/connect", {"port": "COM3"})

        fan = self.post_json("/api/device/fan", {"enabled": True})["result"]
        self.assertTrue(fan["fanOn"])
        self.assertEqual(fan["fanDuty"], 100)

        led = self.post_json("/api/device/led", {"channel": 3, "enabled": True})["result"]
        self.assertEqual(led["ledMask"], 0x04)
        self.assertEqual(led["led3Duty"], 100)

        tungsten = self.post_json("/api/device/tungsten", {
            "channel": 1,
            "enabled": True,
            "durationMs": 5000,
            "operatorConfirmedSafety": True,
        })["result"]
        self.assertTrue(tungsten["autoOffScheduled"])
        self.assertEqual(tungsten["channel"], 1)
        self.assertEqual(tungsten["confirmedMask"], 0x05)
        tungsten_off = self.post_json("/api/device/tungsten", {"channel": 1, "enabled": False})["result"]
        self.assertEqual(tungsten_off["confirmedMask"], 0x04)
        all_off = self.post_json("/api/device/tungsten/all-off", {})["result"]
        self.assertEqual(all_off["confirmedMask"], 0x04)

        actuator = self.post_json("/api/device/actuator", {"action": "extend", "durationMs": 1000})["result"]
        self.assertTrue(actuator["autoStopScheduled"])
        self.assertFalse(actuator["motionCompleted"])
        self.assertFalse(actuator["endpointFeedbackVerified"])
        stopped = self.post_json("/api/device/actuator", {"action": "stop"})["result"]
        self.assertEqual(stopped["action"], "stop")

        wheel = self.post_json("/api/device/wheel/move-relative", {"direction": "clockwise", "slots": 1})["result"]
        self.assertEqual(wheel["slotDelta"], -1)
        self.assertTrue(wheel["command"]["statusFresh"])
        self.assertEqual(self.device.wheel_moves[-1], ("clockwise", 1))
        origin = self.post_json("/api/device/wheel/set-origin", {"operatorConfirmedAligned": True})["result"]
        self.assertTrue(origin["originEstablished"])
        self.assertFalse(origin["automaticHoming"])

        with self.assertRaises(urllib.error.HTTPError) as context:
            self.post_json("/api/device/led", {"channel": 1, "enabled": True})
        self.assertEqual(context.exception.code, 400)

    def test_sample_stage_api_reports_protocol_unknown_boundary(self):
        status = self.get_json("/api/device/sample-stage/status")["sampleStage"]
        self.assertFalse(status["available"])
        self.assertEqual(status["lastError"], "SAMPLE_STAGE_PROTOCOL_UNKNOWN")

        with self.assertRaises(urllib.error.HTTPError) as context:
            self.post_json("/api/device/sample-stage/home", {})
        self.assertEqual(context.exception.code, 409)
        body = json.loads(context.exception.fp.read().decode("utf-8"))
        self.assertIn("SAMPLE_STAGE_PROTOCOL_UNKNOWN", body["message"])

    def test_capture_start_reports_camera_integration_gap(self):
        readiness = self.get_json("/api/capture/readiness?captureMode=single_view")["readiness"]
        self.assertFalse(readiness["ready"])
        self.assertEqual(readiness["blockingReasons"][0]["code"], "FAKE_TRUE_CAPTURE_NOT_READY")

        with self.assertRaises(urllib.error.HTTPError) as context:
            self.post_json("/api/capture/start", {"sampleId": "S001"})

        self.assertEqual(context.exception.code, 409)

    def test_camera_settings_api_apply_and_preview(self):
        status = self.get_json("/api/camera/status")
        self.assertEqual(status["cameras"]["rgb"]["transport"], "UVC/DirectShow")
        self.assertEqual(status["cameras"]["multispectral"]["transport"], "GigE/DVP2")
        self.assertTrue(status["cameras"]["rgb"]["detected"])
        self.assertFalse(status["cameras"]["rgb"]["opened"])

        probed = self.post_json("/api/camera/rgb/probe")
        self.assertTrue(probed["result"]["passed"])
        self.assertTrue(probed["result"]["status"]["available"])

        applied = self.post_json("/api/camera/rgb/apply-settings", {
            "deviceIndex": 1,
            "width": 3840,
            "height": 2160,
            "fps": 25,
            "fourcc": "MJPG",
            "exposure": -6,
        })
        self.assertTrue(applied["result"]["summary"]["matchesRequested"])
        self.assertEqual(self.device.camera_manager.applied_payload["exposure"], -6)

        started = self.post_json("/api/camera/rgb/preview/start", {"width": 960, "height": 540, "fps": 12})
        self.assertTrue(started["result"]["preview"]["rgb"]["running"])
        with self.get_response("/api/camera/rgb/preview-frame") as response:
            self.assertEqual(response.headers["Content-Type"], "image/jpeg")
            self.assertEqual(response.headers["X-Preview-Width"], "960")
            self.assertTrue(response.read().startswith(b"\xff\xd8"))
        stopped = self.post_json("/api/camera/rgb/preview/stop")
        self.assertFalse(stopped["result"]["preview"]["rgb"]["running"])
        self.assertTrue(stopped["result"]["status"]["detected"])
        self.assertTrue(stopped["result"]["status"]["available"])
        self.assertFalse(stopped["result"]["status"]["opened"])

        multi_probed = self.post_json("/api/camera/multispectral/probe")
        self.assertTrue(multi_probed["result"]["passed"])
        multi_applied = self.post_json("/api/camera/multispectral/apply-settings", {
            "exposure": 12000,
            "gain": 1.5,
        })
        self.assertTrue(multi_applied["result"]["summary"]["matchesRequested"])
        self.assertEqual(self.device.camera_manager.multispectral_applied_payload["exposure"], 12000)
        self.assertEqual(self.device.camera_manager.multispectral_applied_payload["gain"], 1.5)
        multi_started = self.post_json("/api/camera/multispectral/preview/start", {"width": 960, "height": 540, "fps": 8})
        self.assertTrue(multi_started["result"]["preview"]["multispectral"]["running"])
        with self.get_response("/api/camera/multispectral/preview-frame") as response:
            self.assertEqual(response.headers["Content-Type"], "image/jpeg")
            self.assertEqual(response.headers["X-Source-Dtype"], "uint16")
            self.assertEqual(response.headers["X-Frame-Mean"], "32.5")
            self.assertEqual(response.headers["X-Frame-Id"], "7")
            self.assertEqual(response.headers["X-Source-Timestamp"], "123456")
            self.assertEqual(response.headers["X-Capture-Duration-Ms"], "4.500")
            self.assertEqual(response.headers["X-Resize-Duration-Ms"], "2.000")
            self.assertEqual(response.headers["X-Jpeg-Encode-Duration-Ms"], "3.000")
            self.assertEqual(response.headers["X-Server-Total-Ms"], "9.500")
            self.assertEqual(response.headers["X-Measured-Preview-Fps"], "12.000")
            self.assertEqual(response.headers["X-Dropped-Frames"], "1")
            self.assertEqual(response.headers["X-Low-Latency-Preview"], "1")
            self.assertEqual(response.headers["X-Preview-Encoder"], "opencv")
            self.assertTrue(response.read().startswith(b"\xff\xd8"))
        multi_stopped = self.post_json("/api/camera/multispectral/preview/stop")
        self.assertFalse(multi_stopped["result"]["preview"]["multispectral"]["running"])

    def test_camera_settings_persistence_routes(self):
        settings = self.get_json("/api/camera/settings")
        self.assertEqual(settings["settings"]["rgb"]["settingsSource"], "persistent")

        saved = self.post_json("/api/camera/settings/save", {
            "rgb": {"deviceIndex": 1, "width": 3840, "height": 2160, "fps": 25, "fourcc": "MJPG"},
            "multispectral": {"exposure": 12000, "gain": 1.5},
        })
        self.assertEqual(saved["result"]["settings"]["multispectral"]["deviceStableId"], "DSGP23400004963")
        self.assertEqual(self.device.camera_manager.saved_settings_payload["multispectral"]["gain"], 1.5)

        restored = self.post_json("/api/camera/settings/restore", {"section": "multispectral", "force": True})
        self.assertEqual(restored["result"]["settings"]["restoreState"]["multispectral"]["state"], "restored")
        self.assertEqual(self.device.camera_manager.restore_settings_payload["section"], "multispectral")

        reset = self.post_json("/api/camera/settings/reset", {"section": "rgb", "apply": True})
        self.assertIn("settings", reset["result"])
        self.assertTrue(self.device.camera_manager.reset_settings_payload["apply"])

        migrated = self.post_json("/api/camera/settings/migrate-legacy", {"rgb": {"deviceIndex": 2}})
        self.assertEqual(migrated["result"]["migration"]["reason"], "backend_settings_exists")
        self.assertEqual(self.device.camera_manager.legacy_settings_payload["rgb"]["deviceIndex"], 2)

    def test_multispectral_focus_evaluate_api_and_capture_start_guard(self):
        self.post_json("/api/camera/multispectral/preview/start", {"width": 960, "height": 540, "fps": 8})

        result = self.post_json("/api/camera/multispectral/focus/evaluate", {
            "roiMode": "center",
            "bandId": "A520",
            "wavelengthNm": 520,
        })["result"]

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["classification"], "unknown")
        self.assertEqual(result["focusScore"], 123.5)
        self.assertEqual(result["metrics"]["tenengrad"], 123.5)
        self.assertEqual(result["frame"]["dtype"], "uint16")
        self.assertEqual(result["bandId"], "A520")
        self.assertEqual(result["wavelengthNm"], 520)
        self.assertTrue(result["capture"]["previewWasRunning"])
        with self.assertRaises(urllib.error.HTTPError) as context:
            self.post_json("/api/capture/start", {"sampleId": "S001"})
        self.assertEqual(context.exception.code, 409)

    def test_calibration_capture_api_is_explicit_and_capture_start_remains_guarded(self):
        with tempfile.TemporaryDirectory(prefix="fta_cal_api_") as tmp:
            dark = self.post_json("/api/capture/calibration/dark", {
                "sampleId": "S001",
                "outputDir": tmp,
                "calibrationId": "cal-api",
                "operatorConfirmed": True,
                "settlingMs": 0,
                "bandPlan": [{"bandId": "A520", "wheelPosition": 2, "wavelengthNm": 520, "exposureUs": 11000, "gain": 1.1}],
            })["capture"]
            white = self.post_json("/api/capture/calibration/white", {
                "sampleId": "S001",
                "outputDir": tmp,
                "calibrationId": "cal-api",
                "operatorConfirmed": True,
                "settlingMs": 0,
                "tungstenMask": 1,
                "bandPlan": [{"bandId": "A520", "wheelPosition": 2, "wavelengthNm": 520, "exposureUs": 11000, "gain": 1.1}],
            })["capture"]

        self.assertEqual(dark["metadata"]["captureType"], "dark")
        self.assertEqual(white["metadata"]["captureType"], "white")
        self.assertEqual(self.device.capture_coordinator.calls[0][0], "dark")
        self.assertEqual(self.device.capture_coordinator.calls[0][1]["operator_confirmed"], True)
        self.assertEqual(self.device.capture_coordinator.calls[1][0], "white")
        self.assertEqual(self.device.capture_coordinator.calls[1][1]["tungsten_mask"], 1)

        with self.assertRaises(urllib.error.HTTPError) as context:
            self.post_json("/api/capture/start", {"sampleId": "S001"})
        self.assertEqual(context.exception.code, 409)

    def test_sample_multiview_capture_api_is_protected_dev_entry_and_start_remains_guarded(self):
        with tempfile.TemporaryDirectory(prefix="fta_multiview_api_") as tmp:
            capture = self.post_json("/api/capture/sample-multiview", {
                "sampleId": "S001",
                "outputDir": tmp,
                "calibrationId": "cal-api",
                "sampleStageMode": "simulation",
                "sampleStageSettlingMs": 0,
                "settlingMs": 0,
                "returnHome": True,
                "sampleRotation": {"enabled": False},
                "bandPlan": [{"bandId": "A520", "wheelPosition": 2, "wavelengthNm": 520}],
            })["capture"]

        self.assertEqual(capture["mode"], "sample_multiview")
        self.assertTrue(capture["metadata"]["multiViewCaptureComplete"])
        self.assertEqual(self.device.capture_coordinator.calls[-1][0], "sample_multiview")
        kwargs = self.device.capture_coordinator.calls[-1][1]
        self.assertEqual(kwargs["sample_id"], "S001")
        self.assertEqual(kwargs["calibration_id"], "cal-api")
        self.assertEqual(kwargs["sample_stage_mode"], "simulation")
        self.assertTrue(kwargs["return_home"])

        with self.assertRaises(urllib.error.HTTPError) as context:
            self.post_json("/api/capture/start", {"sampleId": "S001"})
        self.assertEqual(context.exception.code, 409)

    def test_device_discovery_and_binding_api(self):
        discovered = self.get_json("/api/devices/discover")

        self.assertTrue(discovered["ok"])
        self.assertEqual(len(discovered["discovery"]["byKind"]["serial"]), 1)
        self.assertEqual(discovered["discovery"]["byKind"]["serial"][0]["stableId"], "usb:VID_0483&PID_5740:CTRL-A")
        self.assertIsNone(discovered["discovery"]["byKind"]["uvc"][0]["stableId"])

        bound = self.post_json("/api/devices/bind", {
            "role": DeviceRole.RGB_CAMERA,
            "kind": "uvc",
            "stableId": None,
            "connection": {"backend": "DSHOW", "deviceIndex": 2},
        })

        binding = bound["result"]["binding"]
        self.assertEqual(binding["role"], DeviceRole.RGB_CAMERA)
        self.assertIsNone(binding["stableId"])
        self.assertEqual(binding["lastDeviceIndex"], 2)
        self.assertEqual(bound["result"]["bindings"]["matches"][DeviceRole.RGB_CAMERA]["method"], "lastDeviceIndexVerified")

        bindings = self.get_json("/api/devices/bindings")
        self.assertIn(DeviceRole.RGB_CAMERA, bindings["bindings"]["bindings"])

    def test_camera_settings_ui_separates_rgb_and_multispectral_controls(self):
        html = (Path(__file__).parents[1] / "index.html").read_text(encoding="utf-8")
        self.assertIn("data-camera-settings-tab=\"rgb\"", html)
        self.assertIn("data-camera-settings-tab=\"multispectral\"", html)
        self.assertIn("id=\"rgbLivePreview\"", html)
        self.assertIn("id=\"multispectralLivePreview\"", html)
        self.assertIn("标定与几何参数", html)
        multispectral_section = html.split('id="multispectralCameraSettingsPanel"', 1)[1].split("</article>", 1)[0]
        self.assertIn("GigE / RJ45", multispectral_section)
        self.assertIn("DVP2", multispectral_section)
        self.assertIn("PixelFormat", multispectral_section)
        self.assertIn("id=\"applyMultispectralCameraSettings\"", multispectral_section)
        self.assertIn("id=\"applySaveMultispectralCameraSettings\"", multispectral_section)
        self.assertIn("id=\"restoreSavedMultispectralCameraSettings\"", multispectral_section)
        self.assertIn("id=\"multispectralExposureInput\"", multispectral_section)
        self.assertIn("id=\"multispectralGainInput\"", multispectral_section)
        self.assertNotIn("White Balance", multispectral_section)
        self.assertNotIn("白平衡", multispectral_section)

    def test_camera_settings_ui_uses_probe_endpoint_and_relative_camera_urls(self):
        app_js = (Path(__file__).parents[1] / "app.js").read_text(encoding="utf-8")
        html = (Path(__file__).parents[1] / "index.html").read_text(encoding="utf-8")

        self.assertIn("/api/camera/rgb/probe", app_js)
        self.assertIn("/api/camera/multispectral/probe", app_js)
        self.assertIn("/api/devices/discover", app_js)
        self.assertIn("/api/devices/bind", app_js)
        self.assertIn("/api/device/check", app_js)
        self.assertIn("function $$", app_js)
        self.assertIn("document.querySelectorAll(selector)", app_js)
        self.assertIn("id=\"refreshDeviceDiscovery\"", html)
        self.assertIn("data-device-role=\"RGB_CAMERA\"", html)
        self.assertIn("id=\"cameraRgbDeviceSelect\"", html)
        self.assertIn("id=\"cameraMultispectralDeviceSelect\"", html)
        self.assertIn("id=\"multispectralHostAdapter\"", html)
        self.assertIn("/api/camera/multispectral/apply-settings", app_js)
        self.assertIn("/api/camera/settings", app_js)
        self.assertIn("/api/camera/settings/save", app_js)
        self.assertIn("/api/camera/settings/restore", app_js)
        self.assertIn("/api/camera/settings/reset", app_js)
        self.assertIn("/api/camera/settings/migrate-legacy", app_js)
        self.assertIn("/api/camera/multispectral/preview-frame", app_js)
        self.assertIn("/api/camera/multispectral/focus/evaluate", app_js)
        self.assertIn("id=\"multispectralFocusReadout\"", html)
        self.assertIn("id=\"startMultispectralFocus\"", html)
        self.assertIn('addEventListener("click", probeRgbCamera)', app_js)
        self.assertIn('addEventListener("click", probeMultispectralCamera)', app_js)
        self.assertIn('addEventListener("click", () => applyMultispectralCameraSettings())', app_js)
        self.assertNotIn("http://127.0.0.1", app_js)
        self.assertNotIn("http://localhost", app_js)

    def test_stm32_web_ui_exposes_real_controls_not_fake_hardware_logs(self):
        app_js = (Path(__file__).parents[1] / "app.js").read_text(encoding="utf-8")
        html = (Path(__file__).parents[1] / "index.html").read_text(encoding="utf-8")

        self.assertIn("id=\"wheelCounterclockwise\"", html)
        self.assertIn("id=\"wheelClockwise\"", html)
        self.assertIn("id=\"wheelSetOrigin\"", html)
        self.assertIn("id=\"actuatorExtend\"", html)
        self.assertIn("id=\"led3OnButton\"", html)
        self.assertIn("id=\"tungsten1OnButton\"", html)
        self.assertIn("id=\"tungsten2OnButton\"", html)
        self.assertIn("id=\"tungstenAllOffButton\"", html)
        self.assertIn("id=\"sampleStageHome\"", html)
        self.assertIn("id=\"sampleStageMovePos30\"", html)
        self.assertIn("id=\"startTrueCapture\"", html)
        self.assertIn("id=\"trueCaptureMode\"", html)
        self.assertIn("data-sample-stage-angle=\"90\"", html)
        self.assertIn("/api/device/wheel/move-relative", app_js)
        self.assertIn("/api/device/actuator", app_js)
        self.assertIn("/api/device/fan", app_js)
        self.assertIn("/api/device/led", app_js)
        self.assertIn("/api/device/tungsten", app_js)
        self.assertIn("/api/device/tungsten/all-off", app_js)
        self.assertIn("/api/device/sample-stage/status", app_js)
        self.assertIn("/api/device/sample-stage/move-absolute", app_js)
        self.assertIn("/api/capture/readiness", app_js)
        self.assertIn("/api/capture/start", app_js)
        self.assertIn("协议未知；禁止复用滤光轮电机", app_js)
        self.assertNotIn("平台正转", html)
        self.assertNotIn("平台反转", html)
        self.assertNotIn("升降复位", html)
        self.assertNotIn("hardwareMotionSelfTest", html)
        self.assertNotIn("hardwareMotionSelfTest", app_js)
        self.assertNotIn("manual_stm32_test.py", app_js)


if __name__ == "__main__":
    unittest.main()
