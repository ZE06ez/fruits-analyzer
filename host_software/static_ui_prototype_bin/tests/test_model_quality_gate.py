from __future__ import annotations

import json
import gc
import tempfile
import unittest
from pathlib import Path

from model_studio.service import ModelStudioError, ModelStudioService
from quality_algorithm.analysis_pipeline import FeaturePipelineConfig, build_model_input_contract, pipeline_signature
from quality_algorithm.model_quality import ModelQualityPolicy, evaluate_model_quality, feature_distribution_status


class ModelQualityGateTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="fta_model_quality_")
        self.root = Path(self.temp_dir.name)
        self.service = ModelStudioService(self.root / "app", quality_policy=self.policy())

    def tearDown(self):
        self.service = None
        gc.collect()
        self.temp_dir.cleanup()

    @staticmethod
    def policy(**overrides) -> dict:
        value = {
            "schema_version": 1,
            "policy_version": "test-v1",
            "target": "ssc",
            "minimum_sample_count": 5,
            "minimum_r2": 0.7,
            "maximum_rmse": 0.5,
            "maximum_mae": 0.4,
            "minimum_rpd": 1.5,
            "require_validation": True,
            "require_dataset_version": True,
            "allow_warn_publish": False,
        }
        value.update(overrides)
        return value

    def _candidate(self, model_id: str, **metadata_overrides) -> str:
        dataset = self.service.create_dataset({
            "datasetName": f"Quality {model_id}",
            "fruitType": "blueberry",
            "variety": "Duke",
        })
        version = self.service.create_dataset_version(dataset["dataset_id"], f"Quality {model_id}")
        model_dir = self.service.model_dir / "candidates" / model_id
        model_dir.mkdir(parents=True, exist_ok=True)
        (model_dir / "model.joblib").write_bytes(b"fake")
        config = FeaturePipelineConfig.production()
        contract = build_model_input_contract(
            config=config,
            wavelengths=[450, 560, 670],
            calibrated=True,
            feature_names=["R450", "R560", "R670"],
        ).to_dict()
        metadata = {
            "model_id": model_id,
            "target": "ssc",
            "model_type": "PLSR",
            "model_version": "v1",
            "preprocessing": "RAW",
            "wavelengths_nm": [450, 560, 670],
            "feature_names": ["R450", "R560", "R670"],
            "preprocessing_state": {"method": "RAW"},
            "calibration_required": True,
            "model_input_contract": contract,
            "pipeline_signature": pipeline_signature(contract),
            "dataset_id": dataset["dataset_id"],
            "dataset_version_id": version["dataset_version_id"],
            "fruit_type": "blueberry",
            "variety": "Duke",
            "sample_count": 10,
            "validation_method": "GroupKFold_by_sample_id",
            "r2": 0.8,
            "rmse": 0.2,
            "mae": 0.15,
            "rpd": 2.0,
        }
        metadata.update(metadata_overrides)
        (model_dir / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
        with self.service.connect() as conn:
            conn.execute(
                """
                INSERT INTO models(model_id,model_name,display_name,target,fruit_type,variety,model_type,preprocessing,version,status,is_default,dataset_id,dataset_version_id,dataset_version_label,model_dir,metadata_json,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (model_id, model_id, model_id, "ssc", "blueberry", "Duke", "PLSR", "RAW", "v1", "Candidate", 0,
                 dataset["dataset_id"], version["dataset_version_id"], version["version_name"], str(model_dir), json.dumps(metadata), "2026-01-01"),
            )
        return model_id

    def test_good_model_passes_publish_and_default(self):
        model_id = self._candidate("good")
        report = self.service.get_model_quality(model_id)
        self.assertEqual(report["overall_status"], "PASS")
        published = self.service.publish_model(model_id)
        self.assertEqual(published["status"], "Published")
        default = self.service.set_default_model(model_id)
        self.assertEqual(default["status"], "Default")
        self.assertEqual(default["qualityStatus"], "PASS")

    def test_sample_count_too_low_blocks_publish(self):
        model_id = self._candidate("low_samples", sample_count=4)
        report = self.service.get_model_quality(model_id)
        self.assertEqual(report["checks"]["sample_count"]["error_code"], "MODEL_QUALITY_SAMPLE_COUNT_TOO_LOW")
        with self.assertRaisesRegex(ModelStudioError, "MODEL_QUALITY_GATE_FAILED"):
            self.service.publish_model(model_id)

    def test_metric_thresholds_block_r2_rmse_mae_and_rpd(self):
        cases = (
            ("r2", {"r2": 0.2}, "r2"),
            ("rmse", {"rmse": 0.9}, "rmse"),
            ("mae", {"mae": 0.9}, "mae"),
            ("rpd", {"rpd": 1.0}, "rpd"),
        )
        for suffix, changes, check_name in cases:
            with self.subTest(check=check_name):
                model_id = self._candidate(f"bad_{suffix}", **changes)
                report = self.service.get_model_quality(model_id)
                self.assertEqual(report["overall_status"], "FAIL")
                self.assertEqual(report["checks"][check_name]["status"], "FAIL")

    def test_missing_metric_is_not_available_and_blocking(self):
        model_id = self._candidate("missing_r2", r2=None)
        report = self.service.get_model_quality(model_id)
        self.assertEqual(report["checks"]["r2"]["status"], "NOT_AVAILABLE")
        self.assertEqual(report["overall_status"], "FAIL")
        with self.assertRaisesRegex(ModelStudioError, "MODEL_QUALITY_GATE_FAILED"):
            self.service.publish_model(model_id)

    def test_missing_validation_is_blocking(self):
        model_id = self._candidate("missing_validation", validation_method=None)
        report = self.service.get_model_quality(model_id)
        self.assertEqual(report["checks"]["validation"]["error_code"], "MODEL_VALIDATION_MISSING")
        self.assertEqual(report["overall_status"], "FAIL")
        with self.assertRaisesRegex(ModelStudioError, "MODEL_QUALITY_GATE_FAILED"):
            self.service.publish_model(model_id)

    def test_invalid_production_contract_remains_hard_fail(self):
        config = FeaturePipelineConfig.legacy()
        contract = build_model_input_contract(config=config, wavelengths=[450, 560, 670], calibrated=False, feature_names=["R450", "R560", "R670"]).to_dict()
        model_id = self._candidate("legacy_contract", model_input_contract=contract, pipeline_signature=pipeline_signature(contract))
        with self.assertRaisesRegex(ModelStudioError, "MODEL_INPUT_CONTRACT_NOT_PRODUCTION"):
            self.service.publish_model(model_id)

    def test_non_blocking_warning_can_publish_when_policy_allows_it(self):
        self.service.quality_policy_source = self.policy(
            policy_version="warn-v1",
            minimum_r2=0.9,
            blocking={"r2": False},
            allow_warn_publish=True,
        )
        model_id = self._candidate("warning")
        report = self.service.get_model_quality(model_id)
        self.assertEqual(report["overall_status"], "WARN")
        self.assertEqual(report["checks"]["r2"]["status"], "FAIL")
        self.assertEqual(self.service.publish_model(model_id)["status"], "Published")

    def test_policy_can_disable_an_individual_check(self):
        self.service.quality_policy_source = self.policy(
            minimum_r2=0.99,
            checks={"r2": {"enabled": False, "threshold": 0.99}},
        )
        model_id = self._candidate("disabled_r2")
        report = self.service.get_model_quality(model_id)
        self.assertFalse(report["checks"]["r2"]["enabled"])
        self.assertEqual(report["checks"]["r2"]["status"], "NOT_AVAILABLE")
        self.assertEqual(report["overall_status"], "PASS")

    def test_policy_version_change_re_evaluates_and_replaces_report(self):
        model_id = self._candidate("policy_change")
        first = self.service.get_model_quality(model_id)
        self.assertEqual(first["policy_version"], "test-v1")
        self.service.quality_policy_source = self.policy(policy_version="test-v2", minimum_r2=0.95)
        second = self.service.get_model_quality(model_id)
        self.assertEqual(second["policy_version"], "test-v2")
        stored = json.loads((Path(self.service.get_model(model_id)["model_dir"]) / "metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(stored["quality_policy_version"], "test-v2")

    def test_default_gate_rechecks_quality_for_published_model(self):
        model_id = self._candidate("default_recheck")
        self.service.publish_model(model_id)
        metadata_path = Path(self.service.get_model(model_id)["model_dir"]) / "metadata.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata["r2"] = 0.1
        metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
        with self.assertRaisesRegex(ModelStudioError, "MODEL_QUALITY_GATE_FAILED"):
            self.service.set_default_model(model_id)

    def test_feature_range_helper_is_deterministic_and_physical_bounds_are_configurable(self):
        metadata = {"feature_statistics": {"R450": {"min": 0.0, "max": 1.0}}}
        self.assertEqual(feature_distribution_status({"R450": 0.5}, metadata), "IN_RANGE")
        self.assertEqual(feature_distribution_status({"R450": 2.0}, metadata), "OUT_OF_RANGE")
        report = evaluate_model_quality(
            {"target": "ssc", "sample_count": 10, "validation_method": "GroupKFold", "r2": 0.8, "rmse": 0.2, "mae": 0.1, "rpd": 2.0, "target_range": {"min": 1, "max": 20}},
            policy=self.policy(physical_min=2),
            model={"target": "ssc", "dataset_id": "d", "dataset_version_id": "v", "fruit_type": "blueberry", "variety": "Duke"},
            dataset_version={"dataset_id": "d"},
        )
        self.assertEqual(report.to_dict()["checks"]["physical_bounds"]["status"], "FAIL")


if __name__ == "__main__":
    unittest.main()
