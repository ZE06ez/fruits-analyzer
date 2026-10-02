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

    def test_workflow_stepper_is_persistent_in_sidebar(self) -> None:
        sidebar = self.html.split('<aside class="sidebar">', 1)[1].split('<section class="side-card run-card">', 1)[0]
        capture_page = self.html.split('data-page="capture"', 1)[1].split('class="sample-create-panel"', 1)[0]
        self.assertIn('class="capture-workflow-stepper sidebar-workflow-stepper"', sidebar)
        self.assertNotIn('capture-workflow-stepper', capture_page)
        for label in ("样品", "设备", "标定", "RGB", "多光谱", "分析", "结果"):
            self.assertIn(f'capture-step-label">{label}', sidebar)
        self.assertNotIn("workflow-strip", self.html)
        self.assertIn(".capture-workflow-stepper", self.css)
        self.assertIn("white-space: nowrap", self.css)
        self.assertIn("word-break: keep-all", self.css)
        self.assertIn('data-status="completed"', self.css)
        self.assertIn('data-status="current"', self.css)
        self.assertIn('data-status="pending"', self.html)
        self.assertIn('data-status="blocked"', self.css)
        self.assertNotIn('content: "›"', self.css)
        self.assertIn("function setWorkflowStep", self.js)

    def test_analysis_tabs_merge_preprocessing_and_roi_features(self) -> None:
        self.assertEqual(self.html.count("预处理、ROI 与特征</button>"), 4)
        self.assertNotIn(">数据预处理</button>", self.html)
        self.assertNotIn('data-analysis-step="filter">ROI 与特征</button>', self.html)
        self.assertNotIn('data-analysis-step="filter"', self.html)


if __name__ == "__main__":
    unittest.main()
