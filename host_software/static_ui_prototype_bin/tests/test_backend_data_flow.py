import json
import http.client
import io
import tempfile
import threading
import time
import unittest
import urllib.parse
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
        self.server.daemon_threads = False
        self.server.block_on_close = True
        self.server_ready = threading.Event()
        self.thread = threading.Thread(target=self._serve, name="BackendDataFlowTestServer")
        self.thread.start()
        self.assertTrue(self.server_ready.wait(timeout=2), "test HTTP server did not start")
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        try:
            self.server.shutdown()
        finally:
            self.server.server_close()
            self.thread.join(timeout=2)
        self.assertFalse(self.thread.is_alive(), "test HTTP server thread did not stop")

    def _serve(self):
        self.server_ready.set()
        self.server.serve_forever(poll_interval=0.01)

    def get_json(self, path: str, params: dict | None = None) -> dict:
        query = f"?{urllib.parse.urlencode(params)}" if params else ""
        return self.request_json("GET", path + query)

    def post_json(self, path: str, payload: dict | None = None) -> dict:
        data = json.dumps(payload or {}).encode("utf-8")
        return self.request_json("POST", path, body=data, headers={"Content-Type": "application/json"})

    def request_json(self, method: str, path: str, body: bytes | None = None, headers: dict | None = None) -> dict:
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            request_headers = {"Connection": "close", **(headers or {})}
            connection.request(method, path, body=body, headers=request_headers)
            response = connection.getresponse()
            raw = response.read()
            if response.status >= 400:
                raise urllib.error.HTTPError(
                    self.base_url + path,
                    response.status,
                    response.reason,
                    response.headers,
                    io.BytesIO(raw),
                )
            return json.loads(raw.decode("utf-8"))
        finally:
            connection.close()

    def prepare_device(self) -> dict:
        return self.post_json("/api/device-preparation", {
            "connect": True,
            "motor": True,
            "light": True,
            "camera": True,
            "calibration": True,
        })

    def create_sample(self, name: str = "Duke成熟组03") -> dict:
        self.prepare_device()
        return self.post_json("/api/new-sample", {
            "sampleName": name,
            "fruitType": "blueberry",
            "variety": "Duke",
            "saveRootDir": str(self.root / "FruitData"),
        })["sample"]

    def test_offline_device_preparation_allows_sample_without_camera_sdk(self):
        prep = self.post_json("/api/device-preparation", {
            "connect": True,
            "motor": True,
            "light": True,
            "camera": False,
            "calibration": False,
        })
        self.assertTrue(prep["devicePrepared"])
        self.assertFalse(prep["trueCapturePrepared"])

        status = self.get_json("/api/status")
        self.assertTrue(status["devicePrepared"])
        self.assertFalse(status["trueCapturePrepared"])

        sample = self.post_json("/api/new-sample", {
            "sampleName": "OfflineReady",
            "fruitType": "blueberry",
            "variety": "Duke",
            "saveRootDir": str(self.root / "FruitData"),
        })["sample"]
        self.assertTrue(sample["hasSample"])

    def test_true_capture_prepared_remains_false_until_capture_coordinator_exists(self):
        prep = self.post_json("/api/device-preparation", {
            "connect": True,
            "motor": True,
            "light": True,
            "camera": True,
            "calibration": True,
        })

        self.assertTrue(prep["devicePrepared"])
        self.assertFalse(prep["trueCapturePrepared"])

        status = self.get_json("/api/status")
        self.assertFalse(status["trueCapturePrepared"])

    def make_dataset(self, name: str, rgb_dir_name: str = "rgb", spectral_dir_name: str = "multispectral") -> Path:
        dataset = self.root / name
        rgb = dataset / rgb_dir_name
        spectral = dataset / spectral_dir_name
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
            self.post_json("/api/new-sample", {"sampleName": "蓝莓01", "fruitType": "blueberry", "saveRootDir": str(self.root / "FruitData")})
        self.prepare_device()
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
        self.assertEqual(metadata["image_directories"], {"rgb": "rgb", "multispectral": "multispectral"})
        self.assertEqual(first["rgbDirName"], "rgb")
        self.assertEqual(first["multispectralDirName"], "multispectral")
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

    def test_custom_capture_image_directory_names_are_saved_and_read(self):
        self.prepare_device()
        sample = self.post_json("/api/new-sample", {
            "sampleName": "自定义目录01",
            "fruitType": "blueberry",
            "variety": "Duke",
            "saveRootDir": str(self.root / "FruitData"),
            "rgbDirName": "color_images",
            "multispectralDirName": "spectral_images",
        })["sample"]
        capture_root = Path(sample["currentCaptureDir"])

        self.assertTrue((capture_root / "color_images").is_dir())
        self.assertTrue((capture_root / "spectral_images").is_dir())
        self.assertFalse((capture_root / "rgb").exists())
        self.assertFalse((capture_root / "multispectral").exists())

        capture = self.post_json("/api/complete-capture", {"sampleId": sample["sampleId"]})
        capture_dir = Path(capture["currentCaptureDir"])
        self.assertEqual(capture["rgbDirName"], "color_images")
        self.assertEqual(capture["multispectralDirName"], "spectral_images")
        self.assertTrue((capture_dir / "color_images" / "rgb_001.png").is_file())
        self.assertTrue((capture_dir / "spectral_images" / "450.png").is_file())

        metadata = json.loads((capture_dir / "metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(metadata["image_directories"], {"rgb": "color_images", "multispectral": "spectral_images"})

        report = self.get_json("/api/sample-folder", {"datasetDir": str(capture_dir), "source": "current"})
        self.assertTrue(report["valid"])
        self.assertEqual(report["rgbDirName"], "color_images")
        self.assertEqual(report["multispectralDirName"], "spectral_images")
        self.assertEqual(report["sampleMetadata"]["image_directories"]["rgb"], "color_images")

        images = self.get_json("/api/dataset-images", {"datasetDir": str(capture_dir)})
        self.assertEqual(images["rgbDirName"], "color_images")
        self.assertEqual(images["multispectralDirName"], "spectral_images")
        self.assertEqual(Path(images["multispectralDir"]), capture_dir / "spectral_images")
        self.assertTrue(images["images"])
        self.assertIn("multispectral", images["images"][0])
        self.assertEqual(images["images"][0]["depth"], images["images"][0]["multispectral"])

    def test_image_directory_name_validation_rejects_empty_duplicate_and_illegal_values(self):
        self.prepare_device()
        base = {
            "sampleName": "非法目录名",
            "fruitType": "blueberry",
            "saveRootDir": str(self.root / "FruitData"),
        }
        invalid_payloads = [
            {**base, "rgbDirName": "", "multispectralDirName": "spectral_images"},
            {**base, "rgbDirName": "same", "multispectralDirName": "same"},
            {**base, "rgbDirName": "color/images", "multispectralDirName": "spectral_images"},
            {**base, "rgbDirName": "color_images", "multispectralDirName": "bad:name"},
            {**base, "rgbDirName": ".", "multispectralDirName": "spectral_images"},
        ]
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(urllib.error.HTTPError):
                    self.post_json("/api/new-sample", payload)

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

        self.prepare_device()
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
        self.assertFalse(status["devicePrepared"])
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
            {"datasetDir": str(capture_dir), "source": "current", "colorDir": "rgb", "multispectralDirName": "multispectral"},
        )
        self.assertTrue(report["valid"])
        self.assertEqual(report["rgbCount"], 3)
        self.assertEqual(report["spectralCount"], 3)
        self.assertEqual(Path(report["multispectralDir"]), capture_dir / "multispectral")
        self.assertEqual(report["depthDir"], report["multispectralDir"])
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
            {"datasetDir": str(capture_dir), "colorDir": "rgb", "multispectralDirName": "multispectral"},
        )
        job = self.wait_job(shape["jobId"])
        self.assertEqual(job["status"], "done")
        self.assertEqual(Path(job["result"]["datasetDir"]), capture_dir)

    def test_inspect_image_folders_lists_direct_children_and_suggests_roles(self):
        dataset = self.make_dataset("folder_scan", "color_images", "spectral_images")
        (dataset / "calibration").mkdir()
        (dataset / "mask").mkdir()
        nested = dataset / "nested"
        (nested / "rgb").mkdir(parents=True)

        result = self.get_json("/api/inspect-image-folders", {"parentDir": str(dataset)})
        names = [item["name"] for item in result["directories"]]
        roles = {item["name"]: item["suggestedRole"] for item in result["directories"]}

        self.assertIn("color_images", names)
        self.assertIn("spectral_images", names)
        self.assertIn("calibration", names)
        self.assertIn("mask", names)
        self.assertIn("nested", names)
        self.assertNotIn("rgb", names)
        self.assertEqual(roles["color_images"], "rgb")
        self.assertEqual(roles["spectral_images"], "multispectral")
        self.assertEqual(roles["calibration"], "other")

    def test_manual_parent_selection_can_store_custom_and_other_directories(self):
        self.create_sample("手动目录选择01")
        dataset = self.make_dataset("manual_custom", "color_images", "spectral_images")
        (dataset / "calibration").mkdir()
        (dataset / "mask").mkdir()

        report = self.get_json(
            "/api/sample-folder",
            {
                "datasetDir": str(dataset),
                "source": "other",
                "colorDir": "color_images",
                "multispectralDirName": "spectral_images",
                "otherDirs": "calibration,mask",
                "strictImageDirs": "1",
            },
        )
        self.assertTrue(report["valid"])
        self.assertEqual(report["rgbDirName"], "color_images")
        self.assertEqual(report["multispectralDirName"], "spectral_images")
        self.assertEqual(report["otherImageDirs"], ["calibration", "mask"])

        status = self.get_json("/api/status")
        self.assertEqual(Path(status["analysisDataDir"]), dataset)
        self.assertEqual(status["rgbDirName"], "color_images")
        self.assertEqual(status["multispectralDirName"], "spectral_images")
        self.assertEqual(status["otherImageDirs"], ["calibration", "mask"])

        ssc = self.post_json("/api/predict-ssc")
        self.assertEqual(Path(ssc["sample"]["analysis_data_dir"]), dataset)
        self.assertEqual(ssc["result"]["status"], "model_missing")

    def test_manual_directory_validation_rejects_same_missing_and_parent_escape(self):
        self.create_sample("手动目录非法01")
        dataset = self.make_dataset("manual_invalid", "color_images", "spectral_images")
        invalid_params = [
            {"colorDir": "", "depthDir": "spectral_images"},
            {"colorDir": "color_images", "depthDir": "color_images"},
            {"colorDir": "missing", "depthDir": "spectral_images"},
            {"colorDir": "..", "depthDir": "spectral_images"},
            {"colorDir": "../outside", "depthDir": "spectral_images"},
        ]
        for params in invalid_params:
            with self.subTest(params=params):
                with self.assertRaises(urllib.error.HTTPError):
                    query = {
                        "datasetDir": str(dataset),
                        "source": "other",
                        "strictImageDirs": "1",
                        **params,
                    }
                    self.get_json("/api/sample-folder", query)

    def test_legacy_rgb_multispectral_dataset_still_reads_without_explicit_directories(self):
        dataset = self.make_dataset("legacy_dataset")
        report = self.get_json("/api/sample-folder", {"datasetDir": str(dataset), "source": "other"})
        self.assertTrue(report["valid"])
        self.assertEqual(report["rgbDirName"], "rgb")
        self.assertEqual(report["multispectralDirName"], "multispectral")
        self.assertEqual(Path(report["multispectralDir"]), dataset / "multispectral")

    def test_legacy_depth_dir_alias_still_reads_multispectral_directory(self):
        dataset = self.make_dataset("legacy_depth_alias", "color_images", "spectral_images")
        report = self.get_json(
            "/api/sample-folder",
            {
                "datasetDir": str(dataset),
                "source": "other",
                "colorDir": "color_images",
                "depthDir": "spectral_images",
                "strictImageDirs": "1",
            },
        )

        self.assertTrue(report["valid"])
        self.assertEqual(report["multispectralDirName"], "spectral_images")
        self.assertEqual(Path(report["multispectralDir"]), dataset / "spectral_images")
        self.assertEqual(report["depthDir"], report["multispectralDir"])

    def test_manual_folder_shape_analysis_does_not_require_current_sample(self):
        dataset = self.make_dataset("manual_shape_only")
        shape = self.post_json(
            "/api/analyze-shape",
            {"datasetDir": str(dataset), "colorDir": "rgb", "depthDir": "multispectral"},
        )
        job = self.wait_job(shape["jobId"])
        self.assertEqual(job["status"], "done")

    def test_multiview_capture_plan_writes_view_metadata_and_compatible_files(self):
        self.prepare_device()
        sample = self.post_json("/api/new-sample", {
            "sampleName": "蓝莓多角度01",
            "fruitType": "blueberry",
            "variety": "Duke",
            "saveRootDir": str(self.root / "FruitData"),
            "sampleRotation": {
                "enabled": True,
                "expectedIntervalDeg": 50,
                "startAngleDeg": 0,
                "direction": "CCW",
                "includeClosureView": False,
            },
        })["sample"]
        self.assertEqual(sample["captureRotationPlan"]["view_count"], 8)
        self.assertEqual(sample["captureRotationPlan"]["actual_interval_deg"], 45)

        capture = self.post_json("/api/complete-capture", {
            "sampleId": sample["sampleId"],
            "sampleRotation": {
                "enabled": True,
                "expectedIntervalDeg": 50,
                "startAngleDeg": 0,
                "direction": "CCW",
                "includeClosureView": False,
            },
        })
        capture_dir = Path(capture["currentCaptureDir"])
        metadata = json.loads((capture_dir / "metadata.json").read_text(encoding="utf-8"))
        views = json.loads((capture_dir / "views.json").read_text(encoding="utf-8"))
        rotation = metadata["sample_rotation"]

        self.assertEqual(rotation["angles_deg"], [0, 45, 90, 135, 180, 225, 270, 315])
        self.assertEqual(rotation["home_status"], "HOME_OK")
        self.assertTrue(rotation["returned_home"])
        self.assertEqual(metadata["filter_wheel_rotation"]["control_domain"], "filter_wheel_rotation")
        self.assertTrue(metadata["filter_wheel_rotation"]["independent_from_sample_rotation"])
        self.assertEqual(len(views), 8)
        self.assertTrue(all(view["sample_id"] == sample["sampleId"] for view in views))
        self.assertTrue(all(view["direction"] == "CCW" for view in views))
        self.assertFalse(any(view["closure_view"] for view in views))
        self.assertTrue((capture_dir / "rgb" / "rgb_view_000.png").is_file())
        self.assertTrue((capture_dir / "rgb" / "rgb_view_315.png").is_file())
        self.assertTrue((capture_dir / "multispectral" / "view000_450.png").is_file())
        self.assertTrue((capture_dir / "multispectral" / "view315_670.png").is_file())
        self.assertFalse((capture_dir / "rgb" / "rgb_view_360.png").exists())

        report = self.get_json(
            "/api/sample-folder",
            {"datasetDir": str(capture_dir), "source": "current", "colorDir": "rgb", "depthDir": "multispectral"},
        )
        self.assertTrue(report["valid"])
        self.assertEqual(report["rgbCount"], 8)
        self.assertEqual(report["spectralCount"], 24)
        self.assertEqual(report["sampleMetadata"]["sample_rotation"]["view_count"], 8)

    def test_closure_view_is_saved_only_when_requested(self):
        self.prepare_device()
        sample = self.post_json("/api/new-sample", {
            "sampleName": "蓝莓闭合检查",
            "fruitType": "blueberry",
            "variety": "Duke",
            "saveRootDir": str(self.root / "FruitData"),
            "sampleRotation": {
                "enabled": True,
                "expectedIntervalDeg": 90,
                "includeClosureView": True,
            },
        })["sample"]
        capture = self.post_json("/api/complete-capture", {"sampleId": sample["sampleId"]})
        capture_dir = Path(capture["currentCaptureDir"])
        views = json.loads((capture_dir / "views.json").read_text(encoding="utf-8"))
        self.assertEqual(len(views), 5)
        self.assertTrue(views[-1]["closure_view"])
        self.assertEqual(views[-1]["mechanical_angle_deg"], 360)
        self.assertTrue((capture_dir / "rgb" / "rgb_view_360.png").is_file())

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
