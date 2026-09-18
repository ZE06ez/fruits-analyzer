import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import numpy as np

from quality_algorithm.registration import (
    REGISTRATION_METHOD_PLANAR_HOMOGRAPHY_V1,
    REGISTRATION_SCHEMA_VERSION,
    CameraRegistrationEndpoint,
    CheckerboardTarget,
    RegistrationError,
    RegistrationMetrics,
    RegistrationProfile,
    RegistrationProfileMismatch,
    build_registration_profile,
    compute_reprojection_errors,
    detect_checkerboard_corners,
    estimate_planar_homography,
    project_points,
    reprojection_metrics,
    validate_homography_matrix,
    validate_profile_for_runtime,
    warp_mask_rgb_to_multispectral,
)
from quality_algorithm.roi import apply_mask_to_image


def _profile(
    matrix=None,
    *,
    rgb=None,
    multispectral=None,
    point_count=4,
) -> RegistrationProfile:
    return RegistrationProfile(
        schemaVersion=REGISTRATION_SCHEMA_VERSION,
        method=REGISTRATION_METHOD_PLANAR_HOMOGRAPHY_V1,
        rgb=rgb or CameraRegistrationEndpoint(stableId="rgb-a", deviceIndex=1, width=6, height=6),
        multispectral=multispectral or CameraRegistrationEndpoint(stableId="dvp2-a", serial="DS-001", width=8, height=8),
        referenceBandNm=560,
        target=CheckerboardTarget(innerCornersCols=2, innerCornersRows=2, squareSizeMm=20.0),
        matrixRgbToMultispectral=matrix or [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
        calibrationPlane="Planar checkerboard near fruit center depth.",
        metrics=RegistrationMetrics(pointCount=point_count, rmsePx=0.0, meanErrorPx=0.0, maxErrorPx=0.0, p95ErrorPx=0.0),
        createdAt="2026-09-12T00:00:00Z",
        sourcePairs=[],
        valid=True,
    )


def _normalize_h(h):
    h = np.asarray(h, dtype=np.float64)
    return h / h[2, 2]


class RegistrationTests(unittest.TestCase):
    def test_profile_serialize_deserialize_roundtrip(self):
        folder = Path(tempfile.mkdtemp(prefix="fta_registration_profile_"))
        path = folder / "registration_profile.json"
        profile = _profile()

        profile.save_json(path)
        loaded = RegistrationProfile.from_json_file(path)

        self.assertEqual(loaded.schemaVersion, REGISTRATION_SCHEMA_VERSION)
        self.assertEqual(loaded.method, REGISTRATION_METHOD_PLANAR_HOMOGRAPHY_V1)
        self.assertEqual(loaded.rgb.deviceIndex, 1)
        self.assertEqual(loaded.multispectral.serial, "DS-001")
        self.assertEqual(loaded.to_dict(), profile.to_dict())

    def test_homography_matrix_validation_rejects_bad_shapes_and_singular(self):
        valid = validate_homography_matrix([[1, 0, 0], [0, 1, 0], [0, 0, 1]])
        self.assertEqual(valid.shape, (3, 3))
        with self.assertRaises(RegistrationError):
            validate_homography_matrix([[1, 0], [0, 1]])
        with self.assertRaises(RegistrationError):
            validate_homography_matrix([[1, 0, 0], [2, 0, 0], [0, 0, 1]])
        with self.assertRaises(RegistrationError):
            validate_homography_matrix([[1, 0, 0], [0, float("nan"), 0], [0, 0, 1]])

    def test_identity_homography_has_zero_reprojection_error(self):
        points = np.array([[0, 0], [10, 0], [0, 10], [10, 10], [5, 5]], dtype=np.float64)
        errors = compute_reprojection_errors(points, points, np.eye(3))

        np.testing.assert_allclose(errors, np.zeros(points.shape[0]), atol=1e-9)

    def test_known_translation_homography_is_estimated(self):
        rgb_points = np.array([[0, 0], [10, 0], [0, 10], [10, 10], [5, 5], [20, 10]], dtype=np.float64)
        expected_h = np.array([[1, 0, 3], [0, 1, 7], [0, 0, 1]], dtype=np.float64)
        ms_points = project_points(rgb_points, expected_h)

        actual_h, inliers, metrics = estimate_planar_homography(rgb_points, ms_points, ransac_reproj_threshold=0.5)

        np.testing.assert_allclose(_normalize_h(actual_h), expected_h, atol=1e-7)
        self.assertEqual(int(np.count_nonzero(inliers)), rgb_points.shape[0])
        self.assertAlmostEqual(metrics.rmsePx, 0.0, places=7)

    def test_known_perspective_transform_is_estimated(self):
        xs, ys = np.meshgrid(np.linspace(0, 100, 5), np.linspace(0, 80, 4))
        rgb_points = np.column_stack([xs.reshape(-1), ys.reshape(-1)])
        expected_h = np.array([[1.2, 0.08, 5.0], [-0.04, 0.92, 10.0], [0.001, -0.0004, 1.0]], dtype=np.float64)
        ms_points = project_points(rgb_points, expected_h)

        actual_h, inliers, metrics = estimate_planar_homography(rgb_points, ms_points, ransac_reproj_threshold=0.5)

        np.testing.assert_allclose(_normalize_h(actual_h), expected_h, atol=1e-6)
        self.assertEqual(int(np.count_nonzero(inliers)), rgb_points.shape[0])
        self.assertLess(metrics.maxErrorPx, 1e-5)

    def test_build_registration_profile_records_metrics(self):
        rgb_points = np.array([[0, 0], [20, 0], [0, 20], [20, 20], [10, 10]], dtype=np.float64)
        ms_points = rgb_points + np.array([4.0, 2.0])

        profile, inliers = build_registration_profile(
            rgb_points=rgb_points,
            multispectral_points=ms_points,
            rgb=CameraRegistrationEndpoint(deviceIndex=1, width=1920, height=1080),
            multispectral=CameraRegistrationEndpoint(serial="DS-001", width=2048, height=1200),
            target=CheckerboardTarget(innerCornersCols=2, innerCornersRows=2, squareSizeMm=20.0),
            reference_band_nm=560,
        )

        self.assertTrue(profile.valid)
        self.assertEqual(profile.metrics.pointCount, int(np.count_nonzero(inliers)))
        self.assertLess(profile.metrics.rmsePx, 1e-6)

    def test_warp_mask_uses_nearest_neighbor(self):
        mask = np.zeros((6, 6), dtype=bool)
        mask[1:3, 1:3] = True
        profile = _profile(matrix=[[1.0, 0.0, 2.0], [0.0, 1.0, 1.0], [0.0, 0.0, 1.0]])

        warped = warp_mask_rgb_to_multispectral(mask, profile, (8, 8))

        expected = np.zeros((8, 8), dtype=bool)
        expected[2:4, 3:5] = True
        np.testing.assert_array_equal(warped, expected)

    def test_source_resolution_mismatch_is_rejected(self):
        profile = _profile()
        with self.assertRaises(RegistrationProfileMismatch):
            warp_mask_rgb_to_multispectral(np.zeros((5, 6), dtype=bool), profile, (8, 8))

    def test_target_resolution_mismatch_is_rejected(self):
        profile = _profile()
        with self.assertRaises(RegistrationProfileMismatch):
            validate_profile_for_runtime(
                profile,
                rgb=profile.rgb,
                multispectral=CameraRegistrationEndpoint(stableId="dvp2-a", serial="DS-001", width=7, height=8),
                output_shape=(8, 8),
            )
        with self.assertRaises(RegistrationProfileMismatch):
            validate_profile_for_runtime(profile, rgb=profile.rgb, multispectral=profile.multispectral, output_shape=(7, 8))

    def test_rgb_identity_mismatch_is_rejected(self):
        profile = _profile()
        with self.assertRaises(RegistrationProfileMismatch):
            validate_profile_for_runtime(
                profile,
                rgb=CameraRegistrationEndpoint(stableId="rgb-b", deviceIndex=1, width=6, height=6),
                multispectral=profile.multispectral,
            )
        with self.assertRaises(RegistrationProfileMismatch):
            validate_profile_for_runtime(
                profile,
                rgb=CameraRegistrationEndpoint(stableId="rgb-a", deviceIndex=None, width=6, height=6),
                multispectral=profile.multispectral,
            )

    def test_dvp2_identity_mismatch_is_rejected(self):
        profile = _profile()
        with self.assertRaises(RegistrationProfileMismatch):
            validate_profile_for_runtime(
                profile,
                rgb=profile.rgb,
                multispectral=CameraRegistrationEndpoint(stableId="dvp2-a", serial="DS-002", width=8, height=8),
            )
        with self.assertRaises(RegistrationProfileMismatch):
            validate_profile_for_runtime(
                profile,
                rgb=profile.rgb,
                multispectral=CameraRegistrationEndpoint(stableId="", serial="DS-001", width=8, height=8),
            )

    def test_checkerboard_detection_failure_is_explicit(self):
        blank = np.zeros((80, 80), dtype=np.uint8)

        with self.assertRaisesRegex(RegistrationError, "CHECKERBOARD_CORNERS_NOT_FOUND"):
            detect_checkerboard_corners(blank, inner_corners_cols=9, inner_corners_rows=6)

    def test_insufficient_correspondences_are_rejected(self):
        points = np.array([[0, 0], [1, 0], [0, 1]], dtype=np.float64)
        with self.assertRaises(RegistrationError):
            estimate_planar_homography(points, points)

    def test_reprojection_metrics_are_deterministic(self):
        metrics = reprojection_metrics(np.array([0.0, 3.0, 4.0, 0.0], dtype=np.float64))

        self.assertEqual(metrics.pointCount, 4)
        self.assertAlmostEqual(metrics.rmsePx, 2.5)
        self.assertAlmostEqual(metrics.meanErrorPx, 1.75)
        self.assertAlmostEqual(metrics.maxErrorPx, 4.0)
        self.assertAlmostEqual(metrics.p95ErrorPx, float(np.percentile([0.0, 3.0, 4.0, 0.0], 95)))

    def test_profile_missing_or_malformed_is_rejected(self):
        folder = Path(tempfile.mkdtemp(prefix="fta_registration_bad_profile_"))
        with self.assertRaises(RegistrationError):
            RegistrationProfile.from_json_file(folder / "missing.json")

        bad_json = folder / "bad.json"
        bad_json.write_text("{not json", encoding="utf-8")
        with self.assertRaises(RegistrationError):
            RegistrationProfile.from_json_file(bad_json)

        bad_profile = folder / "bad_profile.json"
        bad_profile.write_text(json.dumps({"schemaVersion": REGISTRATION_SCHEMA_VERSION}), encoding="utf-8")
        with self.assertRaises(RegistrationError):
            RegistrationProfile.from_json_file(bad_profile)

    def test_runtime_registration_profile_is_gitignored(self):
        app_dir = Path(__file__).resolve().parents[1]
        try:
            result = subprocess.run(
                ["git", "check-ignore", "-q", "config/registration_profile.json"],
                cwd=app_dir,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except FileNotFoundError:
            self.skipTest("git is not available")
        if result.returncode == 128:
            self.skipTest("test is not running inside a git worktree")
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))

    def test_existing_identity_mask_resize_path_is_unchanged(self):
        image = np.arange(16, dtype=np.uint8).reshape(4, 4)
        mask = np.array([[True, False], [False, True]], dtype=bool)

        pixels, target_mask = apply_mask_to_image(image, mask, registration_mode="identity")

        self.assertEqual(target_mask.shape, image.shape)
        self.assertGreater(pixels.size, 0)
        calibrated_pixels, calibrated_mask = apply_mask_to_image(image, target_mask, registration_mode="calibrated")
        self.assertEqual(calibrated_mask.shape, image.shape)
        self.assertEqual(calibrated_pixels.size, pixels.size)


if __name__ == "__main__":
    unittest.main()
