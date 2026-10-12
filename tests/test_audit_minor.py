import hashlib
import hmac
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest

from lean_compression.audit import _opaque_id, write_audit_bundle
from lean_compression.io import digest, read_jsonl
from lean_compression.prompts import messages

try:
    from lean_runtime import ROOT, pinned_runtime
except ModuleNotFoundError:  # python3 -m unittest tests.test_audit_minor, run from the repository root
    from tests.lean_runtime import ROOT, pinned_runtime

CONDITIONS = ("standard", "mathematical_compression", "source_concision")


def task(task_id, **fields):
    return {"task_id": task_id, "family_id": "family-" + task_id, "domain": "sets_logic", "origin": "fresh",
            "target_name": "evaluation_target_" + task_id, "binders": "(p : Prop)", "proposition": "p → p",
            "context": "def helper : Nat := 1", "premise_context": "", "imports": ["Init"], **fields}


def fixture(tasks=None, samples=2):
    tasks = tasks or [task(f"t{i}") for i in range(3)]
    rows = []
    for item in tasks:
        for condition in CONDITIONS:
            for sample in range(samples):
                identity = {"task_id": item["task_id"], "condition": condition, "sample_index": sample}
                rows.append({"attempt_id": digest(identity), "task_id": item["task_id"], "condition": condition,
                             "sample_index": sample, "model_snapshot": "secret-model", "validity_status": "success",
                             "metric_status": "available", "metrics": {"W_S": 1, "W_B": 2},
                             "generation": {"raw_response": "fun h => h"}})
    manifest = {"tasks": tasks, "config": {"analysis": {"audit_seed": 20261012}}, "run_hash": "a" * 64,
                "slots": [{key: row[key] for key in ("attempt_id", "task_id", "condition", "sample_index",
                                                     "model_snapshot")} for row in rows]}
    paired = [{"task_id": item["task_id"], "contrast": "compression_vs_standard", "mean_log2_T0_difference": 1.0}
              for item in tasks]
    return manifest, rows, {"models": {"secret-model": {"paired_tasks": paired}}}


def codes(root):
    review = Path(root) / "review"
    return {path.name for path in review.glob("task-*")} | {path.name for path in review.glob("task-*/proof-*")}


def files(root):
    # Directories map to None, so a refused call that only creates directories still changes the snapshot.
    return {str(path.relative_to(root)): path.read_bytes() if path.is_file() else None for path in Path(root).rglob("*")}


def key_of(root):
    return json.loads((Path(root) / "private/code_key.json").read_text())["key"]


def with_key_of(source, destination):
    (destination / "private").mkdir(parents=True)
    shutil.copy(source / "private/code_key.json", destination / "private/code_key.json")
    return destination


class AuditCodeKeyTests(unittest.TestCase):
    def test_codes_cannot_be_recomputed_from_public_run_material(self):
        manifest, rows, result = fixture()
        run_hash, seed = manifest["run_hash"], 20261012
        public = set()
        for slot in manifest["slots"]:
            for kind, identity in (("proof", slot["attempt_id"]), ("task", slot["task_id"])):
                public.add(kind + "-" + hashlib.sha256(f"{run_hash}|{seed}|{kind}|{identity}".encode()).hexdigest()[:16])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_audit_bundle(manifest, rows, result, root)
            self.assertEqual(len(codes(root)), 3 + 18)
            self.assertFalse(codes(root) & public)
            # The private key reproduces every published code; ordering follows those keyed codes.
            key = bytes.fromhex(json.loads((root / "private/code_key.json").read_text())["key"])
            self.assertEqual(len(key), 32)
            keyed = lambda kind, identity: kind + "-" + hmac.new(
                key, f"{run_hash}|{seed}|{kind}|{identity}".encode(), hashlib.sha256).hexdigest()[:16]
            labels = json.loads((root / "private/labels.json").read_text())
            self.assertEqual(len(labels), 18)
            for code, label in labels.items():
                self.assertEqual(code, keyed("proof", label["attempt_id"]))
                self.assertEqual(label["task_code"], keyed("task", label["task_id"]))
            for task_dir in (root / "review").glob("task-*"):
                order = [item["proof_code"] for item in json.loads((task_dir / "proofs.json").read_text())]
                self.assertEqual(order, sorted(order))
            audit = json.loads((root / "audit.json").read_text())
            self.assertEqual(audit["audit_version"], "frozen_blinded_v2")
            self.assertEqual(audit["code_key"], "private/code_key.json")
            self.assertIn("review/ only", audit["reviewer_scope"])
            # Only the owner can list private/ or read the key.
            self.assertEqual(stat.S_IMODE((root / "private").stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE((root / "private/code_key.json").stat().st_mode), 0o600)
            reviewer_text = "".join(path.read_text() for path in (root / "review").rglob("*") if path.is_file())
            for secret in [key.hex(), run_hash, "secret-model", *CONDITIONS[1:], *(row["attempt_id"] for row in rows)]:
                self.assertNotIn(secret, reviewer_text)
            self.assertNotIn(key.hex(), (root / "audit.json").read_text())

    def test_same_directory_reuses_key_and_new_directories_get_new_codes(self):
        manifest, rows, result = fixture()
        with tempfile.TemporaryDirectory() as scratch:
            first, second = Path(scratch) / "first", Path(scratch) / "second"
            summary = write_audit_bundle(manifest, rows, result, first)
            snapshot = files(first)
            self.assertEqual(write_audit_bundle(manifest, rows, result, first), summary)
            self.assertEqual(files(first), snapshot)
            write_audit_bundle(manifest, rows, result, second)
            self.assertFalse(codes(first) & codes(second))
            # analyze always names the bundle audit/, so the key must not follow from the directory name.
            one, two = Path(scratch) / "one/audit", Path(scratch) / "two/audit"
            write_audit_bundle(manifest, rows, result, one)
            write_audit_bundle(manifest, rows, result, two)
            self.assertNotEqual(key_of(one), key_of(two))
            self.assertFalse(codes(one) & codes(two))
            # Nor from the path: a fresh bundle at the same path gets a new key.
            replaced = key_of(one)
            shutil.rmtree(one)
            write_audit_bundle(manifest, rows, result, one)
            self.assertNotEqual(key_of(one), replaced)
            # The key alone fixes the codes: a copied key reproduces the bundle byte for byte.
            copy = with_key_of(first, Path(scratch) / "copy")
            write_audit_bundle(manifest, rows, result, copy)
            self.assertEqual(files(copy), snapshot)
            # Under one key, codes still depend on the run hash and the audit seed.
            changed_run = {**manifest, "run_hash": "b" * 64}
            changed_seed = {**manifest, "config": {"analysis": {"audit_seed": 7}}}
            for name, changed in (("run", changed_run), ("seed", changed_seed)):
                other = with_key_of(first, Path(scratch) / name)
                write_audit_bundle(changed, rows, result, other)
                self.assertEqual(len(codes(other)), len(codes(first)))
                self.assertFalse(codes(other) & codes(first), name)

    def test_existing_bundle_without_key_is_not_extended(self):
        manifest, rows, result = fixture()
        with tempfile.TemporaryDirectory() as directory:
            built = Path(directory) / "built"
            write_audit_bundle(manifest, rows, result, built)
            (built / "private/code_key.json").unlink()
            # A key-less bundle is refused unchanged, and so is any audit.json, review/ or private/ remnant of one.
            for kept in (("audit.json", "review", "private"), ("audit.json",), ("review", "private"), ("review",),
                         ("private",)):
                root = Path(directory) / "-".join(kept)
                root.mkdir()
                for name in kept:
                    (shutil.copytree if (built / name).is_dir() else shutil.copy)(built / name, root / name)
                snapshot = files(root)
                with self.subTest(kept=kept):
                    with self.assertRaisesRegex(FileExistsError, "no private code key"):
                        write_audit_bundle(manifest, rows, result, root)
                    self.assertEqual(files(root), snapshot)

    def test_mismatched_existing_codes_are_refused_before_any_write(self):
        manifest, rows, result = fixture()
        with tempfile.TemporaryDirectory() as directory:
            root, other = Path(directory) / "bundle", Path(directory) / "other"
            write_audit_bundle(manifest, rows, result, root)
            write_audit_bundle(manifest, rows, result, other)
            snapshot = files(root)
            # Another run hash or seed under the bundle's key, or a replaced key, adds no review/ directories.
            for changed in ({**manifest, "run_hash": "b" * 64}, {**manifest, "config": {"analysis": {"audit_seed": 7}}}):
                with self.assertRaisesRegex(FileExistsError, "do not match this bundle's key"):
                    write_audit_bundle(changed, rows, result, root)
                self.assertEqual(files(root), snapshot)
            shutil.copy(other / "private/code_key.json", root / "private/code_key.json")
            snapshot = files(root)
            with self.assertRaisesRegex(FileExistsError, "do not match this bundle's key"):
                write_audit_bundle(manifest, rows, result, root)
            self.assertEqual(files(root), snapshot)

    def test_rejected_input_leaves_no_key_behind(self):
        manifest, rows, result = fixture()
        unknown = {"models": {"secret-model": {"paired_tasks": [
            {"task_id": "absent", "contrast": "compression_vs_standard", "mean_log2_T0_difference": 1.0}]}}}
        with tempfile.TemporaryDirectory() as directory:
            for name, args, message in (("duplicate", (manifest, rows + rows[:1], result), "Duplicate attempt"),
                                        ("unknown", (manifest, rows, unknown), "absent from frozen manifest")):
                with self.subTest(name):
                    with self.assertRaisesRegex(ValueError, message):
                        write_audit_bundle(*args, Path(directory) / name)
                    self.assertFalse((Path(directory) / name).exists())

    def test_malformed_key_and_missing_key_are_rejected(self):
        manifest, rows, result = fixture()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_audit_bundle(manifest, rows, result, root / "valid")
            valid = json.loads((root / "valid/private/code_key.json").read_text())
            for index, bad in enumerate(({**valid, "scheme": "sha256(run_hash|seed|kind|identity)[:16]"},
                                         {**valid, "key": valid["key"][:32]}, {**valid, "key": "Z" * 64},
                                         {**valid, "key": int("1" * 64)}, {"scheme": valid["scheme"]}, [valid])):
                target = root / f"bad{index}"
                (target / "private").mkdir(parents=True)
                (target / "private/code_key.json").write_text(json.dumps(bad))
                with self.assertRaisesRegex(ValueError, "Unrecognized audit code key"):
                    write_audit_bundle(manifest, rows, result, target)
                self.assertFalse((target / "review").exists())
        for missing in ((), (None,), (b"",)):
            with self.subTest(key=missing), self.assertRaisesRegex(ValueError, "private code key"):
                _opaque_id("proof", "a" * 64, "attempt", 20261012, *missing)
        self.assertNotEqual(_opaque_id("proof", "a" * 64, "attempt", 1, b"k" * 32),
                            _opaque_id("proof", "a" * 64, "attempt", 1, b"j" * 32))


class AuditTargetImportTests(unittest.TestCase):
    def test_target_files_keep_import_flags_exactly_as_prompted(self):
        # M8 omits every flag: the prompt then shows no `public`, although the engine defaults is_exported to true.
        specs = [{"module": f"M{i}", "is_exported": bool(i & 1), "is_meta": bool(i & 2), "import_all": bool(i & 4)}
                 for i in range(8)] + [{"module": "M8"}]
        tasks = [task("flagged", imports=[spec["module"] for spec in specs], import_specs=specs),
                 task("plain", imports=["Init", "Mathlib.Logic.Basic"])]
        manifest, rows, result = fixture(tasks, samples=1)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_audit_bundle(manifest, rows, result, root)
            audit = json.loads((root / "audit.json").read_text())
            by_id = {item["task_id"]: root / item["evidence_directory"] for item in audit["selected_tasks"]}
            for item in tasks:
                prompt = messages(item, "standard", "leanprover/lean4:test")[1]["content"]
                imports = prompt.split("Approved imports:\n", 1)[1].split("\n\nRelevant context:", 1)[0]
                target = json.loads((by_id[item["task_id"]] / "target.json").read_text())
                self.assertEqual(target["import_specs"], item.get("import_specs"))
                self.assertEqual(target["imports"], item["imports"])
                # Lean accepts public/meta imports only in a module, so flagged imports follow a module header.
                header = "module\n\n" if item.get("import_specs") else ""
                self.assertEqual((by_id[item["task_id"]] / "target.lean").read_text(),
                                 f"{header}{imports}\n\n{item['context']}\n\ntheorem {item['target_name']} "
                                 f"{item['binders']} :\n  {item['proposition']} := <PROOF>\n")
            flagged = (by_id["flagged"] / "target.lean").read_text().splitlines()
            self.assertEqual(flagged[:11], ["module", "", "import M0", "public import M1", "meta import M2",
                                            "public meta import M3", "import all M4", "public import all M5",
                                            "meta import all M6", "public meta import all M7", "import M8"])
            page = (root / "review/index.html").read_text()
            self.assertNotIn("Exact target", page)
            self.assertIn("Target template", page)
            self.assertIn("Fixed context and import flags", page)


class AuditTargetLeanTests(unittest.TestCase):
    def test_filled_meta_import_template_compiles(self):
        environment, lean = pinned_runtime()
        task_id = "lib_analysis_inequalities_main_pow_real_01"
        item = next(row for row in read_jsonl(ROOT / "data/public/tasks.jsonl") if row["task_id"] == task_id)
        proof = next(row["proof"] for row in read_jsonl(ROOT / "data/private/references.jsonl")
                     if row["task_id"] == task_id)
        self.assertTrue(any(spec["is_meta"] for spec in item["import_specs"]))
        manifest, rows, result = fixture([item], samples=1)
        process_env = {**os.environ, "LEAN_PATH": os.pathsep.join(environment["lean_search_paths"])}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_audit_bundle(manifest, rows, result, root / "audit")
            template = next((root / "audit/review").glob("task-*/target.lean")).read_text(encoding="utf-8")
            # The reference proof compiles and a wrong one does not, so the check is a real one.
            for name, filling, compiles in (("reference", proof, True), ("wrong", "Nat.zero", False)):
                (root / f"{name}.lean").write_text(template.replace("<PROOF>", filling), encoding="utf-8")
                checked = subprocess.run([lean, str(root / f"{name}.lean")], env=process_env,
                                         capture_output=True, text=True, timeout=300)
                self.assertEqual(checked.returncode == 0, compiles, checked.stdout + checked.stderr)


if __name__ == "__main__":
    unittest.main()
