"""Regression checks for first-run smoke-test acceptance conditions."""

import importlib.util
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location(
    "verify_running", Path(__file__).with_name("verify-running.py")
)
VERIFY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFY)


class ProvisionAcceptanceTests(unittest.TestCase):
    def test_requires_persistent_absolute_binary_setting(self):
        with tempfile.TemporaryDirectory(prefix="package data ") as directory:
            data = Path(directory)
            settings = {
                "options": {"core.rclone_binary": {"value": str(data / "rclone")}}
            }
            self.assertEqual(VERIFY.installed_binary(settings, data), data / "rclone")
            for value in (None, "", "system", "rclone", True, str(data / "bin/rclone")):
                with self.subTest(value=value), self.assertRaises(AssertionError):
                    VERIFY.installed_binary(
                        {"options": {"core.rclone_binary": {"value": value}}}, data
                    )

    def test_engine_pid_rejects_non_process_values(self):
        for pid in (True, False, 0, 1, -2, "123", None):
            with self.subTest(pid=pid), self.assertRaises(AssertionError):
                VERIFY.engine_pid(
                    {
                        "status": "active",
                        "rcloneInfo": {"version": "v1.75.0"},
                        "pid": pid,
                    }
                )


if __name__ == "__main__":
    unittest.main()
