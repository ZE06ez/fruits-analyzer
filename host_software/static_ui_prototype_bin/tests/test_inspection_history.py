import json
import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

from inspection.service import InspectionService
from backend_server import JobStore, SessionState, create_handler
from quality_prediction import predict_ph, predict_ssc, predict_ta

try:
    from .http_test_utils import InProcessHttpClient
except ImportError:
    from http_test_utils import InProcessHttpClient


class InspectionHistoryTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="fta_inspection_history_"))
        self.sample_root = self.root / "sample"
        for folder in (
            self.sample_root / "rgb",
            self.sample_root / "multispectral",
            self.sample_root / "calibration" / "dark",
            self.sample_root / "calibration" / "white",
        ):
            folder.mkdir(parents=True)
        Image.new("RGB", (12, 12), (40, 90, 60)).save(self.sample_root / "rgb" / "sample.png")
        for band in (450, 560, 670):
            Image.new("L", (12, 12), 100).save(self.sample_root / "multispectral" / f"sample_{band}.png")
            Image.new("L", (12, 12), 0).save(self.sample_root / "calibration" / "dark" / f"dark_{band}.png")
            Image.new("L", (12, 12), 255).save(self.sample_root / "calibration" / "white" / f"white_{band}.png")
        (self.sample_root / "metadata.json").write_text(json.dumps({
            "sample_mode": "inspection",
            "fruit_type": "blueberry",
            "variety": "Duke",
            "calibrationId": "cal-001",
            "background_reference": {"backgroundReferenceId": "bg-001", "backgroundReferencePath": "bg.png"},
            "registration": {"profileId": "reg-001", "version": "v1"},
        }), encoding="utf-8")
        self.service = InspectionService(self.root / "app")
        files = [str(self.sample_root / "rgb" / "sample.png")]
        spectral = [str(path) for path in sorted((self.sample_root / "multispectral").glob("*.png"))]
        self.sample = SimpleNamespace(
            sample_id="S001", sample_name="Blueberry 001", analysis_data_dir=str(self.sample_root),
            rgb_files=files, multispectral_files=spectral, capture_time="2026-09-18T10:00:00Z",
            fruit_type="blueberry", variety="Duke",
        )

    @staticmethod
    def result(target, value, status="success", **kwargs):
        return SimpleNamespace(
            target=target, value=value, unit={"ssc": "°Brix", "ta": "%", "ph": "pH"}[target],
            confidence=None, model_name=f"{target.upper()} model", model_version="v1",
            sample_count=1, elapsed_time=0.01, status=status, model_id=f"model-{target}",
            model_type="PLSR", preprocessing="RAW", error_message=kwargs.pop("error_message", ""),
            pipeline_signature="pipeline-001", model_pipeline_signature="pipeline-001",
            model_input_contract={"schema_version": 1, "mode": "production"},
            feature_pipeline={"mode": "production"}, **kwargs,
        )

    def test_results_are_grouped_atomically_and_keep_runtime_provenance(self):
        inspection = self.service.record_prediction(self.sample, self.result("ssc", 12.4))
        inspection_id = inspection["inspectionId"]
        self.assertEqual(inspection["status"], "RUNNING")
        self.assertEqual(len(inspection["results"]), 1)
        inspection = self.service.record_prediction(self.sample, self.result("ta", 0.4), inspection_id)
        inspection = self.service.record_prediction(self.sample, self.result("ph", 3.5), inspection_id)
        self.assertEqual(inspection["status"], "COMPLETED")
        self.assertEqual({item["target"] for item in inspection["results"]}, {"ssc", "ta", "ph"})
        self.assertEqual(inspection["sample"]["sampleId"], "S001")
        self.assertEqual(inspection["calibration"]["calibrationId"], "cal-001")
        self.assertEqual(inspection["backgroundReference"]["backgroundReferenceId"], "bg-001")
        self.assertEqual(inspection["pipelineSignature"], "pipeline-001")
        self.assertTrue(inspection["sample"]["sourceFiles"])

        listed = self.service.list_inspections(limit=10, fruit_type="blueberry", variety="Duke")
        self.assertEqual(listed["total"], 1)
        self.assertEqual(listed["items"][0]["inspectionId"], inspection_id)
        self.assertEqual(self.service.get_inspection(inspection_id)["results"][0]["modelId"], "model-ph")
        with self.service.repository.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM inspections").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM prediction_results WHERE inspection_id=?", (inspection_id,)).fetchone()[0], 3)

    def test_failure_has_no_fake_value_and_can_be_partial(self):
        inspection = self.service.record_prediction(self.sample, self.result(
            "ssc", None, status="model_input_mismatch", error_message="MODEL_INPUT_MISMATCH: contract differs",
        ))
        inspection_id = inspection["inspectionId"]
        self.service.record_prediction(self.sample, self.result("ta", 0.4), inspection_id)
        self.service.record_prediction(self.sample, self.result("ph", 3.5), inspection_id)
        detail = self.service.get_inspection(inspection_id)
        self.assertEqual(detail["status"], "PARTIAL")
        ssc = next(item for item in detail["results"] if item["target"] == "ssc")
        self.assertIsNone(ssc["value"])
        self.assertEqual(ssc["status"], "failed")
        self.assertEqual(ssc["errorCode"], "MODEL_INPUT_MISMATCH")

    def test_all_required_targets_failed_marks_inspection_failed(self):
        inspection = self.service.record_predictions(self.sample, [
            self.result("ssc", None, status="model_input_mismatch", error_message="MODEL_INPUT_MISMATCH: ssc"),
            self.result("ta", None, status="model_input_mismatch", error_message="MODEL_INPUT_MISMATCH: ta"),
            self.result("ph", None, status="model_input_mismatch", error_message="MODEL_INPUT_MISMATCH: ph"),
        ])
        self.assertEqual(inspection["status"], "FAILED")
        self.assertEqual(len(inspection["results"]), 3)
        for result in inspection["results"]:
            self.assertIsNone(result["value"])
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["errorCode"], "MODEL_INPUT_MISMATCH")

    def test_standalone_prediction_functions_do_not_create_history(self):
        result = SimpleNamespace(target="ssc", status="model_missing")
        with patch("quality_prediction._predict_target", return_value=result) as predict:
            self.assertIs(predict_ssc(self.sample), result)
            self.assertIs(predict_ta(self.sample), result)
            self.assertIs(predict_ph(self.sample), result)
        predict.assert_any_call(self.sample, target="ssc", unit="°Brix", display_name="SSC prediction model")
        predict.assert_any_call(self.sample, target="ta", unit="%", display_name="TA prediction model")
        predict.assert_any_call(self.sample, target="ph", unit="pH", display_name="pH prediction model")
        self.assertEqual(self.service.list_inspections()["total"], 0)

    def test_restart_preserves_results_and_provenance(self):
        results = [self.result("ssc", 12.4), self.result("ta", 0.4), self.result("ph", 3.5)]
        inspection = self.service.record_predictions(self.sample, results)
        inspection_id = inspection["inspectionId"]
        before = self.service.get_inspection(inspection_id)
        metadata = json.loads((self.sample_root / "metadata.json").read_text(encoding="utf-8"))
        metadata["calibrationId"] = "cal-after"
        metadata["background_reference"]["backgroundReferenceId"] = "bg-after"
        metadata["registration"] = {"profileId": "reg-after", "version": "v2"}
        (self.sample_root / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
        (self.sample_root / "calibration" / "calibration_set_current.json").write_text(
            json.dumps({"calibrationId": "cal-after"}), encoding="utf-8"
        )
        for result in results:
            result.model_id = f"default-after-{result.target}"
        reopened = InspectionService(self.root / "app").get_inspection(inspection_id)
        self.assertEqual(reopened["pipelineSignature"], before["pipelineSignature"])
        self.assertEqual(reopened["calibration"], before["calibration"])
        self.assertEqual(reopened["backgroundReference"], before["backgroundReference"])
        self.assertEqual(reopened["registration"], before["registration"])
        self.assertEqual(reopened["results"], before["results"])

    def test_query_filters_pagination_and_date_range(self):
        samples = []
        for sample_id, fruit_type, variety in (("S-old", "apple", "Gala"), ("S-mid", "blueberry", "Duke"), ("S-new", "blueberry", "Elliott")):
            sample = copy.copy(self.sample)
            sample.sample_id = sample_id
            sample.fruit_type = fruit_type
            sample.variety = variety
            samples.append(self.service.record_prediction(sample, self.result("ssc", 10.0)))
        dates = ("2026-09-18T10:00:00Z", "2026-09-18T11:00:00Z", "2026-09-18T12:00:00Z")
        with self.service.repository.connect() as conn:
            conn.executemany("UPDATE inspections SET created_at=?, updated_at=? WHERE inspection_id=?", [
                (date, date, item["inspectionId"]) for date, item in zip(dates, samples)
            ])
        page = self.service.list_inspections(limit=2, offset=0)
        self.assertEqual(page["total"], 3)
        self.assertEqual([item["sample"]["sampleId"] for item in page["items"]], ["S-new", "S-mid"])
        self.assertEqual(self.service.list_inspections(limit=2, offset=2)["items"][0]["sample"]["sampleId"], "S-old")
        self.assertEqual(self.service.list_inspections(fruit_type="BLUEBERRY", variety="duke")["total"], 1)
        self.assertEqual(self.service.list_inspections(status="RUNNING")["total"], 3)
        self.assertEqual(self.service.list_inspections(date_from="2026-09-18T11:00:00Z", date_to="2026-09-18T12:00:00Z")["total"], 2)

    def test_archive_removes_record_from_default_list_but_keeps_detail(self):
        inspection = self.service.record_prediction(self.sample, self.result("ssc", 11.0))
        inspection_id = inspection["inspectionId"]
        self.assertTrue(self.service.archive_inspection(inspection_id))
        self.assertEqual(self.service.list_inspections()["total"], 0)
        self.assertIsNotNone(self.service.get_inspection(inspection_id))

    def test_http_list_and_detail_endpoints_expose_persisted_history(self):
        inspection = self.service.record_prediction(self.sample, self.result("ssc", 11.0))
        static_dir = self.root / "static"
        outputs_dir = self.root / "outputs"
        static_dir.mkdir()
        outputs_dir.mkdir()
        handler = create_handler(static_dir, outputs_dir, self.root / "app", JobStore(), SessionState())
        client = InProcessHttpClient(handler)
        response = client.request("GET", "/api/inspections?limit=5")
        listing = json.loads(response.read().decode("utf-8"))
        response.close()
        self.assertEqual(listing["total"], 1)
        response = client.request("GET", f"/api/inspections/{inspection['inspectionId']}")
        detail = json.loads(response.read().decode("utf-8"))
        response.close()
        self.assertEqual(detail["inspection"]["inspectionId"], inspection["inspectionId"])
        self.assertEqual(detail["inspection"]["results"][0]["target"], "ssc")


if __name__ == "__main__":
    unittest.main()
