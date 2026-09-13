import json
import io
import tempfile
import time
import unittest
import urllib.parse
import urllib.error
from pathlib import Path

from PIL import Image

from backend_server import JobStore, SessionState, create_handler, create_offline_capture_dataset, validate_file_path, validate_folder_path
from model_studio.service import ModelStudioService

try:
    from .http_test_utils import InProcessHttpClient
except ImportError:
    from http_test_utils import InProcessHttpClient


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
        self.client = InProcessHttpClient(handler)
        self.base_url = "http://127.0.0.1"

    def tearDown(self):
        return None

    def get_json(self, path: str, params: dict | None = None) -> dict:
        query = f"?{urllib.parse.urlencode(params)}" if params else ""
        return self.request_json("GET", path + query)

    def post_json(self, path: str, payload: dict | None = None) -> dict:
        data = json.dumps(payload or {}).encode("utf-8")
        return self.request_json("POST", path, body=data, headers={"Content-Type": "application/json"})

    def request_json(self, method: str, path: str, body: bytes | None = None, headers: dict | None = None) -> dict:
        response = self.client.request(method, path, body=body, headers=headers)
        try:
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
            response.close()

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

    def assert_no_jpeg_paths_under(self, root: Path) -> None:
        suffixes = {path.suffix.lower() for path in root.rglob("*") if path.is_file()}
        self.assertFalse({".jpg", ".jpeg"} & suffixes)
        metadata_path = root / "metadata.json"
        if metadata_path.exists():
            text = metadata_path.read_text(encoding="utf-8").lower()
            self.assertNotIn(".jpg", text)
            self.assertNotIn(".jpeg", text)

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
        model_dir = self.studio.model_dir / "test_inserted" / model_id
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
        deadline = time.time() + 10.0
        while time.time() < deadline:
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
        localized = self.post_json("/api/new-sample", {"sampleName": "sample01", "fruitType": "蓝莓", "saveRootDir": str(self.root / "FruitData")})["sample"]
        self.assertEqual(localized["fruitType"], "蓝莓")
        first = self.post_json("/api/new-sample", {
            "sampleName": "蓝莓实验A-第5颗",
            "fruitType": "blueberry",
            "variety": "Duke",
            "saveRootDir": str(self.root / "FruitData"),
        })["sample"]
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
        self.prepare_device()
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

    def test_true_capture_not_ready_still_blocks_after_sample_creation_without_hardware(self):
        sample = self.post_json("/api/new-sample", {
            "sampleName": "NoHardwareFirst",
            "fruitType": "blueberry",
            "variety": "Duke",
            "saveRootDir": str(self.root / "FruitData"),
        })["sample"]

        readiness = self.get_json("/api/capture/readiness", {
            "captureMode": "single_view",
            "calibrationMode": "existing",
            "calibrationId": "",
            "requireCalibration": "true",
        })["readiness"]

        self.assertTrue(sample["hasSample"])
        self.assertFalse(readiness["ready"])
        codes = {item["code"] for item in readiness["blockingReasons"]}
        self.assertIn("STM32_NOT_READY", codes)
        self.assertIn("CAMERA_NOT_READY", codes)
        self.assertIn("FILTER_WHEEL_NOT_READY", codes)
        self.assertIn("CALIBRATION_REQUIRED", codes)

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
        self.assert_no_jpeg_paths_under(capture_dir)

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

    def test_offline_capture_dataset_uses_png_for_multiview_and_metadata(self):
        capture_dir = self.root / "offline_multiview"
        metadata = {
            "sampleId": "S-OFFLINE-MV",
            "sample_id": "S-OFFLINE-MV",
            "image_directories": {"rgb": "rgb", "multispectral": "multispectral"},
            "sample_rotation": {
                "enabled": True,
                "expectedIntervalDeg": 180,
                "startAngleDeg": 0,
                "direction": "CW",
                "includeClosureView": True,
            },
        }

        created = create_offline_capture_dataset(self.app_dir, "S-OFFLINE-MV", capture_dir=capture_dir, metadata=metadata)

        self.assertEqual(created, capture_dir)
        self.assertTrue((capture_dir / "rgb" / "rgb_view_000.png").is_file())
        self.assertTrue((capture_dir / "multispectral" / "view000_450.png").is_file())
        self.assertTrue((capture_dir / "calibration" / "dark" / "dark_450.png").is_file())
        self.assertTrue((capture_dir / "calibration" / "white" / "white_450.png").is_file())
        self.assert_no_jpeg_paths_under(capture_dir)

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

    def test_manual_folder_without_metadata_requests_sample_scope_then_loads_models(self):
        self.insert_model("scope_ssc", target="ssc", fruit_type="blueberry", variety="Duke", status="Default", is_default=1)
        self.create_sample("缺少元数据Scope")
        dataset = self.make_dataset("manual_no_metadata")

        report = self.get_json(
            "/api/sample-folder",
            {"datasetDir": str(dataset), "source": "other", "colorDir": "rgb", "multispectralDirName": "multispectral"},
        )
        self.assertTrue(report["valid"])
        self.assertTrue(report["requiresSampleScope"])
        self.assertIn("blueberry", [item.lower() for item in report["sampleScopeOptions"]["fruitTypes"]])

        scoped = self.get_json(
            "/api/sample-folder",
            {
                "datasetDir": str(dataset),
                "source": "other",
                "colorDir": "rgb",
                "multispectralDirName": "multispectral",
                "fruitType": "blueberry",
                "variety": "Duke",
            },
        )
        self.assertEqual(scoped["sampleScope"], {"fruitType": "blueberry", "variety": "Duke", "source": "user"})
        status = self.get_json("/api/status")
        self.assertEqual(status["fruitType"], "blueberry")
        self.assertEqual(status["variety"], "Duke")
        self.assertEqual(status["selectedSscModelId"], "scope_ssc")

    def test_model_studio_filters_and_training_start_api_create_new_target_experiment(self):
        dataset = self.post_json("/api/model-studio/datasets", {
            "datasetName": "API Dataset",
            "fruitType": "blueberry",
            "variety": "Duke",
        })["dataset"]
        listed = self.get_json("/api/model-studio/datasets", {"query": "API", "archived": "0"})
        self.assertEqual(listed["datasets"][0]["dataset_id"], dataset["dataset_id"])

        with self.studio.connect() as conn:
            conn.execute(
                """
                INSERT INTO dataset_versions(dataset_version_id,dataset_id,version,version_name,sample_count,sample_ids,label_count,created_at,snapshot_hash,label_snapshot_json,sample_snapshot_json)
                VALUES('api_v1',?,1,'Dataset V1',1,'["sample_001"]',1,'2026-01-01','hash','{"sample_001":{"ta":0.42}}','[{"sample_id":"sample_001"}]')
                """,
                (dataset["dataset_id"],),
            )
            conn.execute("UPDATE datasets SET latest_version_id='api_v1' WHERE dataset_id=?", (dataset["dataset_id"],))
        started = self.post_json("/api/model-studio/training/start", {
            "datasetId": dataset["dataset_id"],
            "datasetVersionId": "api_v1",
            "target": "ta",
            "models": ["PLSR"],
            "preprocessing": ["RAW"],
            "validationMethod": "GroupKFold",
        })
        self.assertEqual(started["experiment"]["target"], "ta")
        self.assertEqual(started["job"]["dataset_version_id"], "api_v1")

    def test_model_studio_dataset_delete_and_archive_routes_refresh_lists(self):
        dataset = self.post_json("/api/model-studio/datasets", {
            "datasetName": "API Delete Dataset",
            "fruitType": "blueberry",
            "variety": "Duke",
        })["dataset"]
        refs = self.get_json(f"/api/model-studio/datasets/{dataset['dataset_id']}/references")["references"]
        self.assertTrue(refs["canDeletePermanently"])
        self.assertEqual(refs["summary"]["models"], 0)

        archived = self.post_json("/api/model-studio/datasets/archive", {"datasetId": dataset["dataset_id"]})["dataset"]
        self.assertEqual(archived["archived"], 1)
        active = self.get_json("/api/model-studio/datasets", {"archived": "0"})
        self.assertNotIn(dataset["dataset_id"], [item["dataset_id"] for item in active["datasets"]])
        all_datasets = self.get_json("/api/model-studio/datasets", {"archived": "all"})
        self.assertIn(dataset["dataset_id"], [item["dataset_id"] for item in all_datasets["datasets"]])

        result = self.post_json("/api/model-studio/datasets/delete", {
            "datasetId": dataset["dataset_id"],
            "confirm": dataset["dataset_name"],
        })["result"]
        self.assertTrue(result["deleted"])
        all_after = self.get_json("/api/model-studio/datasets", {"archived": "all"})
        self.assertNotIn(dataset["dataset_id"], [item["dataset_id"] for item in all_after["datasets"]])

    def test_model_studio_model_delete_route_updates_quality_models(self):
        model_id = "api_delete_model"
        model_dir = self.studio.model_dir / "candidates" / "api" / model_id
        model_dir.mkdir(parents=True, exist_ok=True)
        (model_dir / "model.joblib").write_bytes(b"fake")
        (model_dir / "metadata.json").write_text(json.dumps({
            "model_id": model_id,
            "target": "ssc",
            "model_type": "SVR",
            "preprocessing": "SNV",
        }), encoding="utf-8")
        with self.studio.connect() as conn:
            conn.execute(
                """
                INSERT INTO models(model_id,model_name,display_name,target,fruit_type,variety,model_type,preprocessing,version,status,is_default,model_dir,metadata_json,created_at,published_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    model_id,
                    model_id,
                    "API Delete Model",
                    "ssc",
                    "blueberry",
                    "Duke",
                    "SVR",
                    "SNV",
                    "v1",
                    "Published",
                    0,
                    str(model_dir),
                    "{}",
                    "2026-01-01 00:00:00",
                    "2026-01-01 00:00:00",
                ),
            )
        self.studio.publish_model(model_id, {"displayName": "API Delete Model"})
        before = self.get_json("/api/quality-models", {"fruitType": "blueberry", "variety": "Duke"})
        self.assertIn(model_id, [item["model_id"] for item in before["ssc"]])
        deleted = self.post_json("/api/model-studio/models/delete", {"modelId": model_id, "confirm": model_id})["result"]
        self.assertTrue(deleted["deleted"])
        after = self.get_json("/api/quality-models", {"fruitType": "blueberry", "variety": "Duke"})
        self.assertNotIn(model_id, [item["model_id"] for item in after["ssc"]])
        registry = self.get_json("/api/model-studio/models")
        self.assertNotIn(model_id, [item["model_id"] for item in registry["models"]])

    def test_model_studio_model_batch_delete_route_blocks_default_and_deletes_allowed(self):
        candidate_id = "api_batch_candidate"
        published_id = "api_batch_published"
        default_id = "api_batch_default"
        self.insert_model(candidate_id, status="Candidate")
        self.insert_model(published_id, status="Published")
        self.insert_model(default_id, status="Default", is_default=1)

        result = self.post_json("/api/model-studio/models/delete-batch", {
            "modelIds": [candidate_id, published_id, default_id],
        })["result"]

        self.assertEqual({item["modelId"] for item in result["deleted"]}, {candidate_id, published_id})
        self.assertEqual([item["modelId"] for item in result["blocked"]], [default_id])
        registry = self.get_json("/api/model-studio/models", {"status": "all"})["models"]
        remaining_ids = [item["model_id"] for item in registry]
        self.assertNotIn(candidate_id, remaining_ids)
        self.assertNotIn(published_id, remaining_ids)
        self.assertIn(default_id, remaining_ids)

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
