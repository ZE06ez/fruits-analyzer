import unittest

from rotation_plan import build_capture_rotation_plan, mark_plan_completed


class CaptureRotationPlanTests(unittest.TestCase):
    def test_common_intervals_generate_expected_views_without_360(self):
        cases = [
            (30, 12, 30),
            (45, 8, 45),
            (60, 6, 60),
            (90, 4, 90),
        ]
        for interval, expected_count, actual_interval in cases:
            with self.subTest(interval=interval):
                plan = build_capture_rotation_plan({"enabled": True, "expectedIntervalDeg": interval})
                self.assertEqual(plan["view_count"], expected_count)
                self.assertEqual(plan["actual_interval_deg"], actual_interval)
                self.assertNotIn(360, plan["angles_deg"])

    def test_non_divisible_interval_is_evenly_redistributed(self):
        plan = build_capture_rotation_plan({"enabled": True, "expectedIntervalDeg": 50})
        self.assertEqual(plan["view_count"], 8)
        self.assertEqual(plan["actual_interval_deg"], 45)
        self.assertEqual(plan["angles_deg"], [0, 45, 90, 135, 180, 225, 270, 315])

    def test_interval_at_or_above_full_turn_uses_one_view(self):
        full = build_capture_rotation_plan({"enabled": True, "expectedIntervalDeg": 360})
        over = build_capture_rotation_plan({"enabled": True, "expectedIntervalDeg": 720})
        self.assertEqual(full["view_count"], 1)
        self.assertEqual(full["angles_deg"], [0])
        self.assertEqual(over["view_count"], 1)
        self.assertEqual(over["angles_deg"], [0])

    def test_invalid_enabled_interval_is_rejected(self):
        for interval in (0, -15):
            with self.subTest(interval=interval):
                with self.assertRaises(ValueError):
                    build_capture_rotation_plan({"enabled": True, "expectedIntervalDeg": interval})

    def test_start_angle_is_normalized_and_direction_is_metadata(self):
        plan = build_capture_rotation_plan({
            "enabled": True,
            "expectedIntervalDeg": 90,
            "startAngleDeg": 370,
            "direction": "CCW",
        })
        self.assertEqual(plan["start_angle_deg"], 10)
        self.assertEqual(plan["angles_deg"], [10, 100, 190, 280])
        self.assertEqual(plan["direction"], "CCW")
        self.assertTrue(all(view["direction"] == "CCW" for view in plan["views"]))
        self.assertTrue(all(view["logical_angle_deg"] >= 0 for view in plan["views"]))

    def test_closure_view_is_explicit_and_marked(self):
        without_closure = build_capture_rotation_plan({
            "enabled": True,
            "expectedIntervalDeg": 90,
            "includeClosureView": False,
        })
        with_closure = build_capture_rotation_plan({
            "enabled": True,
            "expectedIntervalDeg": 90,
            "includeClosureView": True,
        })
        self.assertEqual(without_closure["total_capture_views"], 4)
        self.assertFalse(any(view["closure_view"] for view in without_closure["views"]))
        self.assertEqual(with_closure["total_capture_views"], 5)
        self.assertEqual(with_closure["angles_deg"][-1], 360)
        self.assertTrue(with_closure["views"][-1]["closure_view"])

    def test_home_completion_does_not_add_a_capture_view(self):
        plan = build_capture_rotation_plan({"enabled": True, "expectedIntervalDeg": 60})
        completed = mark_plan_completed(plan)
        self.assertEqual(completed["total_capture_views"], 6)
        self.assertEqual(len(completed["completed_views"]), 6)
        self.assertTrue(completed["returned_home"])
        self.assertEqual(completed["home_status"], "HOME_OK")

    def test_disabled_mode_keeps_single_view_plan(self):
        plan = build_capture_rotation_plan({"enabled": False, "expectedIntervalDeg": 0})
        self.assertFalse(plan["enabled"])
        self.assertEqual(plan["view_count"], 1)
        self.assertEqual(plan["angles_deg"], [0])
        self.assertFalse(plan["include_closure_view"])

    def test_sample_stage_and_filter_wheel_domains_are_separate(self):
        plan = build_capture_rotation_plan({"enabled": True, "expectedIntervalDeg": 45})
        self.assertEqual(plan["rotation_domain"], "sample_rotation")
        self.assertTrue(plan["filter_wheel_rotation_independent"])
        self.assertEqual(plan["views"][0]["sample_rotation_control"], "sample_stage")
        self.assertEqual(plan["views"][0]["filter_wheel_control"], "independent")


if __name__ == "__main__":
    unittest.main()
