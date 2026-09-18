import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from quality_algorithm.background_reference import create_background_reference
from quality_algorithm.filters import FilterBand
from quality_algorithm.registered_roi import (
    REGISTERED_ROI_EMPTY,
    REGISTERED_ROI_EXCESSIVE_EROSION,
    REGISTERED_ROI_TARGET_RESOLUTION_MISMATCH,
    REGISTERED_ROI_TOO_SMALL,
    RegisteredRoiConfig,
    RegisteredRoiError,
    build_registered_multispectral_roi,
    erode_mask,
)
from quality_algorithm.registration import (
    REGISTRATION_METHOD_PLANAR_HOMOGRAPHY_V1,
    REGISTRATION_SCHEMA_VERSION,
    CameraRegistrationEndpoint,
    CheckerboardTarget,
    RegistrationMetrics,
    RegistrationProfile,
    warp_mask_rgb_to_multispectral,
)
from quality_algorithm.roi import apply_mask_to_image
from quality_algorithm.spectral_features import FeatureExtractionError, extract_feature_record


def _profile(matrix=None, *, rgb_size=(100, 100), ms_size=(120, 120)) -> RegistrationProfile:
    return RegistrationProfile(
        schemaVersion=REGISTRATION_SCHEMA_VERSION,
        method=REGISTRATION_METHOD_PLANAR_HOMOGRAPHY_V1,
        rgb=CameraRegistrationEndpoint(stableId="rgb-a", deviceIndex=1, width=rgb_size[0], height=rgb_size[1]),
        multispectral=CameraRegistrationEndpoint(stableId="dvp2-a", serial="DS-001", width=ms_size[0], height=ms_size[1]),
        referenceBandNm=560,
        target=CheckerboardTarget(innerCornersCols=2, innerCornersRows=2, squareSizeMm=20.0),
        matrixRgbToMultispectral=matrix or [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
        calibrationPlane="Planar checkerboard near fruit center depth.",
        metrics=RegistrationMetrics(pointCount=4, rmsePx=0.12, meanErrorPx=0.1, maxErrorPx=0.2, p95ErrorPx=0.19),
        createdAt="2026-09-12T00:00:00Z",
        sourcePairs=[],
        valid=True,
    )


class RegisteredRoiTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="registered_roi_"))
        self.profile = _profile(matrix=[[1.0, 0.0, 10.0], [0.0, 1.0, 5.0], [0.0, 0.0, 1.0]])
        self.rgb_endpoint = self.profile.rgb
        self.ms_endpoint = self.profile.multispectral

    def mask(self) -> np.ndarray:
        mask = np.zeros((100, 100), dtype=bool)
        mask[20:60, 30:70] = True
        return mask

    def test_missing_and_malformed_profile_fail(self):
        with self.assertRaisesRegex(RegisteredRoiError, "REGISTRATION_PROFILE_MISSING"):
            build_registered_multispectral_roi(
                rgb_mask=self.mask(),
                registration_profile=None,
                rgb_endpoint=self.rgb_endpoint,
                multispectral_endpoint=self.ms_endpoint,
                target_shape=(120, 120),
            )
        bad = self.root / "bad.json"
        bad.write_text(json.dumps({"schemaVersion": 1}), encoding="utf-8")
        with self.assertRaisesRegex(RegisteredRoiError, "REGISTRATION_PROFILE_INVALID"):
            build_registered_multispectral_roi(
                rgb_mask=self.mask(),
                registration_profile=bad,
                rgb_endpoint=self.rgb_endpoint,
                multispectral_endpoint=self.ms_endpoint,
                target_shape=(120, 120),
            )

    def test_runtime_endpoint_mismatch_fails(self):
        with self.assertRaisesRegex(RegisteredRoiError, "REGISTRATION_PROFILE_MISMATCH"):
            build_registered_multispectral_roi(
                rgb_mask=self.mask(),
                registration_profile=self.profile,
                rgb_endpoint=CameraRegistrationEndpoint(stableId="rgb-b", deviceIndex=1, width=100, height=100),
                multispectral_endpoint=self.ms_endpoint,
                target_shape=(120, 120),
            )
        with self.assertRaisesRegex(RegisteredRoiError, "REGISTRATION_PROFILE_MISMATCH"):
            build_registered_multispectral_roi(
                rgb_mask=self.mask(),
                registration_profile=self.profile,
                rgb_endpoint=self.rgb_endpoint,
                multispectral_endpoint=CameraRegistrationEndpoint(stableId="dvp2-a", serial="DS-002", width=120, height=120),
                target_shape=(120, 120),
            )

    def test_resolution_mismatch_fails_without_resize(self):
        with self.assertRaisesRegex(RegisteredRoiError, "REGISTRATION_PROFILE_MISMATCH"):
            build_registered_multispectral_roi(
                rgb_mask=np.zeros((80, 100), dtype=bool),
                registration_profile=self.profile,
                rgb_endpoint=self.rgb_endpoint,
                multispectral_endpoint=self.ms_endpoint,
                target_shape=(120, 120),
            )
        with self.assertRaisesRegex(RegisteredRoiError, "REGISTRATION_PROFILE_MISMATCH"):
            build_registered_multispectral_roi(
                rgb_mask=self.mask(),
                registration_profile=self.profile,
                rgb_endpoint=self.rgb_endpoint,
                multispectral_endpoint=self.ms_endpoint,
                target_shape=(119, 120),
            )
        with self.assertRaisesRegex(ValueError, REGISTERED_ROI_TARGET_RESOLUTION_MISMATCH):
            apply_mask_to_image(np.zeros((120, 120), dtype=np.float32), self.mask(), registration_mode="calibrated")

    def test_known_identity_and_translation_warp_are_bool_nearest_neighbor(self):
        identity = _profile(rgb_size=(6, 6), ms_size=(8, 8))
        mask = np.zeros((6, 6), dtype=bool)
        mask[1:3, 1:3] = True
        warped_identity = warp_mask_rgb_to_multispectral(mask, identity, (8, 8))
        self.assertEqual(warped_identity.dtype, np.bool_)
        np.testing.assert_array_equal(warped_identity[1:3, 1:3], np.ones((2, 2), dtype=bool))

        translated = _profile(matrix=[[1, 0, 2], [0, 1, 1], [0, 0, 1]], rgb_size=(6, 6), ms_size=(8, 8))
        warped = warp_mask_rgb_to_multispectral(mask, translated, (8, 8))
        expected = np.zeros((8, 8), dtype=bool)
        expected[2:4, 3:5] = True
        np.testing.assert_array_equal(warped, expected)

    def test_erosion_reduces_area_preserves_center_and_can_fail(self):
        result = build_registered_multispectral_roi(
            rgb_mask=self.mask(),
            registration_profile=self.profile,
            rgb_endpoint=self.rgb_endpoint,
            multispectral_endpoint=self.ms_endpoint,
            target_shape=(120, 120),
            config=RegisteredRoiConfig(erosionPx=2, minRetainedPixelCount=16, minRetainedRatio=0.5),
        )
        self.assertTrue(result.valid, result.errorCode)
        self.assertLess(result.erodedPixelCount, result.warpedPixelCount)
        self.assertTrue(result.mask[35, 50])
        self.assertEqual(result.boundingBox, [42, 27, 77, 62])
        self.assertAlmostEqual(result.centroid[0], 59.5)
        self.assertEqual(result.referenceBandNm, 560)
        self.assertEqual(result.registrationMetrics["rmsePx"], 0.12)

        tiny = np.zeros((100, 100), dtype=bool)
        tiny[20:22, 20:22] = True
        empty = build_registered_multispectral_roi(
            rgb_mask=tiny,
            registration_profile=self.profile,
            rgb_endpoint=self.rgb_endpoint,
            multispectral_endpoint=self.ms_endpoint,
            target_shape=(120, 120),
            config=RegisteredRoiConfig(erosionPx=2),
        )
        self.assertFalse(empty.valid)
        self.assertEqual(empty.errorCode, REGISTERED_ROI_EMPTY)

        too_small = build_registered_multispectral_roi(
            rgb_mask=self.mask(),
            registration_profile=self.profile,
            rgb_endpoint=self.rgb_endpoint,
            multispectral_endpoint=self.ms_endpoint,
            target_shape=(120, 120),
            config=RegisteredRoiConfig(erosionPx=1, minRetainedPixelCount=5000, minRetainedRatio=0.1),
        )
        self.assertEqual(too_small.errorCode, REGISTERED_ROI_TOO_SMALL)

        excessive = build_registered_multispectral_roi(
            rgb_mask=self.mask(),
            registration_profile=self.profile,
            rgb_endpoint=self.rgb_endpoint,
            multispectral_endpoint=self.ms_endpoint,
            target_shape=(120, 120),
            config=RegisteredRoiConfig(erosionPx=15, minRetainedPixelCount=1, minRetainedRatio=0.5),
        )
        self.assertEqual(excessive.errorCode, REGISTERED_ROI_EXCESSIVE_EROSION)

    def test_multiple_bands_reuse_identical_roi_geometry_and_count(self):
        sample_dir = self._make_registered_sample(calibrated=False)
        record = extract_feature_record(
            sample_dir,
            registration_mode="calibrated",
            registration_profile=self.profile,
            registration_rgb_endpoint=self.rgb_endpoint,
            registration_multispectral_endpoint=self.ms_endpoint,
            registered_roi_config=RegisteredRoiConfig(erosionPx=1, minRetainedPixelCount=20, minRetainedRatio=0.5),
        )
        self.assertEqual(record.wavelengths, [450, 560, 670])
        self.assertEqual(record.roi_pixel_count, int(np.count_nonzero(erode_mask(warp_mask_rgb_to_multispectral(self.mask(), self.profile, (120, 120)), 1))))
        self.assertLess(record.features[0], record.features[1])
        self.assertLess(record.features[1], record.features[2])

    def test_one_band_wrong_resolution_fails(self):
        sample_dir = self._make_registered_sample(calibrated=False)
        Image.fromarray(np.full((121, 120), 150, dtype=np.uint8), mode="L").save(sample_dir / "multispectral" / "670.png")
        with self.assertRaisesRegex(FeatureExtractionError, REGISTERED_ROI_TARGET_RESOLUTION_MISMATCH):
            extract_feature_record(
                sample_dir,
                registration_mode="calibrated",
                registration_profile=self.profile,
                registration_rgb_endpoint=self.rgb_endpoint,
                registration_multispectral_endpoint=self.ms_endpoint,
                registered_roi_config=RegisteredRoiConfig(erosionPx=1),
            )

    def test_background_reference_and_legacy_modes_with_calibrated_and_identity(self):
        sample_dir, reference = self._make_registered_sample(calibrated=True, with_reference=True)
        kwargs = {
            "registration_mode": "calibrated",
            "registration_profile": self.profile,
            "registration_rgb_endpoint": self.rgb_endpoint,
            "registration_multispectral_endpoint": self.ms_endpoint,
            "registered_roi_config": RegisteredRoiConfig(erosionPx=1, minRetainedPixelCount=20, minRetainedRatio=0.5),
        }
        bg_record = extract_feature_record(sample_dir, segmentation_mode="background_reference", background_reference=reference, **kwargs)
        legacy_record = extract_feature_record(sample_dir, segmentation_mode="legacy_color", **kwargs)
        identity_bg = extract_feature_record(sample_dir, segmentation_mode="background_reference", background_reference=reference)
        identity_legacy = extract_feature_record(sample_dir, segmentation_mode="legacy_color")
        self.assertEqual(bg_record.roi_pixel_count, legacy_record.roi_pixel_count)
        self.assertGreater(identity_bg.roi_pixel_count, 0)
        self.assertGreater(identity_legacy.roi_pixel_count, 0)

    def test_dark_white_math_is_unchanged_except_roi_location(self):
        sample_dir, reference = self._make_registered_sample(calibrated=True, with_reference=True)
        record = extract_feature_record(
            sample_dir,
            segmentation_mode="background_reference",
            background_reference=reference,
            registration_mode="calibrated",
            registration_profile=self.profile,
            registration_rgb_endpoint=self.rgb_endpoint,
            registration_multispectral_endpoint=self.ms_endpoint,
            registered_roi_config=RegisteredRoiConfig(erosionPx=1, minRetainedPixelCount=20, minRetainedRatio=0.5),
        )
        self.assertTrue(record.calibrated)
        self.assertAlmostEqual(record.features[0], 50 / 255, places=4)
        self.assertAlmostEqual(record.features[1], 100 / 255, places=4)
        self.assertAlmostEqual(record.features[2], 150 / 255, places=4)

    def test_uncalibrated_fallback_and_runtime_mismatch(self):
        sample_dir = self._make_registered_sample(calibrated=False)
        record = extract_feature_record(
            sample_dir,
            registration_mode="calibrated",
            registration_profile=self.profile,
            registration_rgb_endpoint=self.rgb_endpoint,
            registration_multispectral_endpoint=self.ms_endpoint,
            registered_roi_config=RegisteredRoiConfig(erosionPx=1, minRetainedPixelCount=20, minRetainedRatio=0.5),
        )
        self.assertFalse(record.calibrated)
        self.assertIn("UNCALIBRATED", record.warnings)
        with self.assertRaisesRegex(FeatureExtractionError, "REGISTRATION_PROFILE_MISMATCH"):
            extract_feature_record(
                sample_dir,
                registration_mode="calibrated",
                registration_profile=self.profile,
                registration_rgb_endpoint=CameraRegistrationEndpoint(stableId="rgb-x", deviceIndex=1, width=100, height=100),
                registration_multispectral_endpoint=self.ms_endpoint,
            )

    def _make_registered_sample(self, *, calibrated=False, with_reference=False):
        sample_dir = self.root / ("sample_cal" if calibrated else "sample_uncal")
        rgb_dir = sample_dir / "rgb"
        ms_dir = sample_dir / "multispectral"
        rgb_dir.mkdir(parents=True)
        ms_dir.mkdir()
        bg = np.zeros((100, 100, 3), dtype=np.uint8)
        bg[:, :] = [20, 22, 24]
        rgb = bg.copy()
        rgb[20:60, 30:70] = [80, 130, 70]
        Image.fromarray(rgb).save(rgb_dir / "rgb_001.png")
        values = {450: 50, 560: 100, 670: 150}
        for wavelength, value in values.items():
            band = np.full((120, 120), 200, dtype=np.uint8)
            band[25:65, 40:80] = value
            Image.fromarray(band, mode="L").save(ms_dir / f"{wavelength}.png")
        if calibrated:
            dark = sample_dir / "calibration" / "dark"
            white = sample_dir / "calibration" / "white"
            dark.mkdir(parents=True)
            white.mkdir(parents=True)
            for wavelength in values:
                Image.fromarray(np.zeros((120, 120), dtype=np.uint8), mode="L").save(dark / f"dark_{wavelength}.png")
                Image.fromarray(np.full((120, 120), 255, dtype=np.uint8), mode="L").save(white / f"white_{wavelength}.png")
        if not with_reference:
            return sample_dir
        bg_path = self.root / "background.png"
        Image.fromarray(bg).save(bg_path)
        return sample_dir, create_background_reference(bg_path)


if __name__ == "__main__":
    unittest.main()
