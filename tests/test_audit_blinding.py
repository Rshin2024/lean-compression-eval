import copy
import json
from pathlib import Path
import tempfile
import unittest

from lean_compression.audit import write_audit_bundle

CONDITIONS = ("standard", "mathematical_compression", "source_concision")


def fixture(count=4):
    tasks = [{"task_id": f"t{i}", "family_id": f"family{i}", "domain": "sets_logic", "origin": "fresh",
              "target_name": f"evaluation_target{i}", "binders": "", "proposition": "True",
              "context": "", "premise_context": "", "imports": []} for i in range(count)]
    rows = [{"attempt_id": f"{task['task_id']}/{condition}/{sample}", "task_id": task["task_id"],
             "condition": condition, "sample_index": sample, "model_snapshot": "secret-model",
             "validity_status": "success", "metric_status": "available", "metrics": {"W_S": 1, "W_B": 2},
             "generation": {"raw_response": "by trivial"}}
            for task in tasks for condition in CONDITIONS for sample in range(4)]
    manifest = {"tasks": tasks, "config": {"analysis": {"audit_seed": 19}}, "run_hash": "frozen-run",
                "slots": [{key: row[key] for key in ("attempt_id", "task_id", "condition", "sample_index",
                                                     "model_snapshot")} for row in rows]}
    paired = [{"task_id": task["task_id"], "contrast": "compression_vs_standard",
               "mean_log2_T0_difference": i - count // 2, "mean_log2_W_difference": 0,
               "mean_log2_U_difference": i - count // 2} for i, task in enumerate(tasks)]
    return manifest, rows, {"models": {"secret-model": {"paired_tasks": paired}}}


def snapshot(root):
    return {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}


class AuditBlindingTests(unittest.TestCase):
    def test_review_order_carries_no_arm_pattern(self):
        manifest, rows, result = fixture()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_audit_bundle(manifest, rows, result, root)
            labels = json.loads((root / "private/labels.json").read_text())
            selected = json.loads((root / "audit.json").read_text())["selected_tasks"]
            arms = [tuple(labels[proof["proof_code"]]["condition"] for proof in
                          json.loads((root / item["evidence_directory"] / "proofs.json").read_text()))
                    for item in selected]
        self.assertEqual(len(arms), 4)
        self.assertTrue(all(sorted(sequence) == sorted(CONDITIONS * 4) for sequence in arms))
        # Sorting by a hidden label would repeat one arm pattern for every task
        # and list each arm as one contiguous block.
        self.assertGreater(len(set(arms)), 1)
        grouped = [sum(a != b for a, b in zip(sequence, sequence[1:])) == len(CONDITIONS) - 1
                   for sequence in arms]
        self.assertFalse(all(grouped))

    def test_differing_rebuild_is_refused_without_changing_evidence(self):
        manifest, rows, result = fixture()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_audit_bundle(manifest, rows, result, root)
            before = snapshot(root)
            changed = copy.deepcopy(rows)
            changed[5]["generation"]["raw_response"] = "by simp"
            with self.assertRaises(FileExistsError):
                write_audit_bundle(manifest, changed, result, root)
            self.assertEqual(snapshot(root), before)
            labels = json.loads((root / "private/labels.json").read_text())
            code = next(code for code, label in labels.items() if label["attempt_id"] == rows[5]["attempt_id"])
            original = root / "review" / labels[code]["task_code"] / code / "original.txt"
            self.assertEqual(original.read_text(), "by trivial")

    def test_malformed_response_text_stays_private(self):
        # Its raw_response is the provider's whole reply, which names the model.
        manifest, rows, result = fixture()
        reply = '{"model": "secret-model", "status": "completed", "output": [{"type": "message", "text": "\\ud835"}]}'
        malformed = rows[::5]
        for row in malformed:
            row.update(validity_status="malformed_response", metric_status="unavailable", metrics=None,
                       generation={"raw_response": reply, "generation_status": "malformed_response"})
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_audit_bundle(manifest, rows, result, root)
            review = [path.read_text() for path in (root / "review").rglob("*") if path.is_file()]
            labels = json.loads((root / "private/labels.json").read_text())
            codes = [code for code, label in labels.items() if label["attempt_id"] in {row["attempt_id"] for row in malformed}]
            summaries = [json.loads((root / "review" / labels[code]["task_code"] / code / "summary.json").read_text())
                         for code in codes]
            kept = [json.loads((root / "private/attempts" / f"{code}.json").read_text()) for code in codes]
        self.assertEqual(len(codes), len(malformed))
        self.assertFalse([text for text in review if "secret-model" in text])
        self.assertEqual({(item["validity_status"], item["source_available"]) for item in summaries},
                         {("malformed_response", False)})
        self.assertEqual({record["generation"]["raw_response"] for record in kept}, {reply})


if __name__ == "__main__":
    unittest.main()
