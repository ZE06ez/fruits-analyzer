from __future__ import annotations

import unittest

from sample_stage import (
    SAMPLE_STAGE_PROTOCOL_UNKNOWN,
    SampleStageNotImplemented,
    SimulatedSampleStage,
    UnimplementedSampleStage,
)


class SampleStageBoundaryTests(unittest.TestCase):
    def test_unimplemented_stage_reports_protocol_unknown_without_fake_feedback(self):
        stage = UnimplementedSampleStage()

        status = stage.get_status().to_dict()

        self.assertFalse(status["available"])
        self.assertFalse(status["protocolKnown"])
        self.assertFalse(status["positionFeedbackSupported"])
        self.assertEqual(status["lastError"], SAMPLE_STAGE_PROTOCOL_UNKNOWN)
        self.assertEqual(stage.get_sample_stage_position().angle_deg, None)

        with self.assertRaises(SampleStageNotImplemented):
            stage.home()

    def test_simulated_stage_supports_basic_motion_for_software_tests(self):
        stage = SimulatedSampleStage()

        stage.connect()
        home = stage.home()
        moved = stage.move_to(390, direction="CCW")
        relative = stage.move_relative(-30, direction="CW")
        stopped = stage.stop()
        status = stage.get_status().to_dict()

        self.assertTrue(home["homed"])
        self.assertEqual(moved["targetAngleDeg"], 30.0)
        self.assertEqual(relative["targetAngleDeg"], 0.0)
        self.assertTrue(stopped["stopped"])
        self.assertTrue(status["available"])
        self.assertTrue(status["positionFeedbackSupported"])
        self.assertEqual(status["currentAngleDeg"], 0.0)
        self.assertEqual(status["hardwareMode"], "simulation")


if __name__ == "__main__":
    unittest.main()
