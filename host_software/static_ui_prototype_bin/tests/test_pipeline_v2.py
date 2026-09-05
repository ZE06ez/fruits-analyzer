import unittest

import numpy as np

from pipeline_v2 import align_pointclouds_by_centroid, align_pointclouds_icp, pointcloud_volume


class PipelineV2Tests(unittest.TestCase):
    def test_centroid_alignment_matches_legacy_icp_alias(self):
        first = np.array(
            [
                [0.0, 0.0, 0.0],
                [2.0, 0.0, 0.0],
                [0.0, 2.0, 0.0],
            ],
            dtype=np.float32,
        )
        second = np.array(
            [
                [10.0, 5.0, 1.0],
                [12.0, 5.0, 1.0],
                [10.0, 7.0, 1.0],
            ],
            dtype=np.float32,
        )
        pointclouds = [first, second]
        expected_second = second + (np.mean(first, axis=0) - np.mean(second, axis=0))
        expected = np.vstack([first, expected_second])

        np.testing.assert_allclose(align_pointclouds_by_centroid(pointclouds), expected)
        np.testing.assert_allclose(align_pointclouds_icp(pointclouds), expected)

    def test_pointcloud_volume_uses_occupied_voxel_count(self):
        points = np.array(
            [
                [0.1, 0.1, 0.1],
                [0.9, 0.9, 0.9],
                [1.2, 0.2, 0.2],
                [2.1, 0.2, 0.2],
            ],
            dtype=np.float32,
        )

        self.assertEqual(pointcloud_volume(points, voxel_size=1.0), 3.0)


if __name__ == "__main__":
    unittest.main()
