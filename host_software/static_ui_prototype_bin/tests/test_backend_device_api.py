from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from backend_server import JobStore, SessionState, create_handler
from device_manager import CameraIntegrationRequired


class FakeCameraManager:
    def __init__(self):
        self.applied_payload = None
        self.multispectral_applied_payload = None
        self.preview_running = False
        self.multispectral_preview_running = False

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
        }


class FakeDeviceManager:
    def __init__(self):
        self.connected = False
        self.port = ""
        self.self_test_motion = None
        self.emergency_stopped = False
        self.camera_manager = FakeCameraManager()

    def _status(self):
        return {
            "connected": self.connected,
            "port": self.port,
            "fanOn": self.connected,
            "door": "closed" if self.connected else "unknown",
            "wheelPosition": 0 if self.connected else None,
            "wheelHomed": self.connected,
            "rgbLed1On": False,
            "rgbLed2On": False,
            "tungsten1On": False,
            "tungsten2On": False,
            "errorCode": 0 if self.connected else None,
            "emergencyStopped": self.emergency_stopped,
            "cameras": self.camera_manager.status(),
        }

    def list_ports(self):
        return [{"device": "COM3", "description": "STM32", "hwid": "USB"}]

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
            "includeMotion": include_motion,
            "status": self._status(),
            "checks": {
                "controller": {"status": "passed", "label": "STM32 控制器", "message": "PING 通过"},
                "filterWheel": {"status": "passed", "label": "滤光轮", "message": "位置: 0"},
                "rgbCamera": {"status": "not_connected", "label": "RGB 相机", "message": "RGB 相机未连接"},
                "multispectralCamera": {"status": "passed", "label": "多光谱相机", "message": "已连接 2048x1200"},
                "calibration": {"status": "manual_required", "label": "标定状态", "message": "当前需要操作员人工确认"},
            },
        }

    def emergency_stop(self):
        self.emergency_stopped = True
        return self._status()

    def fault_clear(self):
        self.emergency_stopped = False
        return self._status()

    def capture_status(self):
        return {"status": "not_ready", "progress": 0, "message": "完整真实采集协调器尚未接入"}

    def start_capture(self, sample_id=""):
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
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.thread.join(timeout=2)
        self.server.server_close()

    def get_json(self, path: str) -> dict:
        with urllib.request.urlopen(self.base_url + path, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))

    def get_response(self, path: str):
        return urllib.request.urlopen(self.base_url + path, timeout=5)

    def post_json(self, path: str, payload: dict | None = None) -> dict:
        request = urllib.request.Request(
            self.base_url + path,
            data=json.dumps(payload or {}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))

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
        self.assertEqual(self_test["result"]["checks"]["controller"]["status"], "passed")
        self.assertEqual(self_test["result"]["checks"]["rgbCamera"]["status"], "not_connected")
        self.assertEqual(self_test["result"]["checks"]["multispectralCamera"]["status"], "passed")
        self.assertEqual(self_test["result"]["checks"]["calibration"]["status"], "manual_required")

    def test_capture_start_reports_camera_integration_gap(self):
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
            self.assertTrue(response.read().startswith(b"\xff\xd8"))
        multi_stopped = self.post_json("/api/camera/multispectral/preview/stop")
        self.assertFalse(multi_stopped["result"]["preview"]["multispectral"]["running"])

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
        self.assertIn("id=\"multispectralExposureInput\"", multispectral_section)
        self.assertIn("id=\"multispectralGainInput\"", multispectral_section)
        self.assertNotIn("White Balance", multispectral_section)
        self.assertNotIn("白平衡", multispectral_section)

    def test_camera_settings_ui_uses_probe_endpoint_and_relative_camera_urls(self):
        app_js = (Path(__file__).parents[1] / "app.js").read_text(encoding="utf-8")

        self.assertIn("/api/camera/rgb/probe", app_js)
        self.assertIn("/api/camera/multispectral/probe", app_js)
        self.assertIn("/api/camera/multispectral/apply-settings", app_js)
        self.assertIn("/api/camera/multispectral/preview-frame", app_js)
        self.assertIn('addEventListener("click", probeRgbCamera)', app_js)
        self.assertIn('addEventListener("click", probeMultispectralCamera)', app_js)
        self.assertIn('addEventListener("click", applyMultispectralCameraSettings)', app_js)
        self.assertNotIn("http://127.0.0.1", app_js)
        self.assertNotIn("http://localhost", app_js)


if __name__ == "__main__":
    unittest.main()
