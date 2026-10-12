"""Locating the pinned Lean runtime: selectable manifest, explicit skips and REQUIRE_LEAN=1.

No Lean process is started; an existing interpreter stands in for the binary.
"""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from lean_runtime import ROOT, manifest_path, pinned_runtime
import test_lean_integration


class LeanRuntimeLocationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        patcher = patch.dict(os.environ)
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in ("LEAN_COMPRESSION_ENVIRONMENT", "REQUIRE_LEAN"):
            os.environ.pop(name, None)

    def manifest(self, lean_binary):
        path = self.directory / "environment.json"
        path.write_text(json.dumps({"lean_binary": str(lean_binary), "lean_search_paths": []}))
        return path

    def raised(self, call, *args):
        # A SkipTest escaping assertRaises would report this test as skipped: the silent pass under test.
        try:
            call(*args)
        except unittest.SkipTest as skipped:
            self.fail(f"skipped instead of failing: {skipped}")
        except Exception as error:
            return error
        self.fail("no error raised")

    def test_manifest_path_defaults_to_repository_root_and_follows_variable(self):
        self.assertEqual(manifest_path(), ROOT / "configs/environment.local.json")
        os.environ["LEAN_COMPRESSION_ENVIRONMENT"] = "configs/environment.new.json"
        self.assertEqual(manifest_path(), ROOT / "configs/environment.new.json")
        os.environ["LEAN_COMPRESSION_ENVIRONMENT"] = str(self.directory / "other.json")
        self.assertEqual(manifest_path(), self.directory / "other.json")

    def test_missing_manifest_or_binary_skips_with_named_manifest_and_command(self):
        missing = self.directory / "missing.json"
        os.environ["LEAN_COMPRESSION_ENVIRONMENT"] = str(missing)
        with self.assertRaises(unittest.SkipTest) as skipped:
            pinned_runtime()
        self.assertIn(str(missing), str(skipped.exception))
        self.assertIn("prepare-environment", str(skipped.exception))
        # The variable is already set, so the text does not suggest setting it.
        self.assertNotIn("LEAN_COMPRESSION_ENVIRONMENT", str(skipped.exception))
        del os.environ["LEAN_COMPRESSION_ENVIRONMENT"]
        manifest = self.manifest(self.directory / "bin" / "lean")
        with self.assertRaises(unittest.SkipTest) as skipped:
            pinned_runtime(manifest)
        self.assertIn(str(manifest), str(skipped.exception))
        self.assertIn(str(self.directory / "bin" / "lean"), str(skipped.exception))
        self.assertIn("set LEAN_COMPRESSION_ENVIRONMENT=configs/environment.new.json", str(skipped.exception))

    def test_require_lean_turns_unavailable_runtime_into_failure(self):
        os.environ["REQUIRE_LEAN"] = "1"
        error = self.raised(pinned_runtime, self.directory / "missing.json")
        self.assertIsInstance(error, RuntimeError)
        self.assertRegex(str(error), "REQUIRE_LEAN=1.*missing.json.*prepare-environment")
        error = self.raised(pinned_runtime, self.manifest(self.directory / "bin" / "lean"))
        self.assertIsInstance(error, RuntimeError)
        self.assertRegex(str(error), "REQUIRE_LEAN=1")
        os.environ["REQUIRE_LEAN"] = "yes"
        error = self.raised(pinned_runtime, self.directory / "missing.json")
        self.assertIsInstance(error, ValueError)
        self.assertRegex(str(error), "REQUIRE_LEAN must be 0 or 1")

    def test_real_lean_fixtures_fail_under_require_lean_without_a_runtime(self):
        os.environ["REQUIRE_LEAN"] = "1"
        fixtures = test_lean_integration.LeanIntegrationTests
        missing = self.directory / "missing.json"
        with patch.object(test_lean_integration, "MANIFEST", missing):
            error = self.raised(fixtures.setUpClass)
        self.assertIsInstance(error, RuntimeError)
        self.assertIn(str(missing), str(error))
        # Without that override the fixtures read LEAN_COMPRESSION_ENVIRONMENT when they start.
        selected = self.directory / "selected.json"
        os.environ["LEAN_COMPRESSION_ENVIRONMENT"] = str(selected)
        error = self.raised(fixtures.setUpClass)
        self.assertIsInstance(error, RuntimeError)
        self.assertIn(str(selected), str(error))

    def test_available_binary_is_returned_without_skipping(self):
        os.environ["REQUIRE_LEAN"] = "1"
        manifest = self.manifest(sys.executable)
        os.environ["LEAN_COMPRESSION_ENVIRONMENT"] = str(manifest)
        environment, lean = pinned_runtime()
        self.assertEqual(lean, sys.executable)
        self.assertEqual(environment["lean_search_paths"], [])


if __name__ == "__main__":
    unittest.main()
