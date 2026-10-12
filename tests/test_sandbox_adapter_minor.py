"""UTF-8 sandbox-profile path literals and line-ending-neutral single-fence adaptation."""
import json
from pathlib import Path
import platform
import subprocess
import tempfile
import unicodedata
import unittest
from unittest.mock import patch

from lean_compression.prompts import adapt_response
from lean_compression.runner import execute_engine, sandbox_profile

SANDBOX_EXEC = Path("/usr/bin/sandbox-exec")
# Raw response -> adapted proof (None: returned unchanged). Pinned from the adapter before CRLF support.
LF_FIXTURES = {
    "```lean\nby exact h\n```": "by exact h",
    "```\nby\n  exact ⟨hp, hq⟩\n```\n": "by\n  exact ⟨hp, hq⟩",
    "  ```lean4\n\n  by exact h\n```  \n\n": "  by exact h",
    "```lean\nby exact h\n\n```": "by exact h\n",
    "```lean\n\n```": "",
    "```lean\nby exact h\n```\n```lean\nrfl\n```": None,
    "Proof:\n```lean\nby exact h\n```": None,
    "```leanx\nby exact h\n```": None,
    "```lean by exact h\n```": None,
    "```lean\nby exact h ```": None,
    "by exact h": None,
}
CRLF_FIXTURES = {
    "```lean\r\nby\r\n  exact ⟨hp, hq⟩\r\n```": "by\r\n  exact ⟨hp, hq⟩",
    "```lean4\r\n⟨hp, hq⟩\r\n```\r\n": "⟨hp, hq⟩",
    "```lean\nby\n  exact ⟨hp, hq⟩\r\n```\n": "by\n  exact ⟨hp, hq⟩",
    "```lean\r\nby exact h\n```": "by exact h",
    "```\r\nby\n  exact ⟨hp, hq⟩\r\n```": "by\n  exact ⟨hp, hq⟩",
    "```lean\r\n\r\n```": "",
    # Only the closing fence's line ending is wrapper; a stray CR in the body stays a parse failure.
    "```lean\nby exact h\r\r\n```": "by exact h\r",
    "```lean\n\rby exact h\r\n```": "\rby exact h",
    "```lean\r\nby exact h\r\n```\r\n```lean\r\nrfl\r\n```": None,
    "```lean\rby exact h\r```": None,
}


class SingleFenceLineEndingTests(unittest.TestCase):
    def check(self, fixtures):
        for raw, expected in fixtures.items():
            with self.subTest(raw=raw):
                self.assertEqual(adapt_response(raw, "single_fence_v1"), raw if expected is None else expected)
                self.assertEqual(adapt_response(raw), raw)

    def test_lf_fixtures_keep_their_previous_adaptation(self):
        self.check(LF_FIXTURES)

    def test_crlf_and_mixed_fences_leave_no_isolated_carriage_return(self):
        self.check(CRLF_FIXTURES)

    def test_crlf_conversion_commutes_with_the_adapter(self):
        for raw in LF_FIXTURES:
            with self.subTest(raw=raw):
                self.assertEqual(adapt_response(raw.replace("\n", "\r\n"), "single_fence_v1"),
                                 adapt_response(raw, "single_fence_v1").replace("\n", "\r\n"))


class SandboxProfileQuotingTests(unittest.TestCase):
    def test_paths_are_utf8_literals_with_quote_and_backslash_escaped(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            profile = sandbox_profile([str(root / "Évaluation-ü"), str(root / 'q"b\\s')], str(root / "scratch-é"))
            self.assertIn(f'(allow file-read-data (subpath "{root}/Évaluation-ü"))', profile)
            self.assertIn(f'(allow file-write* (subpath "{root}/scratch-é"))', profile)
            self.assertIn(f'(subpath "{root}/q\\"b\\\\s")', profile)
            self.assertNotIn("\\u", profile)
            # ASCII paths keep their earlier encoding, so existing profiles are byte-identical.
            ascii_path = "/usr/lib/lean-compression-ascii"
            self.assertIn(f"(subpath {json.dumps(ascii_path)})", sandbox_profile([ascii_path], str(root / "scratch")))

    def test_control_and_undecodable_characters_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            for name in ("tab\tdir", "line\ndir", "back\bspace", "form\ffeed", "del\x7f", "next\x85line", "byte\udcff"):
                with self.subTest(name=name), self.assertRaisesRegex(ValueError, "control or non-UTF-8"):
                    sandbox_profile([str(root / name)], str(root / "scratch"))
            with self.assertRaisesRegex(ValueError, "control or non-UTF-8"):
                sandbox_profile([], str(root / "scratch\r"))


@unittest.skipUnless(platform.system() == "Darwin" and SANDBOX_EXEC.exists(), "requires macOS sandbox-exec")
class MacSandboxNonAsciiPathTests(unittest.TestCase):
    def sandboxed(self, profile_text, scratch, *command):
        profile = scratch / "sandbox.sb"
        profile.write_text(profile_text, encoding="utf-8")
        return subprocess.run([str(SANDBOX_EXEC), "-f", str(profile), *map(str, command)],
                              capture_output=True, text=True, timeout=60)

    def test_generated_profile_allows_non_ascii_and_escaped_paths(self):
        names = ["Évaluation-ü", unicodedata.normalize("NFD", "Évaluation-nfd-ü"), 'quote"back\\slash']
        with tempfile.TemporaryDirectory(prefix="lean-compression-") as directory:
            for name in names:
                with self.subTest(name=name):
                    root = Path(directory).resolve() / name
                    engine, scratch = root / "lean", root / "scratch-é"
                    engine.mkdir(parents=True)
                    scratch.mkdir()
                    target = engine / "Evaluator.lean"
                    target.write_text("readable\n", encoding="utf-8")
                    if self.sandboxed(sandbox_profile([], str(scratch)), scratch, "/bin/cat", target).returncode == 0:
                        self.skipTest("temporary directory is outside the profile's protected roots")
                    profile = sandbox_profile([str(engine)], str(scratch))
                    read = self.sandboxed(profile, scratch, "/bin/cat", target)
                    self.assertEqual((read.returncode, read.stdout), (0, "readable\n"), read.stderr)
                    written = self.sandboxed(profile, scratch, "/usr/bin/touch", scratch / "written")
                    self.assertEqual(written.returncode, 0, written.stderr)
                    outside = self.sandboxed(profile, scratch, "/usr/bin/touch", root / "outside")
                    self.assertNotEqual(outside.returncode, 0)

    def test_execute_engine_runs_from_non_ascii_engine_toolchain_and_tmpdir(self):
        with tempfile.TemporaryDirectory(prefix="lean-compression-") as directory:
            root = Path(directory).resolve() / "Évaluation-ü"
            toolchain, engine, tmp = root / "toolchain", root / "lean" / "Evaluator.lean", root / "tmp-é"
            (toolchain / "bin").mkdir(parents=True)
            engine.parent.mkdir()
            tmp.mkdir()
            engine.write_text("-- engine\n", encoding="utf-8")
            lean = toolchain / "bin" / "lean"
            # Stand-in for `lean --run <engine> <request> <result>`: read both inputs, write a result.
            lean.write_text("#!/bin/sh\n/bin/cat \"$2\" \"$3\" > /dev/null && "
                            "echo '{\"validity_status\": \"success\"}' > \"$4\"\n", encoding="utf-8")
            lean.chmod(0o755)
            environment = {"lean_binary": str(lean), "toolchain_root": str(toolchain),
                           "lean_search_paths": [str(root / "lib")]}
            with patch.object(tempfile, "tempdir", str(tmp)):
                result = execute_engine({"proof": "rfl"}, environment, engine, {"isolation": "macos_sandbox"},
                                        mode="validate", wall_seconds=30)
            self.assertEqual((result.get("process_returncode"), result["validity_status"]), (0, "success"), result)


if __name__ == "__main__":
    unittest.main()
