import csv
import json
from pathlib import Path
import tempfile
import unittest

from lean_compression.audit import write_audit_bundle


class AuditTests(unittest.TestCase):
    def fixture(self, count=3):
        tasks = [{"task_id": f"t{i}", "family_id": f"family{i}", "domain": "sets_logic", "origin": "fresh",
                  "target_name": f"evaluation_target{i}", "binders": "", "proposition": "True",
                  "context": "", "premise_context": "", "imports": []} for i in range(count)]
        rows = []
        for task in tasks:
            for condition in ("standard", "mathematical_compression", "source_concision"):
                for sample in range(4):
                    identity = f"{task['task_id']}/{condition}/{sample}"
                    rows.append({"attempt_id": identity, "task_id": task["task_id"], "condition": condition,
                                 "sample_index": sample, "model_snapshot": "secret-model", "validity_status": "success",
                                 "metric_status": "available", "metrics": {"W_S": 1, "W_B": 2},
                                 "generation": {"raw_response": "```lean\nby trivial\n```"},
                                 "adapted_response": "by trivial",
                                 "extraction": {"root": "r", "nodes": {"r": {"primitive": True}},
                                                "source_tokens": {"W_S": 1, "W_B": 2}}})
        manifest = {"tasks": tasks, "config": {"analysis": {"audit_seed": 19}}, "run_hash": "frozen-run",
                    "slots": [{key: row[key] for key in ("attempt_id", "task_id", "condition", "sample_index", "model_snapshot")}
                              for row in rows]}
        paired = [{"task_id": t["task_id"], "contrast": "compression_vs_standard",
                   "mean_log2_T0_difference": i - count // 2,
                   "mean_log2_W_difference": 0, "mean_log2_U_difference": i - count // 2}
                  for i, t in enumerate(tasks)]
        result = {"models": {"secret-model": {"paired_tasks": paired}},
                  "run_provenance": {"label": "Development fixture; not LLM evidence"}}
        return manifest, rows, result

    def test_all_condition_outputs_with_blinded_review_and_blank_labels(self):
        manifest, rows, result = self.fixture()
        with tempfile.TemporaryDirectory() as directory:
            summary = write_audit_bundle(manifest, rows, result, directory)
            self.assertEqual(summary["selected_tasks"], 3)
            self.assertEqual(summary["attempts"], 36)
            root = Path(directory)
            labels = json.loads((root / "private/labels.json").read_text())
            self.assertEqual({label["condition"] for label in labels.values()},
                             {"standard", "mathematical_compression", "source_concision"})
            for path in (root / "review").rglob("*"):
                if path.is_file():
                    content = path.read_text()
                    self.assertNotIn("secret-model", content)
                    self.assertNotIn("mathematical_compression", content)
                    self.assertNotIn("source_concision", content)
            proof_dirs = list((root / "review").glob("task-*/proof-*"))
            self.assertEqual(len(proof_dirs), 36)
            self.assertEqual((proof_dirs[0] / "original.txt").read_text(), "```lean\nby trivial\n```")
            self.assertEqual((proof_dirs[0] / "proof.lean").read_text(), "by trivial")
            self.assertTrue((proof_dirs[0] / "dependencies.json").is_file())
            with (root / "review/annotations.csv").open() as handle:
                annotations = list(csv.DictReader(handle))
            self.assertEqual(len(annotations), 36)
            self.assertTrue(all(row["mechanism"] == "" and row["reviewer_notes"] == "" for row in annotations))
            # Rebuilding the same frozen packet is deterministic and safe.
            self.assertEqual(write_audit_bundle(manifest, rows, result, directory), summary)

    def test_extremes_and_random_sample_are_frozen_and_keep_all_attempts(self):
        manifest, rows, result = self.fixture(51)
        with tempfile.TemporaryDirectory() as directory:
            write_audit_bundle(manifest, rows, result, directory)
            audit = json.loads((Path(directory) / "audit.json").read_text())
            groups = audit["selection_by_contrast"]["secret-model/compression_vs_standard"]
            self.assertEqual(len(groups["largest_positive"]), 10)
            self.assertEqual(len(groups["largest_negative"]), 10)
            self.assertEqual(len(groups["random_paired"]), 20)
            self.assertEqual(groups["largest_positive"][0], "t50")
            self.assertEqual(groups["largest_negative"][0], "t0")
            self.assertTrue(all(task["attempt_count"] == 12 for task in audit["selected_tasks"]))
            self.assertTrue(all(task["paired_context"]["secret-model"] for task in audit["selected_tasks"]))

    def test_missing_scheduled_response_preserved_as_gap(self):
        manifest, rows, result = self.fixture()
        rows = rows[1:]
        with tempfile.TemporaryDirectory() as directory:
            summary = write_audit_bundle(manifest, rows, result, directory)
            self.assertEqual(summary["attempts"], 36)
            summaries = [json.loads(path.read_text()) for path in Path(directory).glob("review/task-*/proof-*/summary.json")]
            gaps = [row for row in summaries if not row["source_available"]]
            self.assertEqual(len(gaps), 1)
            self.assertEqual(gaps[0]["validity_status"], "missing_record")

    def test_duplicate_attempt_rejected(self):
        manifest, rows, result = self.fixture()
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "Duplicate attempt"):
                write_audit_bundle(manifest, rows + [rows[0]], result, directory)


if __name__ == "__main__":
    unittest.main()
