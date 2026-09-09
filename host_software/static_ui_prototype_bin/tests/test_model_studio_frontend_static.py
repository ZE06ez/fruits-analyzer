from __future__ import annotations

import unittest
from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[1]


class ModelStudioFrontendStaticTests(unittest.TestCase):
    def test_single_delete_confirmation_uses_readonly_id_and_trimmed_input_value(self):
        js = (APP_DIR / "model_studio" / "static" / "model_studio.js").read_text(encoding="utf-8")

        self.assertIn("readonly-confirm-value", js)
        self.assertIn("confirmValueLabel = \"Model ID\"", js)
        self.assertIn("input.value.trim() !== confirmValue", js)
        self.assertNotIn("placeholder=\"${escapeHtml(confirmValue)}\"", js)

    def test_model_bulk_selection_and_delete_controls_exist(self):
        html = (APP_DIR / "model_studio" / "static" / "index.html").read_text(encoding="utf-8")
        js = (APP_DIR / "model_studio" / "static" / "model_studio.js").read_text(encoding="utf-8")

        self.assertIn("modelBulkBar", html)
        self.assertIn("selectAllModels", html)
        self.assertIn("deleteSelectedModels", html)
        self.assertIn("selectedModelIds: new Set()", js)
        self.assertIn("data-model-select", js)
        self.assertIn("/api/model-studio/models/delete-batch", js)

    def test_candidate_and_validated_default_actions_are_disabled_with_publish_hint(self):
        js = (APP_DIR / "model_studio" / "static" / "model_studio.js").read_text(encoding="utf-8")

        self.assertGreaterEqual(js.count("请先发布模型。"), 3)
        self.assertIn("当前默认", js)
