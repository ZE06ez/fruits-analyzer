from __future__ import annotations

import unittest
from pathlib import Path


class DeviceMaintenanceUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        root = Path(__file__).parents[1]
        cls.html = (root / "index.html").read_text(encoding="utf-8")
        cls.js = (root / "app.js").read_text(encoding="utf-8")
        cls.css = (root / "styles.css").read_text(encoding="utf-8")

    def test_device_maintenance_is_the_single_navigation_entry(self) -> None:
        self.assertIn("设备与维护</button>", self.html)
        self.assertNotIn("设备总览</button>", self.html)
        self.assertNotIn("工程调试</button>", self.html)
        self.assertIn("系统设备状态", self.html)
        self.assertIn("核心设备", self.html)
        self.assertIn("光学系统", self.html)
        self.assertIn("运动系统", self.html)

    def test_summary_cards_and_progressive_inspectors_exist(self) -> None:
        for text in ("STM32 Controller", "RGB Camera", "DVP2 Multispectral Camera", "Filter Wheel", "SampleStage"):
            self.assertIn(text, self.html)
        for inspector in ("controllerInspector", "wheelInspector", "tungstenInspector", "actuatorInspector", "sampleStageInspector"):
            self.assertIn(inspector, self.html)
        self.assertIn("Advanced · raw diagnostics", self.html)

    def test_diagnostics_remain_clickable_and_hardware_gating_is_preserved(self) -> None:
        self.assertIn('id="startDeviceCheck"', self.html)
        self.assertIn("data-device-diagnostic", self.html)
        self.assertIn("function runDeviceDiagnostic", self.js)
        self.assertIn("function setActionAvailability", self.js)
        self.assertIn("function guardBlockedAction", self.js)
        self.assertIn("showActionBlockedDetails", self.js)
        self.assertIn('button.disabled = next.state === "running"', self.js)
        self.assertIn('data-action-state="blocked"', self.css)


if __name__ == "__main__":
    unittest.main()
