from __future__ import annotations

import json
import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from tests.e2e_support import build_production_resources, build_synthetic_sample
from inspection.service import InspectionService
from model_studio.service import ModelStudioService
from quality_algorithm.analysis_pipeline import FeaturePipelineConfig
from quality_algorithm.model_io import load_model_bundle
from quality_prediction import build_prediction_feature_record, build_sample_session, predict_ssc
import quality_prediction


class SoftwareE2ETests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="fta_software_e2e_", ignore_cleanup_errors=True)
        self.root = Path(self.temp_dir.name)
        self.source_root = self.root / "source_samples"
        self.source_root.mkdir()
        self.background_json, self.profile_path, self.pipeline_config = build_production_resources(self.root)
        self.sample_paths = [
            build_synthetic_sample(self.source_root, f"sample_{index:03d}", index)
            for index in range(6)
        ]
        self.inspection_path = build_synthetic_sample(self.source_root, "inspection_001", 6)
        self.app_dir = self.root / "app"
        self.service = ModelStudioService(self.app_dir)
        self.service.feature_pipeline_config = self.pipeline_config

    def tearDown(self):
        self.service = None
        time.sleep(0.5)
        self.temp_dir.cleanup()

    def test_production_train_publish_default_and_inspection_prediction(self):
        with self._hardware_guard():
            dataset, version, features = self._prepare_dataset()
            self.assertEqual(version["sample_count"], 6)
            self.assertTrue(version["snapshot_hash"])
            self.assertEqual(features["rows"], 6)
            self.assertEqual(features["modelInputContract"]["mode"], "production")
            self.assertTrue(features["pipelineSignature"])

            experiment = self.service.create_experiment({
                "datasetId": dataset["dataset_id"],
                "datasetVersionId": version["dataset_version_id"],
                "target": "ssc",
                "models": ["PLSR"],
                "preprocessing": ["RAW"],
                "validationMethod": "GroupKFold",
            })
            job = self.service.create_training_job(experiment["experiment_id"])
            job = self._wait_for_job(job["job_id"])
            self.assertEqual(job["status"], "Completed", job.get("error") or job.get("message"))
            candidate = self.service.list_models()[0]
            candidate_metadata = json.loads((Path(candidate["model_dir"]) / "metadata.json").read_text(encoding="utf-8"))
            self.assertEqual(candidate_metadata["target"], "ssc")
            self.assertEqual(candidate_metadata["fruit_type"], "blueberry")
            self.assertEqual(candidate_metadata["variety"], "Duke")
            self.assertTrue(candidate_metadata["feature_pipeline"])
            self.assertEqual(candidate_metadata["pipeline_signature"], features["pipelineSignature"])

            validated = self.service.validate_model(candidate["model_id"])
            self.assertEqual(validated["status"], "Validated")
            published = self.service.publish_model(candidate["model_id"], {"setDefault": True})
            self.assertEqual(published["status"], "Default")

            session, report = build_sample_session(self.inspection_path, fruit_type="blueberry", variety="Duke")
            self.assertTrue(report["complete"])
            production_root = self.app_dir / "trained_models"
            old_model_root = quality_prediction.MODEL_ROOT
            try:
                quality_prediction.MODEL_ROOT = production_root
                bundle = load_model_bundle(production_root / "ssc")
                actual_record = build_prediction_feature_record(session, bundle.metadata)
                self.assertEqual(actual_record.pipeline_signature, bundle.metadata["pipeline_signature"])
                result = predict_ssc(session)
            finally:
                quality_prediction.MODEL_ROOT = old_model_root
            self.assertEqual(result.status, "success")
            self.assertIsNotNone(result.value)
            self.assertEqual(result.model_id, published["model_id"])
            history = InspectionService(self.app_dir).record_prediction(session, result)
            self.assertEqual(history["status"], "RUNNING")
            self.assertEqual(len(history["results"]), 1)
            reopened = InspectionService(self.app_dir).get_inspection(history["inspectionId"])
            self.assertEqual(reopened["status"], "FAILED_RECOVERABLE")
            self.assertEqual(len(reopened["results"]), 1)
            persisted = reopened["results"][0]
            self.assertEqual(persisted["value"], result.value)
            self.assertEqual(persisted["modelId"], result.model_id)
            self.assertEqual(reopened["pipelineSignature"], result.pipeline_signature)
            self.assertEqual(reopened["featurePipeline"], history["featurePipeline"])
            self.assertEqual(reopened["calibration"], history["calibration"])
            self.assertEqual(reopened["registration"], history["registration"])

    def test_fault_injection_rejects_invalid_production_inputs(self):
        dataset, version, _ = self._prepare_dataset()
        experiment = self.service.create_experiment({
            "datasetId": dataset["dataset_id"],
            "datasetVersionId": version["dataset_version_id"],
            "target": "ssc",
            "models": ["PLSR"],
            "preprocessing": ["RAW"],
            "validationMethod": "GroupKFold",
        })
        job = self._wait_for_job(self.service.create_training_job(experiment["experiment_id"])["job_id"])
        self.assertEqual(job["status"], "Completed", job.get("error") or job.get("message"))
        candidate = self.service.list_models()[0]
        published = self.service.publish_model(candidate["model_id"], {"setDefault": True})
        self.assertEqual(published["status"], "Default")

        production_root = self.app_dir / "trained_models"
        old_model_root = quality_prediction.MODEL_ROOT
        quality_prediction.MODEL_ROOT = production_root
        try:
            cases = {
                "missing calibration": self._fault_sample("missing_calibration", remove="calibration/dark/450.png"),
                "missing band": self._fault_sample("missing_band", remove="multispectral/560.png"),
            }
            for name, sample in cases.items():
                session, _ = build_sample_session(sample, fruit_type="blueberry", variety="Duke")
                result = predict_ssc(session)
                self.assertIsNone(result.value, name)
                self.assertNotEqual(result.status, "success", name)

            session, _ = build_sample_session(self.inspection_path, fruit_type="blueberry", variety="Duke")
            with self._metadata_override(lambda metadata: metadata["feature_pipeline"].update({"background_reference": None})):
                result = predict_ssc(session)
                self.assertIsNone(result.value)
                self.assertIn("BACKGROUND_REFERENCE_MISSING", result.error_message)

            with self._metadata_override(lambda metadata: metadata["feature_pipeline"].update({"registration_profile": None})):
                result = predict_ssc(session)
                self.assertIsNone(result.value)
                self.assertIn("REGISTRATION_PROFILE_MISSING", result.error_message)

            mismatch_profile = self.root / "production_resources" / "registration_profile_mismatch.json"
            profile_data = json.loads(self.profile_path.read_text(encoding="utf-8"))
            profile_data["matrixRgbToMultispectral"][0][2] = 1.0
            mismatch_profile.write_text(json.dumps(profile_data), encoding="utf-8")
            with self._metadata_override(lambda metadata: metadata["feature_pipeline"].update({"registration_profile": str(mismatch_profile)})):
                selected_metadata = json.loads((Path(self.service.list_models()[0]["model_dir"]) / "metadata.json").read_text(encoding="utf-8"))
                self.assertEqual(selected_metadata["feature_pipeline"]["registration_profile"], str(mismatch_profile))
                result = predict_ssc(session)
                self.assertIsNone(result.value, result.to_dict())
                self.assertIn("MODEL_INPUT_MISMATCH", result.error_message, result.to_dict())

            with self._metadata_override(lambda metadata: metadata["feature_pipeline"].update({
                "registered_roi_config": {"erosionPx": 0, "minRetainedPixelCount": 16, "minRetainedRatio": 0.25}
            })):
                result = predict_ssc(session)
                self.assertIsNone(result.value)
                self.assertIn("MODEL_INPUT_MISMATCH", result.error_message)

            with self._metadata_override(lambda metadata: metadata["model_input_contract"]["feature_schema"].update({"schema_version": 999})):
                result = predict_ssc(session)
                self.assertIsNone(result.value)
                self.assertIn("MODEL_INPUT_MISMATCH", result.error_message)

            wrong_scope, _ = build_sample_session(self.inspection_path, fruit_type="grape", variety="Fuji")
            result = predict_ssc(wrong_scope)
            self.assertIsNone(result.value)
            self.assertEqual(result.status, "model_missing")

            with self._metadata_override(lambda metadata: (metadata.pop("model_input_contract"), metadata.pop("pipeline_signature"))):
                result = predict_ssc(session)
                self.assertIsNone(result.value)
                self.assertIn("MODEL_INPUT_CONTRACT_MISSING", result.error_message)
        finally:
            quality_prediction.MODEL_ROOT = old_model_root

    def test_legacy_contract_cannot_be_published(self):
        dataset, version, _ = self._prepare_dataset()
        self.service.feature_pipeline_config = FeaturePipelineConfig.legacy(filters=self.pipeline_config.filters)
        experiment = self.service.create_experiment({
            "datasetId": dataset["dataset_id"],
            "datasetVersionId": version["dataset_version_id"],
            "target": "ssc",
            "models": ["PLSR"],
            "preprocessing": ["RAW"],
            "validationMethod": "GroupKFold",
        })
        job = self._wait_for_job(self.service.create_training_job(experiment["experiment_id"])["job_id"])
        self.assertEqual(job["status"], "Completed", job.get("error") or job.get("message"))
        candidate = self.service.list_models()[0]
        with self.assertRaisesRegex(RuntimeError, "MODEL_INPUT_CONTRACT_NOT_PRODUCTION"):
            self.service.publish_model(candidate["model_id"])

    def _prepare_dataset(self):
        dataset = self.service.create_dataset({
            "datasetName": "Synthetic Production E2E",
            "fruitType": "blueberry",
            "variety": "Duke",
            "storagePath": str(self.source_root),
        })
        imported = {"imported": 0}
        for sample_path in self.sample_paths:
            result = self.service.import_samples(dataset["dataset_id"], sample_path)
            imported["imported"] += result["imported"]
        self.assertEqual(imported["imported"], 6)
        for index in range(6):
            self.service.save_sample_label(dataset["dataset_id"], f"sample_{index:03d}", {"ssc": str(10.0 + index)})
        version = self.service.create_dataset_version(dataset["dataset_id"], "Synthetic production snapshot")
        features = self.service.generate_features(dataset["dataset_id"], version["dataset_version_id"], self.pipeline_config)
        signatures = {features["pipelineSignature"]}
        self.assertEqual(len(signatures), 1)
        return dataset, version, features

    def _fault_sample(self, sample_id: str, *, remove: str) -> Path:
        sample = build_synthetic_sample(self.source_root, sample_id, 7)
        (sample / remove).unlink()
        return sample

    def _wait_for_job(self, job_id: str) -> dict:
        for _ in range(120):
            job = self.service.get_job(job_id)
            if job["status"] in {"Completed", "Failed", "Cancelled"}:
                return job
            import time
            time.sleep(0.05)
        self.fail(f"training job did not finish: {job_id}")

    @contextmanager
    def _metadata_override(self, mutate):
        paths = set(self.app_dir.glob("trained_models/**/metadata.json"))
        paths.update(self.app_dir.glob("model_studio/models/**/metadata.json"))
        paths = sorted(paths)
        originals = [path.read_text(encoding="utf-8") for path in paths]
        metadata = json.loads(originals[-1])
        mutate(metadata)
        payload = json.dumps(metadata, ensure_ascii=False, indent=2)
        for path in paths:
            path.write_text(payload, encoding="utf-8")
        try:
            yield
        finally:
            for path, original in zip(paths, originals):
                path.write_text(original, encoding="utf-8")

    @contextmanager
    def _hardware_guard(self):
        with patch("camera_service.rgb_uvc.RgbUvcCamera.open", side_effect=AssertionError("E2E must not open RGB hardware")), \
             patch("camera_service.dvp2_mono.Dvp2MonoCamera.open", side_effect=AssertionError("E2E must not open DVP2 hardware")), \
             patch("serial_service.SerialService.connect", side_effect=AssertionError("E2E must not open serial hardware")):
            yield


if __name__ == "__main__":
    unittest.main()
