"""Outcome taxonomy, provider retry semantics, and import validation.

The HTTP layer is mocked as in test_orchestration; no network, model, or Docker
daemon is used. The final class needs the pinned local Lean runtime and skips
without it.
"""
import contextlib
import http.client
import io
import json
import os
from pathlib import Path
import platform
import re
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import urllib.error

from lean_compression import cli
from lean_compression.analysis import CONDITIONS, analyze, write_report
from lean_compression.io import digest, file_hash, read_json, read_jsonl, write_json
from lean_compression.outcomes import (CHECKER_ERRORS, EXTRACTOR_OUTCOMES, GENERATION_OUTCOMES, MODEL_FAILURES,
                                       PROVIDER_STATUSES, outcome_class)
from lean_compression.providers import generate_run, import_responses, parse_response
from lean_compression.runner import evaluate_run
from lean_compression.store import create_run, record_generation, records

try:
    from lean_runtime import pinned_runtime
except ModuleNotFoundError:  # python3 -m unittest tests.test_outcome_taxonomy, run from the repository root
    from tests.lean_runtime import pinned_runtime

PROJECT = Path(__file__).resolve().parents[1]
ENGINE = PROJECT / "lean/Evaluator.lean"
MODEL = "pinned-model-2026-01-01"
TINY = [{"task_id": "tiny_01", "family_id": "fam_a", "domain": "sets_logic", "split": "pilot", "origin": "fresh",
         "stratum": "provisional_easy", "imports": ["Init"], "context": "", "premise_context": "",
         "forbidden_declarations": [], "target_name": "tiny_target_01", "binders": "(P : Prop) (h : P)",
         "proposition": "P ∧ P"},
        {"task_id": "tiny_02", "family_id": "fam_b", "domain": "sets_logic", "split": "pilot", "origin": "fresh",
         "stratum": "provisional_easy", "imports": ["Init"], "context": "", "premise_context": "",
         "forbidden_declarations": [], "target_name": "tiny_target_02", "binders": "(P Q : Prop) (hp : P) (hq : Q)",
         "proposition": "P ∧ Q"}]
# Hand-written expectations. Tests compare the allowlists with these, never with themselves.
MODEL_FAILURE_STATUSES = ("output_format_failure", "parse_failure", "elaboration_failure", "target_mismatch",
                          "forbidden_dependency", "disallowed_axiom", "validation_error", "kernel_replay_failure",
                          "verification_timeout", "verification_resource_limit", "refusal", "truncated_output",
                          "malformed_response")
CHECKER_STATUSES = ("infrastructure_error", "evaluator_error", "environment_error", "forbidden_environment")
# Every validity_status lean/Evaluator.lean reports, and the class each must have.
ENGINE_STATUS_CLASSES = {
    "success": "success", "parse_failure": "model_failure", "output_format_failure": "model_failure",
    "disallowed_axiom": "model_failure", "elaboration_failure": "model_failure", "target_mismatch": "model_failure",
    "forbidden_dependency": "model_failure", "validation_error": "model_failure",
    "kernel_replay_failure": "model_failure", "verification_timeout": "model_failure",
    "verification_resource_limit": "model_failure", "environment_error": "incomplete",
    "forbidden_environment": "incomplete", "evaluator_error": "incomplete"}
# What the process runner reports for the validation process: launch, result and cleanup failures, wall
# timeouts and signalled resource limits.
RUNNER_STATUS_CLASSES = {"infrastructure_error": "incomplete", "verification_timeout": "model_failure",
                         "verification_resource_limit": "model_failure"}


def engine_statuses(source):
    """Every status literal the engine source can pass to err or report as success; other call shapes fail."""
    replay = re.search(r"\ndef replayStatus\b.*?(?=\ndef )", source, re.S).group(0)
    statuses = set(re.findall(r'"validity_status", toJson "(\w+)"', source))
    for call in re.findall(r"(?<!def )\berr\b *([^\n]*)", source):
        literal = re.match(r'"(\w+)"', call)
        if literal:
            statuses.add(literal.group(1))
        elif call.startswith("(replayStatus "):
            statuses.update(re.findall(r'(?:then|else) "(\w+)"', replay))
        elif call.startswith("(if "):
            statuses.update(re.findall(r'(?:then|else) "(\w+)"', call[:call.index(")")]))
        else:
            raise AssertionError("Classify the status of this engine err call: " + call)
    return statuses


def http_error(code, headers=None, body="{}"):
    return urllib.error.HTTPError("https://api.openai.com/v1/responses", code, "error", headers or {},
                                  io.BytesIO(body.encode()))


def reply(status="completed", model=MODEL, content=None):
    content = [{"type": "output_text", "text": "by exact h"}] if content is None else content
    value = io.BytesIO(json.dumps({"id": "mock-response", "model": model, "status": status,
                                   "output": [{"type": "message", "content": content}]}).encode())
    value.headers = {}
    return value


def respond(*items):
    """urlopen side effect: exceptions are raised, callables build replies; the last item repeats."""
    queue = list(items)
    def side_effect(*args, **kwargs):
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, BaseException):
            raise item
        return item()
    return side_effect


def attempt(task_id, condition, index, status="success"):
    return {"task_id": task_id, "model_snapshot": MODEL, "condition": condition, "sample_index": index,
            "validity_status": status, "metric_status": "available" if status == "success" else "unavailable",
            "metrics": {"metric_status": "available", "W_S": 1, "W_B": 3, "U_S_decimal": "1",
                        "U_B_decimal": "7", "D": 2} if status == "success" else None}


def lean_success(engine_hash):
    return {"validity_status": "success", "metric_status": "available", "engine_hash": engine_hash,
            "validation": {"validity_status": "success", "kernel_replay": True},
            "metrics": {"metric_status": "available", "W_S": 1, "W_B": 3, "U_S_decimal": "1",
                        "U_B_decimal": "7", "D": 2}}


class RunFixture(unittest.TestCase):
    def setUp(self):
        self.config = read_json(PROJECT / "configs/pilot.json")
        image = "sha256:" + "0" * 64
        self.config["validation"].update(isolation="docker", docker_image=image)
        self.config["generation"].update(snapshot_pinned=True, settings_resolved=True)
        self.env = {"environment_hash": "fixture", "lean_toolchain": self.config["environment"]["lean_toolchain"],
                    "mathlib_commit": self.config["environment"]["mathlib_commit"],
                    "hash_mode": "development_metadata_only", "runtime_backend": "docker", "docker_image": image,
                    "source_exclusion_audit": {"lean_sources": 0, "git_directories": 0}}
        self.tasks = [{"task_id": "one", "family_id": "family", "domain": "sets_logic", "split": "pilot",
                       "origin": "fresh", "target_name": "testTarget", "binders": "(P : Prop) (h : P)",
                       "proposition": "P", "imports": ["Init"], "premise_context": "", "context": ""}]
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.run = Path(temp.name) / "run"
        self.manifest = create_run(self.run, self.tasks, self.config, self.env, split="pilot", model=MODEL,
                                   development=True)
        self.slots = self.manifest["slots"]

    def row(self, slot, **changes):
        return {"attempt_id": slot["attempt_id"], "prompt_hash": slot["prompt_hash"],
                "model_snapshot": slot["model_snapshot"], "raw_response": "by exact h", **changes}

    def generations(self):
        return sorted((self.run / "generations").glob("*.json"))

    def markers(self):
        return sorted((self.run / "inflight").glob("*.json"))

    def events(self):
        path = self.run / "transport.jsonl"
        return [event["event"] for event in read_jsonl(path)] if path.exists() else []

    def model(self, rows):
        config = self.manifest["config"]
        config = {**config, "analysis": {**config["analysis"], "bootstrap_replicates": 0}}
        return analyze(self.manifest["tasks"], rows, {}, config)["models"][MODEL]


class TaxonomyTests(unittest.TestCase):
    def test_only_allowlisted_statuses_are_model_outcomes(self):
        self.assertEqual(set(MODEL_FAILURES), set(MODEL_FAILURE_STATUSES))
        self.assertEqual(set(CHECKER_ERRORS), set(CHECKER_STATUSES))
        self.assertEqual(GENERATION_OUTCOMES, ("completed", "refusal", "truncated_output", "malformed_response"))
        self.assertEqual(set(PROVIDER_STATUSES), {"model_version_mismatch", "transport_error", "provider_failure"})
        self.assertEqual(set(EXTRACTOR_OUTCOMES), {"extractor_timeout", "extractor_resource_limit"})
        self.assertEqual(outcome_class("success"), "success")
        for status in MODEL_FAILURE_STATUSES:
            self.assertEqual(outcome_class(status), "model_failure", status)
        for status in (*CHECKER_STATUSES, "model_version_mismatch", "transport_error", "provider_failure",
                       "extractor_timeout", "extractor_resource_limit", "not_generated", "not_evaluated",
                       "missing_record", "unverified_success", "completed", "end_turn", "Success", None,
                       {"status": "success"}):
            self.assertEqual(outcome_class(status), "incomplete", status)
        # A generation record's own outcomes other than "completed" are final model failures.
        self.assertLessEqual(set(GENERATION_OUTCOMES) - {"completed"}, set(MODEL_FAILURES))

    def test_every_engine_and_runner_status_has_its_expected_class(self):
        self.assertEqual(engine_statuses(ENGINE.read_text(encoding="utf-8")), set(ENGINE_STATUS_CLASSES))
        for status, expected in (*ENGINE_STATUS_CLASSES.items(), *RUNNER_STATUS_CLASSES.items()):
            self.assertEqual(outcome_class(status), expected, status)


class ImportValidationTests(RunFixture):
    def test_generation_labels_that_are_not_model_outcomes_are_rejected(self):
        for status in ("success", "end_turn", "stop", None, "transport_error", "provider_failure",
                       "model_version_mismatch", "infrastructure_error", ["completed"]):
            with self.assertRaisesRegex(ValueError, "generation_status"):
                import_responses(self.run, [self.row(self.slots[0], generation_status=status)])
        self.assertEqual(self.generations(), [])

    def test_model_outcomes_are_accepted_and_default_to_completed(self):
        result = import_responses(self.run, [self.row(self.slots[0]),
                                             self.row(self.slots[1], generation_status="refusal", raw_response=""),
                                             self.row(self.slots[2], generation_status="truncated_output", usage=None)])
        self.assertEqual(result, {"imported": 3, "already_imported": 0})
        statuses = {read_json(path)["attempt_id"]: read_json(path)["generation_status"] for path in self.generations()}
        self.assertEqual(statuses, {self.slots[0]["attempt_id"]: "completed",
                                    self.slots[1]["attempt_id"]: "refusal",
                                    self.slots[2]["attempt_id"]: "truncated_output"})

    def test_malformed_rows_are_clear_errors(self):
        slot = self.slots[0]
        bad = [("raw_response", self.row(slot, raw_response=12345)),
               ("raw_response", self.row(slot, raw_response={"type": "output_text", "text": "h"})),
               ("raw_response", self.row(slot, raw_response=None)),
               ("usage", self.row(slot, usage="12 tokens")),
               ("lacks attempt_id", {key: value for key, value in self.row(slot).items() if key != "attempt_id"}),
               ("lacks raw_response", {key: value for key, value in self.row(slot).items() if key != "raw_response"}),
               ("JSON object", ["not", "an", "object"]),
               ("attempt ID", self.row(slot, attempt_id=["unhashable"])),
               ("attempt ID", self.row(slot, attempt_id="unscheduled")),
               ("frozen cohort", self.row(slot, model_snapshot="other-model")),
               ("prompt hash", self.row(slot, prompt_hash="BAD"))]
        for message, row in bad:
            with self.assertRaisesRegex(ValueError, message):
                import_responses(self.run, [row])
        self.assertEqual(self.generations(), [])

    def test_import_is_all_or_nothing(self):
        good = [self.row(slot) for slot in self.slots[:3]]
        for rows in ([*good, self.row(self.slots[3], prompt_hash="BAD")],
                     [*good, good[0]],
                     [*good, self.row(self.slots[3], model_snapshot="other-model")],
                     [*good, self.row(self.slots[3], generation_status="success")]):
            with self.assertRaises(ValueError):
                import_responses(self.run, rows)
            self.assertEqual(self.generations(), [])
        self.assertEqual(import_responses(self.run, good)["imported"], 3)

    def test_reimport_is_idempotent_but_changed_content_is_an_error(self):
        rows = [self.row(slot, usage={"output_tokens": 3}) for slot in self.slots[:2]]
        self.assertEqual(import_responses(self.run, rows), {"imported": 2, "already_imported": 0})
        self.assertEqual(import_responses(self.run, rows), {"imported": 0, "already_imported": 2})
        for changed in (self.row(self.slots[0], usage={"output_tokens": 3}, generation_status="refusal"),
                        self.row(self.slots[0], usage={"output_tokens": 4}),
                        self.row(self.slots[0], usage={"output_tokens": 3}, raw_response="by simp")):
            with self.assertRaisesRegex(FileExistsError, "different generation"):
                import_responses(self.run, [self.row(self.slots[2]), changed])
        self.assertEqual(len(self.generations()), 2)


class EvaluationOutcomeTests(RunFixture):
    def evaluate(self, *outcomes, limit=None):
        with patch("lean_compression.runner.evaluate_proof", side_effect=list(outcomes)) as check:
            result = evaluate_run(self.run, ENGINE, limit=limit)
        return result, check

    def test_generation_label_can_never_become_success(self):
        # Simulates a generation stored by an earlier, unvalidated import.
        record_generation(self.run, self.slots[0], "this is not a proof", generation_status="success")
        rows = records(self.run)
        legacy = next(row for row in rows if row["attempt_id"] == self.slots[0]["attempt_id"])
        self.assertEqual(legacy["validity_status"], "unrecognized_generation_status")
        self.assertTrue(legacy["scheduled_outcome_incomplete"])
        with self.assertRaisesRegex(ValueError, "Unrecognized generation_status 'success'"):
            self.evaluate()
        self.assertFalse((self.run / "evaluations/v1" / f"{self.slots[0]['attempt_id']}.json").exists())
        model = self.model(records(self.run))
        self.assertEqual(sum(c["valid_attempts"] for c in model["conditions"].values()), 0)
        self.assertFalse(model["run_complete"])

    def test_stored_success_without_lean_evidence_is_flagged(self):
        slot = self.slots[0]
        generation = record_generation(self.run, slot, "by exact h", generation_status="completed")
        write_json(self.run / "evaluations/v1" / f"{slot['attempt_id']}.json",
                   {"attempt_id": slot["attempt_id"], "validity_status": "success", "metric_status": "unavailable",
                    "metrics": None, "scheduled_outcome_incomplete": False,
                    "generation": generation, "generation_hash": digest(generation)})
        row = next(row for row in records(self.run) if row["attempt_id"] == slot["attempt_id"])
        self.assertEqual(row["validity_status"], "unverified_success")
        self.assertTrue(row["scheduled_outcome_incomplete"])
        self.assertEqual(sum(c["valid_attempts"] for c in self.model(records(self.run))["conditions"].values()), 0)

    def test_checker_errors_are_incidents_and_slots_are_retried(self):
        engine_hash = file_hash(ENGINE)
        for slot in self.slots:
            record_generation(self.run, slot, "by exact h", generation_status="completed")
        outage = [{"validity_status": status, "metric_status": "unavailable", "engine_hash": engine_hash,
                   "error": "docker: Cannot connect to the Docker daemon"}
                  for status in [*CHECKER_STATUSES, "unexpected_engine_status"] * 3]
        result, check = self.evaluate(*outage)
        self.assertEqual(result["new_evaluations"], 0)
        self.assertEqual(result["checker_incidents"], 12)
        self.assertEqual(sorted((self.run / "evaluations/v1").glob("*.json")), [self.run / "evaluations/v1/policy.json"])
        incidents = read_jsonl(self.run / "evaluations/v1/incidents.jsonl")
        self.assertEqual({row["attempt_id"] for row in incidents}, {slot["attempt_id"] for slot in self.slots})
        self.assertTrue(all(row["event"] == "checker_incident" and row["generation_hash"] and row["error"]
                            and row["adapted_response"] == "by exact h" for row in incidents))
        rows = records(self.run)
        self.assertEqual({row["validity_status"] for row in rows}, {"not_evaluated"})
        model = self.model(rows)
        self.assertFalse(model["run_complete"])
        self.assertFalse(model["correctness_conclusions_ready"])
        result, check = self.evaluate(*[lean_success(engine_hash) for _ in self.slots])
        self.assertEqual((result["new_evaluations"], result["checker_incidents"]), (12, 0))
        model = self.model(records(self.run))
        self.assertTrue(model["run_complete"])
        self.assertEqual(model["conditions"][CONDITIONS[0]]["success_rate"], 1)

    def test_each_validation_status_is_a_record_or_an_incident_as_expected(self):
        record_generation(self.run, self.slots[0], "by exact h", generation_status="completed")
        statuses = {**ENGINE_STATUS_CLASSES, **RUNNER_STATUS_CLASSES}
        del statuses["success"]
        for index, (status, expected) in enumerate(sorted(statuses.items())):
            version = self.run / "evaluations" / f"s{index}"
            with self.subTest(status=status), \
                 patch("lean_compression.runner.execute_engine",
                       return_value={"validity_status": status, "metric_status": "unavailable"}) as engine:
                result = evaluate_run(self.run, ENGINE, version=f"s{index}")
                written = [read_json(path) for path in version.glob("*.json") if path.name != "policy.json"]
                engine.assert_called_once()
                if expected == "model_failure":
                    self.assertEqual((result["new_evaluations"], result["checker_incidents"]), (1, 0))
                    self.assertEqual([record["validity_status"] for record in written], [status])
                    self.assertFalse((version / "incidents.jsonl").exists())
                else:
                    self.assertEqual((result["new_evaluations"], result["checker_incidents"]), (0, 1))
                    self.assertEqual(written, [])
                    self.assertEqual([row["validity_status"] for row in read_jsonl(version / "incidents.jsonl")], [status])

    def test_limit_counts_checker_incidents(self):
        engine_hash = file_hash(ENGINE)
        for slot in self.slots:
            record_generation(self.run, slot, "by exact h", generation_status="completed")
        result, check = self.evaluate(*[{"validity_status": "infrastructure_error", "metric_status": "unavailable",
                                         "engine_hash": engine_hash} for _ in range(3)], limit=2)
        self.assertEqual(check.call_count, 2)
        self.assertEqual(result["checker_incidents"], 2)

    def test_generation_outcomes_map_through_without_lean(self):
        statuses = {self.slots[0]["attempt_id"]: "refusal", self.slots[1]["attempt_id"]: "truncated_output",
                    self.slots[2]["attempt_id"]: "model_version_mismatch", self.slots[3]["attempt_id"]: "transport_error",
                    self.slots[4]["attempt_id"]: "provider_failure"}
        for slot in self.slots[:5]:
            record_generation(self.run, slot, "", generation_status=statuses[slot["attempt_id"]])
        result, check = self.evaluate()
        check.assert_not_called()
        self.assertEqual(result["new_evaluations"], 5)
        for attempt_id, status in statuses.items():
            stored = read_json(self.run / "evaluations/v1" / f"{attempt_id}.json")
            self.assertEqual(stored["validity_status"], status)
            self.assertEqual(stored["scheduled_outcome_incomplete"], status in PROVIDER_STATUSES)


class AnalysisClassificationTests(unittest.TestCase):
    def report(self, status):
        attempts = [attempt("a", condition, index) for condition in CONDITIONS for index in range(4)]
        attempts[4]["validity_status"] = status
        attempts[4]["metric_status"], attempts[4]["metrics"] = "unavailable", None
        return analyze([{"task_id": "a", "family_id": "a", "domain": "algebra", "split": "pilot"}], attempts,
                       {"a": attempts[0]["metrics"]}, {"bootstrap_replicates": 10})

    def test_checker_and_unrecognized_statuses_leave_the_run_incomplete(self):
        for status in (*CHECKER_STATUSES, "end_turn", None, "unverified_success", "unrecognized_generation_status"):
            report = self.report(status)
            model = report["models"][MODEL]
            self.assertFalse(model["run_complete"], status)
            self.assertFalse(model["correctness_conclusions_ready"], status)
            self.assertFalse(model["compression_conclusions_ready"], status)
            breakdown = model["conditions"][CONDITIONS[1]]["failure_breakdown"]
            self.assertEqual(breakdown["model_failures"], {})
            self.assertEqual(sum(breakdown["unresolved_outcomes"].values()), 1)
            with tempfile.TemporaryDirectory() as directory:
                paths = write_report(report, directory)
                text = Path(paths["html"]).read_text()
                self.assertIn("Run incomplete", text)
                self.assertIn("not classified as incorrect mathematical proofs", text)
                if status:
                    self.assertIn(status, text)
                self.assertIn("unresolved_attempts", Path(paths["correctness"]).read_text())

    def test_model_failures_are_final_and_reported_separately(self):
        for status in MODEL_FAILURE_STATUSES:
            report = self.report(status)
            model = report["models"][MODEL]
            self.assertTrue(model["run_complete"], status)
            self.assertTrue(model["correctness_conclusions_ready"], status)
            summary = model["conditions"][CONDITIONS[1]]
            self.assertEqual(summary["failure_breakdown"], {"model_failures": {status: 1}, "unresolved_outcomes": {}})
            self.assertEqual(summary["failure_statuses"], {status: 1})
            self.assertEqual(summary["success_rate"], .75)
            with tempfile.TemporaryDirectory() as directory:
                text = Path(write_report(report, directory)["html"]).read_text()
                self.assertNotIn("Run incomplete", text)
                self.assertIn(f"<td>{status}: 1</td><td>none</td>", text)


class ProviderTransportTests(RunFixture):
    def generate(self, *responses, key="unit-test-only", limit=None):
        sleeps = []
        with patch.dict(os.environ, {"OPENAI_API_KEY": key}), \
             patch("lean_compression.providers.subprocess.run", return_value=SimpleNamespace(returncode=0)), \
             patch("lean_compression.providers.time.sleep", side_effect=sleeps.append), \
             patch("lean_compression.providers.urllib.request.urlopen", side_effect=respond(*responses)) as request:
            try:
                outcome = generate_run(self.run, limit=limit)
            except (RuntimeError, ValueError) as error:
                outcome = error
        return outcome, request, sleeps

    def assert_released(self, outcome, request_count, request, pattern="No model outcome"):
        self.assertIsInstance(outcome, RuntimeError)
        self.assertRegex(str(outcome), pattern)
        self.assertEqual(request.call_count, request_count)
        self.assertEqual(self.generations(), [])
        self.assertEqual(self.markers(), [])
        self.assertEqual(self.events()[-1], "no_model_outcome")

    def test_retryable_error_then_success_records_one_model_sample(self):
        outcome, request, sleeps = self.generate(http_error(429), reply, limit=1)
        self.assertEqual(outcome["new_generation_records"], 1)
        self.assertEqual(request.call_count, 2)
        self.assertEqual(sleeps, [1])
        self.assertEqual(self.events(), ["request_started", "http_error", "request_started", "response_received"])
        generation = read_json(self.generations()[0])
        self.assertEqual((generation["attempt_id"], generation["generation_status"]),
                         (self.slots[0]["attempt_id"], "completed"))

    def test_exhausted_transport_failure_is_not_recorded_and_retries_the_same_attempt(self):
        outcome, request, sleeps = self.generate(http_error(503))
        self.assert_released(outcome, 3, request)
        self.assertEqual(sleeps, [1, 4])
        outcome, request, sleeps = self.generate(reply, limit=1)
        self.assertEqual(outcome["new_generation_records"], 1)
        self.assertEqual(read_json(self.generations()[0])["attempt_id"], self.slots[0]["attempt_id"])
        self.assertEqual({row["validity_status"] for row in records(self.run)}, {"not_generated", "not_evaluated"})

    def test_retry_after_is_honored_within_the_budget(self):
        outcome, request, sleeps = self.generate(http_error(429, {"Retry-After": "3600"}))
        self.assert_released(outcome, 1, request)
        self.assertEqual(sleeps, [])
        outcome, request, sleeps = self.generate(http_error(503, {"Retry-After": "7"}), reply, limit=1)
        self.assertEqual(outcome["new_generation_records"], 1)
        self.assertEqual(sleeps, [7])
        self.assertEqual(read_jsonl(self.run / "transport.jsonl")[-3]["retry_after"], "7")

    def test_configuration_errors_stop_without_retry(self):
        for code in (400, 401, 403, 404, 422):
            outcome, request, sleeps = self.generate(http_error(code, body='{"error": "unit-test-only"}'))
            self.assert_released(outcome, 1, request, "rejected the configuration")
            self.assertEqual(sleeps, [])
            self.assertIn("[REDACTED_API_KEY]", (self.run / "transport.jsonl").read_text())

    def test_provider_failure_is_not_a_model_outcome(self):
        for status in ("failed", None, "in_progress", "cancelled"):
            outcome, request, sleeps = self.generate(lambda status=status: reply(status))
            self.assert_released(outcome, 1, request)
            self.assertEqual(read_jsonl(self.run / "transport.jsonl")[-1]["provider_response"]["status"], status)

    def test_refusal_and_truncation_are_recorded_model_outcomes(self):
        refusal = [{"type": "refusal", "refusal": "I cannot help with that."}]
        self.assertEqual(parse_response(json.loads(reply("completed", content=refusal).read()))["generation_status"],
                         "refusal")
        outcome, request, sleeps = self.generate(lambda: reply("completed", content=refusal),
                                                 lambda: reply("incomplete"), limit=2)
        self.assertEqual(outcome["new_generation_records"], 2)
        self.assertEqual(sorted(read_json(path)["generation_status"] for path in self.generations()),
                         ["refusal", "truncated_output"])

    def test_lost_response_keeps_marker_and_is_never_resent(self):
        for error in (TimeoutError("The read operation timed out"),
                      http.client.RemoteDisconnected("Remote end closed connection without response"),
                      ConnectionResetError(54, "Connection reset by peer"), http.client.IncompleteRead(b"{")):
            with self.subTest(error=type(error).__name__):
                for marker in self.markers():
                    marker.unlink()
                outcome, request, sleeps = self.generate(error)
                self.assertIsInstance(outcome, RuntimeError)
                self.assertRegex(str(outcome), "outcome is unknown")
                self.assertEqual(request.call_count, 1)
                self.assertEqual(len(self.markers()), 1)
                self.assertEqual(self.events()[-1], "outcome_unknown")
                outcome, request, sleeps = self.generate(reply)
                self.assertRegex(str(outcome), "Unresolved in-flight call")
                request.assert_not_called()
        self.assertEqual(self.generations(), [])

    def test_unreadable_response_body_is_ambiguous(self):
        body = io.BytesIO(b"not json")
        body.headers = {}
        outcome, request, sleeps = self.generate(lambda: body)
        self.assertRegex(str(outcome), "outcome is unknown")
        self.assertEqual(len(self.markers()), 1)
        # So is a body cut off while reading; a complete one is also captured first (test_freeze_and_capture).
        def cut_off(*size):
            raise http.client.IncompleteRead(b'{"id": "mock-')
        self.markers()[0].unlink()
        body = io.BytesIO()
        body.headers, body.read = {}, cut_off
        outcome, request, sleeps = self.generate(lambda: body)
        self.assertRegex(str(outcome), "outcome is unknown")
        self.assertEqual(len(self.markers()), 1)

    def test_unsent_request_is_retried_then_released(self):
        refused = urllib.error.URLError(ConnectionRefusedError(61, "Connection refused"))
        outcome, request, sleeps = self.generate(refused)
        self.assert_released(outcome, 3, request)
        self.assertEqual(self.events().count("request_not_sent"), 3)
        outcome, request, sleeps = self.generate(refused, reply, limit=1)
        self.assertEqual(outcome["new_generation_records"], 1)

    def test_cli_reports_transport_failures_without_traceback(self):
        stderr = io.StringIO()
        with patch.dict(os.environ, {"OPENAI_API_KEY": "unit-test-only"}), \
             patch("lean_compression.providers.subprocess.run", return_value=SimpleNamespace(returncode=0)), \
             patch("lean_compression.providers.urllib.request.urlopen",
                   side_effect=http.client.RemoteDisconnected("closed")), \
             contextlib.redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as stopped:
                cli.main(["generate", "--run", str(self.run)])
        self.assertEqual(stopped.exception.code, 2)
        self.assertIn("outcome is unknown", stderr.getvalue())

    def test_api_key_is_validated_before_any_marker_and_never_echoed(self):
        for key in ("sk-unit\nSECRETVALUE", "sk-unit\rSECRETVALUE", "sk-unit SECRETVALUE", "sk-unit\x1bSECRETVALUE",
                    "sk-unit-SECRETVALUÉ"):
            stderr = io.StringIO()
            with patch.dict(os.environ, {"OPENAI_API_KEY": key}), \
                 patch("lean_compression.providers.subprocess.run", return_value=SimpleNamespace(returncode=0)), \
                 patch("lean_compression.providers.urllib.request.urlopen") as request, \
                 contextlib.redirect_stderr(stderr):
                with self.assertRaises(SystemExit):
                    cli.main(["generate", "--run", str(self.run)])
            request.assert_not_called()
            self.assertIn("OPENAI_API_KEY contains", stderr.getvalue())
            self.assertNotIn("SECRETVALU", stderr.getvalue())
            self.assertFalse((self.run / "transport.jsonl").exists())
            self.assertFalse((self.run / "inflight").exists())
        outcome, request, sleeps = self.generate(reply, key="  unit-test-only\r\n", limit=1)
        self.assertEqual(outcome["new_generation_records"], 1)
        self.assertEqual(request.call_args[0][0].get_header("Authorization"), "Bearer unit-test-only")

    def test_live_generation_requires_container_isolation(self):
        config = {**self.config, "validation": {**self.config["validation"], "isolation": "macos_sandbox"}}
        run = self.run.parent / "native"
        create_run(run, self.tasks, config, {**self.env, "runtime_backend": "native"}, split="pilot", model=MODEL,
                   development=True)
        with patch.dict(os.environ, {"OPENAI_API_KEY": "unit-test-only"}), \
             patch("lean_compression.providers.subprocess.run", return_value=SimpleNamespace(returncode=0)), \
             patch("lean_compression.providers.urllib.request.urlopen") as request:
            with self.assertRaisesRegex(ValueError, "require the container runtime profile"):
                generate_run(run)
        request.assert_not_called()


class ReferenceIncidentTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / "tasks.jsonl").write_text("".join(json.dumps(task) + "\n" for task in TINY))
        (self.root / "refs.jsonl").write_text('{"task_id": "tiny_01", "proof": "⟨h, h⟩"}\n'
                                              '{"task_id": "tiny_02", "proof": "⟨hp, hq⟩"}\n')
        write_json(self.root / "env.json", {"environment_hash": "fixture"})
        self.output = self.root / "checks"

    def verify(self, *outcomes):
        stderr, stdout = io.StringIO(), io.StringIO()
        # The checker is stubbed, so the fixture record need not describe this host's Lean;
        # test_environment_integrity covers load_environment.
        with patch("lean_compression.runner.evaluate_proof", side_effect=list(outcomes)), \
             patch("lean_compression.cli.load_environment", read_json), \
             contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(stdout):
            try:
                cli.main(["verify-references", "--tasks", str(self.root / "tasks.jsonl"),
                          "--references", str(self.root / "refs.jsonl"), "--environment", str(self.root / "env.json"),
                          "--config", str(PROJECT / "configs/development.json"), "--engine", str(ENGINE),
                          "--output", str(self.output)])
            except SystemExit as stopped:
                return stopped.code, stderr.getvalue()
        return json.loads(stdout.getvalue()), stderr.getvalue()

    def test_checker_incident_leaves_the_reference_pending_for_a_rerun(self):
        engine_hash = file_hash(ENGINE)
        outage = {"validity_status": "infrastructure_error", "metric_status": "unavailable",
                  "engine_hash": engine_hash, "failure_reason": "engine_changed_during_verification"}
        result, log = self.verify(outage, lean_success(engine_hash))
        self.assertEqual((result["new_checks"], result["checker_incidents"], result["verified"]), (1, 1, 1))
        self.assertIn("tiny_01: infrastructure_error (checker incident; task left pending)", log)
        incident, = read_jsonl(self.output / "incidents.jsonl")
        self.assertEqual((incident["task_id"], incident["failure_reason"]), ("tiny_01", "engine_changed_during_verification"))
        result, log = self.verify(lean_success(engine_hash))
        self.assertEqual((result["new_checks"], result["checked"], result["verified"]), (1, 2, 2))
        self.assertEqual({read_json(path)["record_kind"] for path in self.output.glob("*.json")
                          if path.name != "policy.json"}, {"verified_reference"})

    def test_record_from_a_different_engine_is_refused(self):
        code, log = self.verify({**lean_success("different-engine")})
        self.assertEqual(code, 2)
        self.assertIn("Engine changed during this run", log)
        self.assertEqual([path.name for path in self.output.glob("*.json")], ["policy.json"])


class LeanOutcomeIntegrationTests(unittest.TestCase):
    """Real Lean under the development sandbox; a transient launch failure is retried, not scored."""

    @classmethod
    def setUpClass(cls):
        if platform.system() != "Darwin":
            raise unittest.SkipTest("Needs the macOS development sandbox")
        cls.environment, _ = pinned_runtime()

    def test_transient_launch_failure_is_retried_and_only_lean_decides_success(self):
        config = read_json(PROJECT / "configs/development.json")
        config.update(samples_per_goal_condition=1)
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory) / "run"
            manifest = create_run(run, TINY[:1], config, self.environment, split="pilot", model="reference_fixture",
                                  development=True)
            slots = {slot["condition"]: slot for slot in manifest["slots"]}
            def row(condition, text, **extra):
                slot = slots[condition]
                return {"attempt_id": slot["attempt_id"], "prompt_hash": slot["prompt_hash"],
                        "model_snapshot": slot["model_snapshot"], "raw_response": text, **extra}
            with self.assertRaisesRegex(ValueError, "generation_status"):
                import_responses(run, [row("standard", "not a proof", generation_status="success")])
            import_responses(run, [row("standard", "⟨h, h⟩"),
                                   row("mathematical_compression", "I cannot help.", generation_status="refusal"),
                                   row("source_concision", "by exact h")])
            with patch("lean_compression.runner.subprocess.Popen",
                       side_effect=OSError(35, "Resource temporarily unavailable")):
                first = evaluate_run(run, ENGINE)
            self.assertEqual((first["new_evaluations"], first["checker_incidents"]), (1, 2))
            self.assertEqual({row["validity_status"] for row in read_jsonl(run / "evaluations/v1/incidents.jsonl")},
                             {"infrastructure_error"})
            settings = {**config, "analysis": {**config["analysis"], "bootstrap_replicates": 0}}
            model = analyze(manifest["tasks"], records(run), {}, settings)["models"]["reference_fixture"]
            self.assertFalse(model["run_complete"])
            second = evaluate_run(run, ENGINE)
            self.assertEqual((second["new_evaluations"], second["checker_incidents"]), (2, 0))
            rows = {row["condition"]: row for row in records(run)}
            self.assertEqual(rows["standard"]["validity_status"], "success")
            self.assertEqual(rows["standard"]["validation"]["validity_status"], "success")
            self.assertEqual(rows["source_concision"]["validity_status"], "elaboration_failure")
            model = analyze(manifest["tasks"], list(rows.values()), {}, settings)["models"]["reference_fixture"]
            self.assertTrue(model["run_complete"])
            self.assertEqual({condition: summary["failure_breakdown"]["model_failures"]
                              for condition, summary in model["conditions"].items()},
                             {"standard": {}, "mathematical_compression": {"refusal": 1},
                              "source_concision": {"elaboration_failure": 1}})


if __name__ == "__main__":
    unittest.main()
