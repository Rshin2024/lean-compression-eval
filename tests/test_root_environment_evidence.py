import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from lean_compression.environment import prepare_environment
from lean_compression.io import file_hash


class RootPackageEvidenceTests(unittest.TestCase):
    def test_root_library_source_and_compiled_objects_are_hashed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"
            mathlib = root / ".lake/packages/mathlib"
            ml_lib = mathlib / ".lake/build/lib/lean"
            ml_lib.mkdir(parents=True)
            own_lib = root / ".lake/build/lib/lean/OAI"
            own_lib.mkdir(parents=True)
            (root / "OAI").mkdir()
            (root / "OAI/Example.lean").write_text("def answer := 42\n")
            (own_lib / "Example.olean").write_bytes(b"compiled-owned-module")
            (own_lib / "Example.ir").write_bytes(b"owned-ir")
            for directory in (root, mathlib):
                (directory / "lean-toolchain").write_text("leanprover/lean4:v4.34.1\n")
            (root / "lake-manifest.json").write_text(json.dumps({"packages": [
                {"name": "mathlib", "rev": "pin", "type": "git"}]}))
            lean = Path(temp) / "toolchain/bin/lean"
            lean.parent.mkdir(parents=True)
            lean.write_bytes(b"lean-binary")
            (lean.parent.parent / "lib/lean").mkdir(parents=True)
            def git(args, cwd):
                return "" if "status" in args else "pin"
            with patch("lean_compression.environment.command", side_effect=git), \
                 patch("lean_compression.environment.subprocess.check_output", return_value=str(lean)):
                env = prepare_environment(root, Path(temp) / "environment.json")
            files = {x["path"]: x["sha256"] for x in env["compiled_files"]}
            self.assertEqual(files["__root__/OAI/Example.olean"], file_hash(own_lib / "Example.olean"))
            self.assertIn("__root__/OAI/Example.ir", files)
            self.assertIn(str(own_lib.parent.resolve()), env["lean_search_paths"])
            self.assertIn("__root__/OAI/Example.lean", {x["path"] for x in env["source_files"]})
