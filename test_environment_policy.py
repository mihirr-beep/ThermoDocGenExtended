import unittest
from pathlib import Path

from environment_policy import (
    datasheet_generation_enabled,
    resolve_application_environment,
)


class EnvironmentPolicyTests(unittest.TestCase):
    VALID_ENVIRONMENTS = {"default", "development", "production", "testing"}

    def test_defaults_to_production(self):
        self.assertEqual(
            resolve_application_environment(
                environ={},
                default="production",
                valid_names=self.VALID_ENVIRONMENTS,
            ),
            "production",
        )

    def test_environment_variable_overrides_explicit_config(self):
        self.assertEqual(
            resolve_application_environment(
                "testing",
                environ={"APP_ENV": "production"},
                valid_names=self.VALID_ENVIRONMENTS,
            ),
            "production",
        )

    def test_explicit_non_production_config_is_preserved(self):
        self.assertEqual(
            resolve_application_environment(
                "testing",
                environ={},
                valid_names=self.VALID_ENVIRONMENTS,
            ),
            "testing",
        )

    def test_invalid_environment_uses_safe_default(self):
        self.assertEqual(
            resolve_application_environment(
                environ={"APP_ENV": "unknown"},
                default="production",
                valid_names=self.VALID_ENVIRONMENTS,
            ),
            "production",
        )

    def test_datasheet_generation_is_enabled_in_every_environment(self):
        self.assertTrue(datasheet_generation_enabled("production"))
        self.assertTrue(datasheet_generation_enabled(" Production "))
        self.assertTrue(datasheet_generation_enabled("testing"))
        self.assertTrue(datasheet_generation_enabled("development"))


class AssignedTestActionTemplateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = Path("templates/assigned_test.html").read_text(
            encoding="utf-8"
        )
        cls.app_source = Path("app.py").read_text(encoding="utf-8")

    def test_assigned_page_has_generation_actions(self):
        self.assertIn("/datasheet/ce/{{ assignment.id }}/form", self.source)
        self.assertIn(
            "/datasheet/g/{{ _tn }}/{{ assignment.id }}/form",
            self.source,
        )
        self.assertIn("Generate Datasheet", self.source)

    def test_assigned_page_has_no_manual_upload_flow(self):
        self.assertNotIn("Upload Datasheet", self.source)
        self.assertNotIn("uploadDataSheetModal", self.source)
        self.assertNotIn("/upload-test-datasheet", self.source)
        self.assertNotIn(
            "@flask_app.route('/upload-test-datasheet'",
            self.app_source,
        )

    def test_unsupported_tests_do_not_fall_back_to_upload(self):
        self.assertIn("Datasheet generation unavailable", self.source)


if __name__ == "__main__":
    unittest.main()
