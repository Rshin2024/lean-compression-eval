"""Environment records: full hashes cover the IR Lean loads; edited or stale records are refused at load."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from lean_compression.cli import main
from lean_compression.environment import load_environment, prepare_environment, verify_environment
from lean_compression.io import digest, read_json, write_json

PROJECT = Path(__file__).resolve().parents[1]
TOOLCHAIN = "leanprover/lean4:v4.34.0-rc1"


def rehash(env: dict) -> dict:
    unhashed = {k: v for k, v in env.items() if k != "environment_hash"}
    return {**unhashed, "environment_hash": digest(unhashed)}


@unittest.skipUnless(shutil.which("git"), "git is required for the locked-package fixture")
class EnvironmentIntegrityTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.core = self.root / "toolchain/lib/lean"
        self.core.mkdir(parents=True)
        (self.root / "toolchain/bin").mkdir()
        self.lean = self.root / "toolchain/bin/lean"
        self.lean.write_bytes(b"launcher")
        # Both runtime library suffixes, so the native re-check is tested whatever the host platform.
        for name in ("Init.olean", "Init.ir", "Init.ilean", "libleanshared.dylib", "libleanshared.so", "libInit.a"):
            (self.core / name).write_bytes(name.encode())
        self.package = self.root / "project"
        mathlib = self.package / ".lake/packages/mathlib"
        self.lib = mathlib / ".lake/build/lib/lean"
        (self.lib / "Mathlib").mkdir(parents=True)
        (mathlib / "Mathlib.lean").write_text("import Mathlib.Basic\n")
        (mathlib / "lean-toolchain").write_text(TOOLCHAIN + "\n")
        (mathlib / ".gitignore").write_text(".lake\n")
        self.git = lambda *args: subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false",
             "-c", "core.hooksPath=/dev/null", *args], cwd=mathlib, check=True, capture_output=True, text=True).stdout.strip()
        self.git("init", "-q")
        self.git("add", "-A")
        self.git("commit", "-qm", "pin")
        (self.package / "lean-toolchain").write_text(TOOLCHAIN + "\n")
        (self.package / "lake-manifest.json").write_text(json.dumps(
            {"packages": [{"name": "mathlib", "type": "git", "rev": self.git("rev-parse", "HEAD")}]}))
        for name in ("Basic.olean", "Basic.olean.private", "Basic.ir", "Basic.ir.sig", "Basic.ilean",
                     "Basic.trace", "Basic.olean.hash"):
            (self.lib / "Mathlib" / name).write_bytes(name.encode())

    def prepare(self, name="env.json", full_hash=True):
        real = subprocess.check_output
        elan = lambda args, **options: f"{self.lean}\n" if args[:2] == ["elan", "which"] else real(args, **options)
        with patch("lean_compression.environment.subprocess.check_output", side_effect=elan):
            return prepare_environment(self.package, self.root / name, full_hash=full_hash)

    def test_full_hash_covers_loaded_ir_and_skips_unread_artifacts(self):
        env = self.prepare()
        self.assertEqual(env["schema_version"], 2)
        self.assertEqual({item["path"] for item in env["compiled_files"]},
                         {"mathlib/Mathlib/Basic.olean", "mathlib/Mathlib/Basic.olean.private", "mathlib/Mathlib/Basic.ir",
                          "mathlib/Mathlib/Basic.ir.sig", "toolchain/Init.olean", "toolchain/Init.ir",
                          "toolchain/libleanshared.dylib", "toolchain/libleanshared.so"})
        self.assertEqual(env["compiled_excluded_suffixes"], [".trace", ".hash", ".ilean", ".a"])
        self.assertIsNone(self.prepare("metadata-only.json", full_hash=False)["compiled_excluded_suffixes"])
        for name in ("Basic.ir", "Basic.ir.sig"):
            original = (self.lib / "Mathlib" / name).read_bytes()
            (self.lib / "Mathlib" / name).write_bytes(b"different tactic code")
            changed = self.prepare(f"{name}.json")
            self.assertNotEqual(changed["compiled_environment_hash"], env["compiled_environment_hash"], name)
            self.assertNotEqual(changed["environment_hash"], env["environment_hash"], name)
            (self.lib / "Mathlib" / name).write_bytes(original)
        (self.lib / "Mathlib/Basic.trace").write_bytes(b"rebuilt")
        (self.core / "Init.ilean").write_bytes(b"reindexed")
        self.assertEqual(self.prepare("metadata.json")["compiled_environment_hash"], env["compiled_environment_hash"])
        os.symlink(self.core, self.lib / "Linked")
        with self.assertRaisesRegex(ValueError, "Linked directory"):
            self.prepare("linked.json")

    def test_edited_records_are_rejected_and_valid_records_load(self):
        env = self.prepare()
        self.assertEqual(load_environment(self.root / "env.json"), env)
        for key, value in (("lean_search_paths", [str(self.root)]), ("runtime_backend", "docker"),
                           ("docker_image", "sha256:" + "b" * 64)):
            write_json(self.root / f"{key}.json", {**env, key: value})
            with self.assertRaisesRegex(ValueError, "does not match its environment_hash"):
                load_environment(self.root / f"{key}.json")
        with self.assertRaisesRegex(ValueError, "schema_version"):
            verify_environment(rehash({**env, "schema_version": 3}))
        with self.assertRaisesRegex(ValueError, "lacks lean_binary_hash"):
            verify_environment(rehash({k: v for k, v in env.items() if k != "lean_binary_hash"}))
        old = rehash({**env, "schema_version": 1})
        write_json(self.root / "old.json", old)
        with redirect_stderr(io.StringIO()) as err:
            self.assertEqual(load_environment(self.root / "old.json"), old)
        self.assertIn("omits Lean IR", err.getvalue())
        # The note is for host full records only; Docker and metadata-only schema 1 records load silently.
        for name, record in (("old-docker.json", rehash({**old, "runtime_backend": "docker"})),
                             ("old-metadata.json", rehash({**old, "hash_mode": "development_metadata_only"}))):
            write_json(self.root / name, record)
            with redirect_stderr(io.StringIO()) as err:
                self.assertEqual(load_environment(self.root / name), record)
            self.assertEqual(err.getvalue(), "", name)
        (self.root / "list.json").write_text(json.dumps([env]))
        with self.assertRaisesRegex(ValueError, f"record {re.escape(str(self.root / 'list.json'))} must be a JSON object"):
            load_environment(self.root / "list.json")
        with self.assertRaisesRegex(ValueError, "Environment record must be a JSON object, not list"):
            verify_environment([env])

    def test_native_runtime_drift_is_rejected(self):
        self.prepare()
        path = self.root / "env.json"
        for file in (self.lean, self.core / "libleanshared.dylib", self.core / "libleanshared.so"):
            original = file.read_bytes()
            file.write_bytes(b"patched")
            with self.assertRaisesRegex(ValueError, "Lean runtime file differs"):
                load_environment(path)
            file.write_bytes(original)
        self.lib.rename(self.lib.with_name("moved"))
        with self.assertRaisesRegex(ValueError, "library directory .* missing"):
            load_environment(path)
        self.lib.with_name("moved").rename(self.lib)
        load_environment(path)
        self.git("commit", "--allow-empty", "-qm", "update")
        with self.assertRaisesRegex(ValueError, "mathlib no longer matches its recorded revision"):
            load_environment(path)

    def test_unreadable_package_revision_is_not_a_match(self):
        env = self.prepare()
        # No git on PATH, or a package directory that is gone: the revision cannot be read, so the record fails.
        with patch.dict(os.environ, {"PATH": str(self.root)}), self.assertRaisesRegex(
                ValueError, f"Cannot read the git revision of package mathlib at {re.escape(env['packages'][0]['path'])}$"):
            load_environment(self.root / "env.json")
        absent = rehash({**env, "packages": [{**env["packages"][0], "path": str(self.root / "absent")}]})
        with self.assertRaisesRegex(ValueError, f"Cannot read the git revision of package mathlib at "
                                                f"{re.escape(str(self.root / 'absent'))}$"):
            verify_environment(absent)

    def test_docker_record_checks_only_its_digest(self):
        env = self.prepare()
        image = rehash({**env, "runtime_backend": "docker", "docker_image": "sha256:" + "a" * 64,
                        "lean_binary": "/opt/elan/bin/lean", "lean_search_paths": ["/opt/eval/package/lib"]})
        self.assertEqual(verify_environment(image), image)
        with self.assertRaisesRegex(ValueError, "environment_hash"):
            verify_environment({**image, "docker_image": "sha256:" + "b" * 64})

    def test_cli_verifies_once_and_refuses_stale_records_before_writing_evidence(self):
        env = self.prepare()
        write_json(self.root / "edited.json", {**env, "lean_search_paths": [str(self.root)]})
        write_json(self.root / "stale.json", rehash({**env, "lean_binary_hash": "0" * 64}))
        tasks, config = PROJECT / "data/public/smoke.jsonl", PROJECT / "configs/development.json"
        check = ["--tasks", tasks, "--config", config, "--engine", PROJECT / "lean/Evaluator.lean", "--limit", "0"]
        commands = {
            "freeze": ["freeze", "--development", "--tasks", tasks, "--environment", self.root / "edited.json",
                       "--config", config, "--split", "pilot", "--model", "reference_fixture", "--run", self.root / "run"],
            "verify-references": ["verify-references", *check, "--references", PROJECT / "data/private/smoke_references.jsonl",
                                  "--environment", self.root / "stale.json", "--output", self.root / "references"],
            "baseline": ["baseline", *check, "--environment", self.root / "stale.json", "--output", self.root / "baseline"],
            "index-closures": ["index-closures", "--references", PROJECT / "examples/demo-reference-check",
                               "--environment", self.root / "edited.json", "--config", config, "--output", self.root / "index.json"],
        }
        for name, argv in commands.items():
            with self.subTest(name), redirect_stderr(io.StringIO()) as err, self.assertRaises(SystemExit) as raised:
                main([str(arg) for arg in argv])
            self.assertEqual(raised.exception.code, 2)
            self.assertRegex(err.getvalue(), "does not match its environment_hash|Lean runtime file differs")
        for output in ("run", "references", "baseline", "index.json"):
            self.assertFalse((self.root / output).exists(), output)
        with patch("lean_compression.environment.verify_environment", wraps=verify_environment) as verify, \
                redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
            main([str(arg) for arg in ["verify-references", *check, "--environment", self.root / "env.json",
                                       "--references", PROJECT / "data/private/smoke_references.jsonl",
                                       "--output", self.root / "references"]])
        self.assertEqual(verify.call_count, 1)
        self.assertEqual(read_json(self.root / "references/policy.json")["environment_hash"], env["environment_hash"])


class CommittedEnvironmentRecordTests(unittest.TestCase):
    def test_committed_records_are_self_consistent(self):
        for env in (read_json(PROJECT / "configs/environment.local.json"),
                    read_json(PROJECT / "configs/environment.hashed.json"),
                    read_json(PROJECT / "examples/pipeline-demo/manifest.json")["environment"]):
            self.assertEqual(rehash(env), env)

    def test_committed_records_load_against_the_local_runtime(self):
        local = read_json(PROJECT / "configs/environment.local.json")
        if not Path(local["lean_binary"]).exists():
            raise unittest.SkipTest("Pinned local Lean binary is unavailable")
        with redirect_stderr(io.StringIO()) as err:
            loaded = [load_environment(PROJECT / "configs" / name)
                      for name in ("environment.local.json", "environment.hashed.json")]
        legacy = sum(env["schema_version"] == 1 and env["hash_mode"] == "full" for env in loaded)
        self.assertEqual(err.getvalue().count("omits Lean IR"), legacy)


if __name__ == "__main__":
    unittest.main()
