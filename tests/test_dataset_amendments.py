"""Fast, data-driven consistency checks for the committed dataset and its amendment records.

These read the repository's data files only; no Lean process or model call is made.
"""
import hashlib
import json
from pathlib import Path
import unittest

from lean_compression.dataset import ANSWER_ACCESS_MIN_CHARS, sha256_text
from lean_compression.io import file_hash, read_json, read_jsonl, verification_request_hash

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "data" / "public"
PRIVATE = ROOT / "data" / "private"
SPLITS = ("smoke.jsonl", "fresh_main.jsonl", "library_candidates.jsonl")
REFERENCE_FILES = ("references.jsonl", "smoke_references.jsonl", "fresh_main_references.jsonl",
                   "library_candidate_references.jsonl")
# SHA-256 of dataset_amendments.jsonl as published before the 2026-10-12 amendments (226 rows).
LOG_PREFIX_ROWS = 226
LOG_PREFIX_SHA256 = "c00468798aef07061519ad2665d059d9a78ade2f48d71a39ad891d670b5bd986"


def normalized(text):
    return " ".join(text.split())


def path(relative):
    return ROOT / relative


class DatasetAmendmentConsistencyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lines = (PUBLIC / "tasks.jsonl").read_text(encoding="utf-8").splitlines()
        cls.tasks = [json.loads(line) for line in cls.lines]
        cls.by_id = {task["task_id"]: task for task in cls.tasks}
        cls.order = [task["task_id"] for task in cls.tasks]
        cls.amendments = read_jsonl(PRIVATE / "dataset_amendments.jsonl")
        cls.references = {row["task_id"]: row["proof"] for row in read_jsonl(PRIVATE / "references.jsonl")}
        cls.index = read_json(PRIVATE / "reference-index.json")

    def test_split_files_are_byte_identical_manifest_rows_in_manifest_order(self):
        line_of = {json.loads(line)["task_id"]: line for line in self.lines}
        position = {task_id: i for i, task_id in enumerate(self.order)}
        covered = []
        for name in SPLITS:
            text = (PUBLIC / name).read_text(encoding="utf-8")
            self.assertTrue(text.endswith("\n"), name)
            ids = []
            for line in text.splitlines():
                task_id = json.loads(line)["task_id"]
                self.assertEqual(line, line_of[task_id], f"{name}: {task_id} differs from tasks.jsonl")
                ids.append(task_id)
            self.assertEqual([position[t] for t in ids], sorted(position[t] for t in ids), name)
            covered.extend(ids)
        self.assertEqual(sorted(covered), sorted(self.order))

    def test_amendment_log_is_append_only_and_rows_name_current_tasks(self):
        data = (PRIVATE / "dataset_amendments.jsonl").read_bytes()
        prefix = b"".join(data.splitlines(keepends=True)[:LOG_PREFIX_ROWS])
        self.assertEqual(hashlib.sha256(prefix).hexdigest(), LOG_PREFIX_SHA256)
        for number, row in enumerate(self.amendments, 1):
            for key in ("date", "phase", "task_id", "reason"):
                self.assertIn(key, row, f"line {number}")
            self.assertIn(row["task_id"], self.by_id, f"line {number}")

    def test_latest_amendment_row_for_each_amended_field_matches_current_value(self):
        latest = {}
        for number, row in enumerate(self.amendments, 1):
            task_id = row["task_id"]
            new_task = row.get("new_task") or {}
            for field in ("family_id", "context", "premise_context", "import_specs"):
                if field in new_task:
                    latest[task_id, field] = ("value", new_task[field], number)
            if "new_reference" in row:
                latest[task_id, "proof"] = ("value", row["new_reference"]["proof"], number)
            for key, field, kind in (("new_family_id", "family_id", "value"), ("new_import_specs", "import_specs", "value"),
                                     ("new_context_sha256", "context", "sha256"),
                                     ("new_premise_sha256", "premise_context", "sha256"), ("new_audit", "audit", "value"),
                                     ("new_reference_proof", "proof", "value")):
                if key in row:
                    latest[task_id, field] = (kind, row[key], number)
        for (task_id, field), (kind, recorded, number) in latest.items():
            current = self.references[task_id] if field == "proof" else self.by_id[task_id].get(field)
            if kind == "sha256":
                self.assertEqual(recorded, sha256_text(current), f"line {number}: {task_id} {field}")
                self.assertEqual(recorded, self.by_id[task_id]["hashes"][field], f"line {number}: {task_id}")
            else:
                self.assertEqual(recorded, current, f"line {number}: {task_id} {field}")
        for task in self.tasks:
            if task["origin"] == "library":
                # Every current library runtime context and premise excerpt is explained by the log.
                self.assertIn((task["task_id"], "context"), latest)
                self.assertIn((task["task_id"], "premise_context"), latest)
            else:
                self.assertEqual(latest[task["task_id"], "audit"][1], task["audit"])

    def test_retroactive_context_records_reconstruct_the_recorded_pre_edit_context(self):
        rows = [r for r in self.amendments if r.get("record_kind") == "retroactive_record_of_earlier_edit"]
        self.assertEqual(sorted(r["task_id"] for r in rows),
                         sorted(t["task_id"] for t in self.tasks if t["origin"] == "library"))
        # Earlier library context hashes: new_context_sha256 (lines 8-27), new_task snapshots (28-29).
        recorded = {}
        for number, row in enumerate(self.amendments, 1):
            if "new_context_sha256" in row and "record_kind" not in row:
                recorded[number] = (row["task_id"], "new_context_sha256", row["new_context_sha256"])
            elif "new_task" in row and self.by_id[row["task_id"]]["origin"] == "library":
                hashes = row["new_task"]["hashes"]
                self.assertEqual(sha256_text(row["new_task"]["context"]), hashes["context"], f"line {number}")
                recorded[number] = (row["task_id"], "new_task.hashes.context", hashes["context"])
        cited = []
        for row in rows:
            context = self.by_id[row["task_id"]]["context"]
            self.assertTrue(context.endswith(row["appended_text"]))
            self.assertTrue(row["appended_text"].endswith(
                "\nassert_not_exists _root_." + self.by_id[row["task_id"]]["forbidden_declarations"][0] + "\n"))
            self.assertEqual(sha256_text(context[: len(context) - len(row["appended_text"])]), row["old_context_sha256"])
            self.assertEqual(sha256_text(context), row["new_context_sha256"])
            self.assertLess(row["edit_date"], row["date"])
            if row["validated_against"]:
                number = int(row["validated_against"].split("line ")[1].split()[0])
                task_id, field, value = recorded[number]
                self.assertEqual(row["validated_against"], f"dataset_amendments.jsonl line {number} {field}")
                self.assertEqual((task_id, value), (row["task_id"], row["old_context_sha256"]))
                cited.append(number)
        # Every earlier recorded library context hash validates exactly one retroactive row.
        self.assertEqual(sorted(cited), sorted(recorded))

    def test_no_reference_proof_text_occurs_in_its_premise_context(self):
        proofs = {task_id: {proof} for task_id, proof in self.references.items()}
        for name in REFERENCE_FILES[1:]:
            for row in read_jsonl(PRIVATE / name):
                proofs[row["task_id"]].add(row["proof"])
        for entry in self.index["entries"]:
            record = read_json(path(entry["evidence_path"]))
            for key in ("proof", "submitted_proof"):
                if isinstance(record.get(key), str):
                    proofs[entry["task_id"]].add(record[key])
        self.assertEqual(sorted(proofs), sorted(self.order))
        for task in self.tasks:
            premise = normalized(task["premise_context"])
            for proof in proofs[task["task_id"]]:
                text = normalized(proof)
                if len(text) >= ANSWER_ACCESS_MIN_CHARS:
                    # assertFalse keeps private proof text out of failure output.
                    self.assertFalse(text in premise, f"{task['task_id']}: reference proof text in premise_context")

    def test_reference_index_entries_hash_to_their_evidence_and_current_runtime_inputs(self):
        entries = self.index["entries"]
        self.assertEqual(len(entries), self.index["count"])
        self.assertEqual(sorted(e["task_id"] for e in entries), sorted(self.order))
        cohorts = {}
        for entry in entries:
            self.assertEqual(file_hash(path(entry["evidence_path"])), entry["evidence_file_sha256"], entry["task_id"])
            self.assertEqual(entry["verification_request_hash"], verification_request_hash(self.by_id[entry["task_id"]]))
            cohorts[entry["engine_hash"]] = cohorts.get(entry["engine_hash"], 0) + 1
        self.assertEqual(cohorts, self.index["engine_cohorts"])

    def test_every_archived_evidence_path_hashes_to_its_entry(self):
        archived = read_json(PRIVATE / "reference-index-before-normalization.json")
        moved = 0
        for entry in archived["entries"]:
            if "archived_evidence_path" in entry:
                moved += 1
                self.assertIn("/history-engine-" + entry["engine_hash"][:8] + "/", entry["archived_evidence_path"])
                self.assertEqual(file_hash(path(entry["archived_evidence_path"])), entry["evidence_file_sha256"],
                                 entry["task_id"])
            else:
                self.assertEqual(file_hash(path(entry["evidence_path"])), entry["evidence_file_sha256"], entry["task_id"])
        rows = [r for r in self.amendments if r.get("index_path") ==
                "data/private/reference-index-before-normalization.json"]
        corrections = {r["task_id"]: r for r in rows}
        self.assertEqual(len(corrections), moved)
        for entry in archived["entries"]:
            if "archived_evidence_path" in entry:
                row = corrections[entry["task_id"]]
                self.assertEqual(row["archived_evidence_path"], entry["archived_evidence_path"])
                self.assertEqual(row["evidence_file_sha256"], entry["evidence_file_sha256"])
        # The latest correction row records the archived index's current bytes.
        self.assertEqual(rows[-1]["new_index_sha256"], file_hash(PRIVATE / "reference-index-before-normalization.json"))

    def test_dataset_status_hashes_match_the_files(self):
        status = read_json(PUBLIC / "dataset_status.json")
        self.assertEqual(status["manifest_sha256"], file_hash(PUBLIC / "tasks.jsonl"))
        self.assertEqual(status["amendments_sha256"], file_hash(PRIVATE / "dataset_amendments.jsonl"))
        self.assertEqual(status["reference_index_sha256"], file_hash(PRIVATE / "reference-index.json"))
        self.assertEqual(status["references_sha256"], file_hash(PRIVATE / "references.jsonl"))
        self.assertEqual(status["task_count"], len(self.tasks))

    def test_pipeline_demo_supersession_event_matches_the_retained_versions(self):
        run = ROOT / "examples" / "pipeline-demo"
        events = [e for e in read_jsonl(run / "amendments.jsonl") if e["kind"] == "supersede_evaluation_version"]
        self.assertEqual([(e["source"], e["destination"]) for e in events], [("v1", "v2")])
        event = events[0]
        for version, prefix in (("v1", "source"), ("v2", "destination")):
            policy = run / "evaluations" / version / "policy.json"
            self.assertEqual(event[prefix + "_policy_sha256"], file_hash(policy))
        self.assertEqual(event["source_policy_engine_hash"], read_json(run / "evaluations/v1/policy.json")["engine_hash"])
        for attempt, engine in event["source_record_engine_hashes"].items():
            self.assertEqual(read_json(run / "evaluations/v1" / f"{attempt}.json")["engine_hash"], engine)
        destination = {read_json(p)["engine_hash"] for p in (run / "evaluations/v2").glob("*.json")}
        self.assertEqual(destination, {event["destination_engine_hash"]})
        self.assertEqual(read_json(ROOT / "examples/demo-reference-check/policy.json")["engine_hash"],
                         event["destination_engine_hash"])


if __name__ == "__main__":
    unittest.main()
