import json
import tempfile
import threading
import time
import unittest
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from PIL import Image

from backend_server import JobStore, SessionState, create_handler


class BackendDataFlowTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="fta_backend_flow_"))
        self.static_dir = self.root / "static"
        self.app_dir = self.root / "app"
        self.outputs_dir = self.root / "outputs"
        self.static_dir.mkdir()
        self.app_dir.mkdir()
        self.outputs_dir.mkdir()
        self.session = SessionState()
        handler = create_handler(self.static_dir, self.outputs_dir, self.app_dir, JobStore(), self.session)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.thread.join(timeout=2)
        self.server.server_close()

    def get_json(self, path: str, params: dict | None = None) -> dict:
        query = f"?{urllib.parse.urlencode(params)}" if params else ""
        with urllib.request.urlopen(self.base_url + path + query, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))

    def post_json(self, path: str, payload: dict | None = None) -> dict:
        data = json.dumps(payload or {}).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + path,
            data=data,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))

    def make_dataset(self, name: str) -> Path:
        dataset = self.root / name
        rgb = dataset / "rgb"
        spectral = dataset / "multispectral"
        rgb.mkdir(parents=True)
        spectral.mkdir()
        Image.new("RGB", (40, 40), (80, 120, 70)).save(rgb / f"{name}_rgb.png")
        for band in (450, 560, 670):
            Image.new("L", (40, 40), 128).save(spectral / f"{name}_{band}.png")
        return dataset

    def wait_job(self, job_id: str) -> dict:
        for _ in range(40):
            job = self.get_json(f"/api/jobs/{job_id}")["job"]
            if job["status"] in {"done", "failed", "cancelled"}:
                return job
            time.sleep(0.05)
        self.fail("shape analysis job did not finish")

    def test_capture_sets_analysis_dir_and_quality_endpoints_use_it(self):
        capture = self.post_json("/api/complete-capture", {"sampleId": "S001"})
        capture_dir = Path(capture["currentCaptureDir"])
        self.assertEqual(Path(capture["analysisDataDir"]), capture_dir)

        status = self.get_json("/api/status")
        self.assertEqual(Path(status["currentCaptureDir"]), capture_dir)
        self.assertEqual(Path(status["analysisDataDir"]), capture_dir)

        report = self.get_json(
            "/api/sample-folder",
            {"datasetDir": str(capture_dir), "source": "current", "colorDir": "rgb", "depthDir": "multispectral"},
        )
        self.assertTrue(report["valid"])
        self.assertEqual(report["rgbCount"], 3)
        self.assertEqual(report["spectralCount"], 3)

        ssc = self.post_json("/api/predict-ssc")
        self.assertEqual(Path(ssc["sample"]["analysis_data_dir"]), capture_dir)
        self.assertEqual(ssc["result"]["status"], "model_missing")

        acid = self.post_json("/api/predict-acid")
        self.assertEqual(Path(acid["sample"]["analysis_data_dir"]), capture_dir)
        self.assertEqual(acid["taResult"]["status"], "model_missing")
        self.assertEqual(acid["phResult"]["status"], "model_missing")

        shape = self.post_json(
            "/api/analyze-shape",
            {"datasetDir": str(capture_dir), "colorDir": "rgb", "depthDir": "multispectral"},
        )
        job = self.wait_job(shape["jobId"])
        self.assertEqual(job["status"], "done")
        self.assertEqual(Path(job["result"]["datasetDir"]), capture_dir)

    def test_manual_folder_switches_session_to_latest_dataset(self):
        dataset_a = self.make_dataset("apple_001")
        dataset_b = self.make_dataset("apple_002")

        report_a = self.get_json(
            "/api/sample-folder",
            {"datasetDir": str(dataset_a), "source": "other", "colorDir": "rgb", "depthDir": "multispectral"},
        )
        self.assertTrue(report_a["valid"])

        report_b = self.get_json(
            "/api/sample-folder",
            {"datasetDir": str(dataset_b), "source": "other", "colorDir": "rgb", "depthDir": "multispectral"},
        )
        self.assertTrue(report_b["valid"])

        status = self.get_json("/api/status")
        self.assertEqual(Path(status["analysisDataDir"]), dataset_b)

        ssc = self.post_json("/api/predict-ssc")
        self.assertEqual(Path(ssc["sample"]["analysis_data_dir"]), dataset_b)
        self.assertEqual(ssc["result"]["status"], "model_missing")

        acid = self.post_json("/api/predict-acid")
        self.assertEqual(Path(acid["sample"]["analysis_data_dir"]), dataset_b)
        self.assertEqual(acid["taResult"]["status"], "model_missing")


if __name__ == "__main__":
    unittest.main()
