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


class FakeDeviceManager:
    def __init__(self):
        self.connected = False
        self.port = ""
        self.self_test_motion = None
        self.emergency_stopped = False

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
        return {"passed": True, "includeMotion": include_motion, "status": self._status()}

    def emergency_stop(self):
        self.emergency_stopped = True
        return self._status()

    def fault_clear(self):
        self.emergency_stopped = False
        return self._status()

    def capture_status(self):
        return {"status": "not_ready", "progress": 0, "message": "相机服务尚未接入"}

    def start_capture(self, sample_id=""):
        raise CameraIntegrationRequired("相机服务尚未接入，不能开始真实采集")

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
        self.assertFalse(status["device"]["connected"])

        ports = self.get_json("/api/device/ports")
        self.assertEqual(ports["ports"][0]["device"], "COM3")

        connected = self.post_json("/api/device/connect", {"port": "COM3"})
        self.assertTrue(connected["device"]["connected"])
        self.assertEqual(connected["device"]["port"], "COM3")

        self_test = self.post_json("/api/device/self-test", {"includeMotion": True})
        self.assertTrue(self_test["result"]["passed"])
        self.assertTrue(self.device.self_test_motion)

    def test_capture_start_reports_camera_integration_gap(self):
        with self.assertRaises(urllib.error.HTTPError) as context:
            self.post_json("/api/capture/start", {"sampleId": "S001"})

        self.assertEqual(context.exception.code, 409)


if __name__ == "__main__":
    unittest.main()
