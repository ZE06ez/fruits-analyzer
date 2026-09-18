import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image

from model_studio.service import ModelStudioService
from quality_algorithm.analysis_pipeline import FeaturePipelineConfig, canonical_contract_json, pipeline_signature, run_feature_pipeline
from quality_algorithm.background_reference import create_background_reference
from quality_algorithm.model_io import MODEL_INPUT_CONTRACT_MISSING, ModelBundle, ModelInputMismatch, predict_feature_record
from quality_algorithm.registered_roi import RegisteredRoiConfig
from quality_algorithm.registration import (
    REGISTRATION_METHOD_PLANAR_HOMOGRAPHY_V1,
    REGISTRATION_SCHEMA_VERSION,
    CameraRegistrationEndpoint,
    CheckerboardTarget,
    RegistrationMetrics,
    RegistrationProfile,
)
from quality_algorithm.spectral_features import FeatureExtractionError
from quality_prediction import build_prediction_feature_record, build_sample_session


class AnalysisPipelineTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="fta_analysis_pipeline_"))

    def test_training_and_prediction_call_shared_pipeline(self):
        app_dir = self.root / "app"
        sample = self._make_legacy_sample("sample_001")
        service = ModelStudioService(app_dir)
        service.feature_pipeline_config = FeaturePipelineConfig.legacy()
        dataset = service.create_dataset({"datasetName": "Pipeline", "fruitType": "blueberry"})
        service.import_samples(dataset["dataset_id"], sample)
        service.save_sample_label(dataset["dataset_id"], "sample_001", {"ssc": "10.0"})
        version = service.create_dataset_version(dataset["dataset_id"], "V1")

        with patch("model_studio.service.run_feature_pipeline") as training_pipeline:
            training_pipeline.return_value = run_feature_pipeline(sample, sample_id="sample_001", config=FeaturePipelineConfig.legacy())
            service.generate_features(dataset["dataset_id"], version["dataset_version_id"])
        self.assertTrue(training_pipeline.called)

        session, _report = build_sample_session(sample, sample_id="sample_001")
        with patch("quality_prediction.run_feature_pipeline") as prediction_pipeline:
            prediction_pipeline.return_value = run_feature_pipeline(sample, sample_id="sample_001", config=FeaturePipelineConfig.legacy())
            build_prediction_feature_record(session, {"feature_pipeline": FeaturePipelineConfig.legacy().to_dict()})
        self.assertTrue(prediction_pipeline.called)

    def test_production_missing_dependencies_do_not_fallback(self):
        sample = self._make_legacy_sample("sample_missing")
        with self.assertRaisesRegex(FeatureExtractionError, "CALIBRATION_MISSING"):
            run_feature_pipeline(sample, config=FeaturePipelineConfig.production())

        sample_with_cal = self._make_production_sample("sample_no_bg", include_metadata=False)
        with self.assertRaisesRegex(FeatureExtractionError, "BACKGROUND_REFERENCE_MISSING"):
            run_feature_pipeline(sample_with_cal, config=FeaturePipelineConfig.production(registration_profile=self._profile(), registration_rgb_endpoint=self._profile().rgb, registration_multispectral_endpoint=self._profile().multispectral))

        reference = create_background_reference(sample_with_cal / "background.png")
        with self.assertRaisesRegex(FeatureExtractionError, "REGISTRATION_PROFILE_MISSING"):
            run_feature_pipeline(sample_with_cal, config=FeaturePipelineConfig.production(background_reference=reference))

    def test_explicit_legacy_development_config_keeps_old_flow(self):
        sample = self._make_legacy_sample("sample_legacy")
        record = run_feature_pipeline(sample, config=FeaturePipelineConfig.legacy())
        self.assertFalse(record.calibrated)
        self.assertEqual(record.wavelengths, [450, 560, 670])
        self.assertIn("UNCALIBRATED", record.warnings)

    def test_training_and_prediction_feature_records_match(self):
        sample = self._make_production_sample("sample_prod")
        config = self._production_config(sample)
        app_dir = self.root / "app_prod"
        service = ModelStudioService(app_dir)
        service.feature_pipeline_config = config
        dataset = service.create_dataset({"datasetName": "Production Pipeline", "fruitType": "blueberry"})
        service.import_samples(dataset["dataset_id"], sample)
        service.save_sample_label(dataset["dataset_id"], "sample_prod", {"ssc": "10.5", "ta": "0.4", "ph": "3.5"})
        version = service.create_dataset_version(dataset["dataset_id"], "V1")

        features = service.generate_features(dataset["dataset_id"], version["dataset_version_id"])
        with Path(features["featureCsv"]).open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        session, _report = build_sample_session(sample, sample_id="sample_prod")
        prediction_record = build_prediction_feature_record(session, {"feature_pipeline": config.to_dict()})

        self.assertEqual([float(rows[0][f"R{w}"]) for w in prediction_record.wavelengths], prediction_record.features)
        self.assertTrue(prediction_record.calibrated)
        self.assertEqual(features["modelInputContract"], prediction_record.model_input_contract)
        self.assertEqual(features["pipelineSignature"], prediction_record.pipeline_signature)

    def test_pipeline_signature_is_canonical_and_semantic(self):
        sample = self._make_production_sample("sample_signature")
        base = run_feature_pipeline(sample, config=self._production_config(sample))
        same_profile_different_runtime_noise = self._profile(
            created_at="2030-01-01T00:00:00Z",
            source_pairs=[{"rgbPath": "C:/tmp/a.png", "multispectralPath": "C:/tmp/b.png"}],
        )
        same = run_feature_pipeline(
            sample,
            config=FeaturePipelineConfig.production(
                background_reference=str(sample / "background.png"),
                registration_profile=same_profile_different_runtime_noise,
                registration_rgb_endpoint=same_profile_different_runtime_noise.rgb,
                registration_multispectral_endpoint=same_profile_different_runtime_noise.multispectral,
            ),
        )
        self.assertEqual(base.pipeline_signature, pipeline_signature(base.model_input_contract))
        self.assertEqual(base.pipeline_signature, pipeline_signature(json.loads(canonical_contract_json(base.model_input_contract))))
        self.assertEqual(base.pipeline_signature, same.pipeline_signature)

        changed_profile = self._profile(matrix=[[1.0, 0.0, 1.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]])
        registration_changed = run_feature_pipeline(
            sample,
            config=FeaturePipelineConfig.production(
                background_reference=create_background_reference(sample / "background.png"),
                registration_profile=changed_profile,
                registration_rgb_endpoint=changed_profile.rgb,
                registration_multispectral_endpoint=changed_profile.multispectral,
            ),
        )
        roi_changed = run_feature_pipeline(
            sample,
            config=FeaturePipelineConfig.production(
                background_reference=create_background_reference(sample / "background.png"),
                registration_profile=self._profile(),
                registration_rgb_endpoint=self._profile().rgb,
                registration_multispectral_endpoint=self._profile().multispectral,
                registered_roi_config=RegisteredRoiConfig(erosionPx=3, minRetainedPixelCount=16, minRetainedRatio=0.25),
            ),
        )
        legacy_changed = run_feature_pipeline(sample, config=FeaturePipelineConfig.legacy())
        self.assertNotEqual(base.pipeline_signature, registration_changed.pipeline_signature)
        self.assertNotEqual(base.pipeline_signature, roi_changed.pipeline_signature)
        self.assertNotEqual(base.pipeline_signature, legacy_changed.pipeline_signature)

    def test_pipeline_signature_reads_semantics_from_profile_and_filter_paths(self):
        sample = self._make_production_sample("profile_path")
        profile = self._profile()
        profile_path = self.root / "profile.json"
        profile.save_json(profile_path)
        config = self._production_config(sample)
        config.registration_profile = profile_path
        config.filters = [band for band in config.filters or []]
        record = run_feature_pipeline(sample, config=config)
        profile_data = json.loads(profile_path.read_text(encoding="utf-8"))
        profile_data["matrixRgbToMultispectral"][0][2] = 1.0
        changed_path = self.root / "profile_changed.json"
        changed_path.write_text(json.dumps(profile_data), encoding="utf-8")
        changed = run_feature_pipeline(sample, config=self._production_config(sample, registration_profile=changed_path))
        self.assertNotEqual(record.pipeline_signature, changed.pipeline_signature)

    def test_model_input_contract_blocks_prediction_before_model_call(self):
        production_sample = self._make_production_sample("sample_contract_prod")
        legacy_sample = self._make_legacy_sample("sample_contract_legacy")
        expected_record = run_feature_pipeline(production_sample, config=self._production_config(production_sample))
        actual_record = run_feature_pipeline(legacy_sample, config=FeaturePipelineConfig.legacy())
        model = _TrackingModel()
        bundle = ModelBundle(
            model=model,
            metadata={
                "wavelengths_nm": expected_record.wavelengths,
                "model_input_contract": expected_record.model_input_contract,
                "pipeline_signature": expected_record.pipeline_signature,
                "preprocessing_state": {"method": "RAW"},
            },
        )
        with self.assertRaisesRegex(ModelInputMismatch, "segmentation mismatch|registration mismatch|ROI config mismatch"):
            predict_feature_record(bundle, actual_record)
        self.assertFalse(model.called)

    def test_missing_contract_requires_explicit_legacy_compatibility(self):
        sample = self._make_legacy_sample("sample_old_model")
        record = run_feature_pipeline(sample, config=FeaturePipelineConfig.legacy())
        metadata = {
            "wavelengths_nm": record.wavelengths,
            "feature_pipeline": FeaturePipelineConfig.legacy().to_dict(),
            "preprocessing_state": {"method": "RAW"},
        }
        strict_model = _TrackingModel()
        with self.assertRaisesRegex(ModelInputMismatch, MODEL_INPUT_CONTRACT_MISSING):
            predict_feature_record(ModelBundle(strict_model, metadata), record)
        self.assertFalse(strict_model.called)

        legacy_model = _TrackingModel()
        value = predict_feature_record(
            ModelBundle(legacy_model, metadata),
            record,
            allow_legacy_missing_contract=True,
        )
        self.assertTrue(legacy_model.called)
        self.assertEqual(value, 42.0)

    def _make_legacy_sample(self, name: str) -> Path:
        sample = self.root / name
        (sample / "rgb").mkdir(parents=True)
        (sample / "multispectral").mkdir()
        rgb = np.zeros((24, 24, 3), dtype=np.uint8)
        rgb[6:18, 6:18] = [80, 130, 70]
        Image.fromarray(rgb).save(sample / "rgb" / "rgb_001.png")
        for band, value in [(450, 70), (560, 95), (670, 120)]:
            Image.new("L", (24, 24), value).save(sample / "multispectral" / f"{band}.png")
        return sample

    def _make_production_sample(self, name: str, *, include_metadata: bool = True) -> Path:
        sample = self.root / name
        (sample / "rgb").mkdir(parents=True)
        (sample / "multispectral").mkdir()
        bg = np.zeros((100, 100, 3), dtype=np.uint8)
        bg[:, :] = [20, 22, 24]
        rgb = bg.copy()
        rgb[25:75, 25:75] = [80, 130, 70]
        Image.fromarray(bg).save(sample / "background.png")
        Image.fromarray(rgb).save(sample / "rgb" / "rgb_001.png")
        (sample / "calibration" / "dark").mkdir(parents=True)
        (sample / "calibration" / "white").mkdir(parents=True)
        for band, value in [(450, 50), (560, 100), (670, 150)]:
            Image.new("L", (100, 100), value).save(sample / "multispectral" / f"{band}.png")
            Image.new("L", (100, 100), 0).save(sample / "calibration" / "dark" / f"{band}.png")
            Image.new("L", (100, 100), 255).save(sample / "calibration" / "white" / f"{band}.png")
        if include_metadata:
            metadata = {
                "sample_id": name,
                "background_reference": {"backgroundReferencePath": str(sample / "background.png")},
                "analysis_pipeline": {
                    "registrationProfile": self._profile().to_dict(),
                    "registrationRgbEndpoint": self._profile().rgb.to_dict(),
                    "registrationMultispectralEndpoint": self._profile().multispectral.to_dict(),
                },
            }
            (sample / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
        return sample

    def _production_config(self, sample: Path, **overrides) -> FeaturePipelineConfig:
        values = {
            "background_reference": create_background_reference(sample / "background.png"),
            "registration_profile": self._profile(),
            "registration_rgb_endpoint": self._profile().rgb,
            "registration_multispectral_endpoint": self._profile().multispectral,
        }
        values.update(overrides)
        return FeaturePipelineConfig.production(
            **values,
        )

    def _profile(self, *, matrix: list[list[float]] | None = None, created_at: str = "2026-09-16T00:00:00Z", source_pairs: list[dict] | None = None) -> RegistrationProfile:
        endpoint = CameraRegistrationEndpoint(stableId="cam-a", deviceIndex=1, width=100, height=100)
        ms_endpoint = CameraRegistrationEndpoint(stableId="dvp2-a", serial="DS-001", width=100, height=100)
        return RegistrationProfile(
            schemaVersion=REGISTRATION_SCHEMA_VERSION,
            method=REGISTRATION_METHOD_PLANAR_HOMOGRAPHY_V1,
            rgb=endpoint,
            multispectral=ms_endpoint,
            referenceBandNm=560,
            target=CheckerboardTarget(innerCornersCols=2, innerCornersRows=2, squareSizeMm=20.0),
            matrixRgbToMultispectral=matrix or [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
            calibrationPlane="Planar checkerboard near fruit center depth.",
            metrics=RegistrationMetrics(pointCount=4, rmsePx=0.1, meanErrorPx=0.1, maxErrorPx=0.1, p95ErrorPx=0.1),
            createdAt=created_at,
            sourcePairs=source_pairs or [],
            valid=True,
        )


class _TrackingModel:
    def __init__(self):
        self.called = False

    def predict(self, x):
        self.called = True
        return [42.0]


if __name__ == "__main__":
    unittest.main()
