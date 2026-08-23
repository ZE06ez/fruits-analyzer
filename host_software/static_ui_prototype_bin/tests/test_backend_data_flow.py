import json
import tempfile
import threading
import time
import unittest
import urllib.parse
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer
from pathlib import Path

from PIL import Image

from backend_server import JobStore, SessionState, create_handler, validate_file_path, validate_folder_path
from model_studio.service import ModelStudioService


class BackendDataFlowTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="fta_backend_flow_"))
        self.static_dir = self.root / "static"
        self.app_dir = self.root / "app"
        self.outputs_dir = self.root / "outputs"
        self.static_dir.mkdir()
        self.app_dir.mkdir()
        self.outputs_dir.mkdir()
        self.studio = ModelStudioService(self.app_dir)
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

    def create_sample(self, name: str = "Duke成熟组03") -> dict:
        return self.post_json("/api/new-sample", {
            "sampleName": name,
            "fruitType": "blueberry",
            "variety": "Duke",
            "saveRootDir": str(self.root / "FruitData"),
        })["sample"]

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

    def test_path_validation_helpers_cover_folder_and_labels_csv(self):
        selected_dir = self.root / "含空格 标签目录"
        selected_dir.mkdir()
        folder_status = validate_folder_path(selected_dir, purpose="save", app_dir=self.app_dir)
        self.assertEqual(folder_status["state"], "已选择")
        self.assertTrue(folder_status["exists"])
        self.assertTrue(folder_status["isDirectory"])
        self.assertTrue(folder_status["writable"])

        labels_csv = selected_dir / "labels.csv"
        labels_csv.write_text("sample_id,ssc,ta,ph\nS001,11.2,0.41,3.5\n", encoding="utf-8")
        file_status = validate_file_path(labels_csv, purpose="labels-csv", app_dir=self.app_dir)
        self.assertEqual(file_status["state"], "已选择")
        self.assertTrue(file_status["isFile"])
        self.assertTrue(file_status["readable"])

        wrong_file = selected_dir / "labels.txt"
        wrong_file.write_text("not csv", encoding="utf-8")
        wrong_status = validate_file_path(wrong_file, purpose="labels-csv", app_dir=self.app_dir)
        self.assertEqual(wrong_status["state"], "无效")
        self.assertIn(".csv", wrong_status["message"])

        missing_status = validate_folder_path(selected_dir / "missing", purpose="sample", app_dir=self.app_dir)
        self.assertEqual(missing_status["state"], "无效")
        self.assertFalse(missing_status["exists"])

    def insert_model(
        self,
        model_id: str,
        *,
        target: str = "ssc",
        fruit_type: str = "blueberry",
        variety: str = "Duke",
        status: str = "Published",
        is_default: int = 0,
        display_name: str = "",
    ) -> None:
        model_dir = self.app_dir / "model_artifacts" / model_id
        model_dir.mkdir(parents=True, exist_ok=True)
        with self.studio.connect() as conn:
            conn.execute(
                """
                INSERT INTO models(model_id,model_name,display_name,target,fruit_type,variety,model_type,preprocessing,version,status,is_default,model_dir,metadata_json,created_at,published_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    model_id,
                    model_id,
                    display_name or model_id,
                    target,
                    fruit_type,
                    variety,
                    "SVR",
                    "SNV",
                    "v1.0.0",
                    status,
                    is_default,
                    str(model_dir),
                    "{}",
                    "2026-08-12 10:00:00",
                    "2026-08-12 10:00:00" if status in {"Published", "Default", "Production"} else "",
                ),
            )

    def wait_job(self, job_id: str) -> dict:
        for _ in range(40):
            job = self.get_json(f"/api/jobs/{job_id}")["job"]
            if job["status"] in {"done", "failed", "cancelled"}:
                return job
            time.sleep(0.05)
        self.fail("shape analysis job did not finish")

    def test_new_sample_requires_name_and_generates_unique_id(self):
        with self.assertRaises(urllib.error.HTTPError):
            self.post_json("/api/new-sample", {"sampleName": "", "fruitType": "blueberry", "saveRootDir": str(self.root / "FruitData")})
        with self.assertRaises(urllib.error.HTTPError):
            self.post_json("/api/new-sample", {"sampleName": "蓝莓01", "fruitType": "blueberry"})
        first = self.create_sample("蓝莓实验A-第5颗")
        first_dir = Path(first["currentCaptureDir"])
        self.assertTrue(first_dir.exists())
        self.assertTrue((first_dir / "rgb").is_dir())
        self.assertTrue((first_dir / "multispectral").is_dir())
        self.assertTrue((first_dir / "calibration" / "dark").is_dir())
        self.assertTrue((first_dir / "calibration" / "white").is_dir())
        self.assertTrue((first_dir / "metadata.json").is_file())
        metadata = json.loads((first_dir / "metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(metadata["fruit_type"], "blueberry")
        self.assertEqual(metadata["variety"], "Duke")
        self.post_json("/api/complete-capture", {})
        status_after_capture = self.get_json("/api/status")
        self.assertTrue(status_after_capture["currentCaptureDir"])
        self.assertEqual(Path(status_after_capture["currentCaptureDir"]), first_dir)

        second = self.create_sample("蓝莓实验A-第5颗")
        self.assertNotEqual(first["sampleId"], second["sampleId"])
        self.assertEqual(second["sampleName"], "蓝莓实验A-第5颗")
        self.assertNotEqual(Path(second["currentCaptureDir"]), first_dir)
        self.assertTrue(Path(second["currentCaptureDir"]).exists())
        self.assertFalse(second["analysisDataDir"])

    def test_quality_models_catalog_and_analysis_model_selection_scope(self):
        self.insert_model("duke_ssc", target="ssc", fruit_type="blueberry", variety="Duke", status="Default", is_default=1)
        self.insert_model("generic_ta", target="ta", fruit_type="blueberry", variety="generic", status="Default", is_default=1)
        self.insert_model("apple_ssc", target="ssc", fruit_type="apple", variety="Fuji", status="Default", is_default=1)
        self.insert_model("candidate_ph", target="ph", fruit_type="blueberry", variety="Duke", status="Candidate")

        catalog = self.get_json("/api/quality-models", {"fruitType": "blueberry", "variety": "Duke"})
        self.assertIn("blueberry", [item.lower() for item in catalog["fruitTypes"]])
        self.assertIn("Duke", catalog["varieties"])
        self.assertEqual(catalog["defaults"]["ssc"]["model_id"], "duke_ssc")
        self.assertEqual(catalog["defaults"]["ta"]["model_id"], "generic_ta")
        self.assertEqual(catalog["ph"], [])

        sample = self.post_json("/api/new-sample", {
            "sampleName": "蓝莓Duke-01",
            "fruitType": "blueberry",
            "variety": "Duke",
            "saveRootDir": str(self.root / "FruitData"),
        })["sample"]
        self.assertEqual(sample["selectedSscModelId"], "")
        self.assertEqual(sample["selectedTaModelId"], "")
        self.assertEqual(sample["selectedPhModelId"], "")

        selection = self.post_json("/api/model-selection", {
            "fruitType": "blueberry",
            "variety": "Duke",
            "selectedSscModelId": "duke_ssc",
            "selectedTaModelId": "generic_ta",
        })["session"]
        self.assertEqual(selection["selectedSscModelId"], "duke_ssc")
        self.assertEqual(selection["selectedTaModelId"], "generic_ta")

        second = self.post_json("/api/new-sample", {
            "sampleName": "苹果Fuji-01",
            "fruitType": "apple",
            "variety": "Fuji",
            "saveRootDir": str(self.root / "FruitData"),
        })["sample"]
        self.assertEqual(second["selectedSscModelId"], "")
        self.assertEqual(second["fruitType"], "apple")

        with self.assertRaises(urllib.error.HTTPError):
            self.post_json("/api/model-selection", {
                "fruitType": "blueberry",
                "variety": "Duke",
                "selectedSscModelId": "apple_ssc",
            })
        with self.assertRaises(urllib.error.HTTPError):
            self.post_json("/api/model-selection", {
                "fruitType": "blueberry",
                "variety": "Duke",
                "selectedSscModelId": "generic_ta",
            })
        with self.assertRaises(urllib.error.HTTPError):
            self.post_json("/api/model-selection", {
                "fruitType": "blueberry",
                "variety": "Duke",
                "selectedPhModelId": "candidate_ph",
            })

    def test_capture_sets_analysis_dir_and_quality_endpoints_use_it(self):
        status = self.get_json("/api/status")
        self.assertFalse(status["hasSample"])
        self.assertFalse(status["sampleId"])
        with self.assertRaises(urllib.error.HTTPError):
            self.post_json("/api/complete-capture", {"sampleId": "S001"})
        sample = self.create_sample()
        self.assertTrue(sample["hasSample"])
        self.assertEqual(sample["sampleName"], "Duke成熟组03")
        capture = self.post_json("/api/complete-capture", {"sampleId": "S001"})
        capture_dir = Path(capture["currentCaptureDir"])
        self.assertEqual(Path(capture["analysisDataDir"]), capture_dir)
        self.assertTrue((capture_dir / "rgb" / "rgb_001.png").is_file())
        self.assertTrue((capture_dir / "multispectral" / "450.png").is_file())
        self.assertTrue((capture_dir / "calibration" / "dark" / "dark_001.png").is_file())
        self.assertTrue((capture_dir / "calibration" / "white" / "white_001.png").is_file())

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
        self.assertEqual(report["sampleMetadata"]["fruit_type"], "blueberry")
        self.assertEqual(report["sampleMetadata"]["variety"], "Duke")

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
        self.create_sample("苹果测试01")
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
