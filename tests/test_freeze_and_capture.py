"""Raw response capture and unreadable or malformed responses; freeze gates for the engine, pilot report, adapter, runtime and answer access.

The HTTP layer is mocked as in test_outcome_taxonomy. No network, model, Docker daemon, or Lean run is used.
"""
import base64
import contextlib
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import urllib.error

from lean_compression import cli
from lean_compression.dataset import DOMAINS
from lean_compression.integrity import pilot_report, require_completed_pilot
from lean_compression.io import digest, file_hash, measurement_hash, read_json, read_jsonl, verification_request_hash, write_json
from lean_compression.outcomes import outcome_class
from lean_compression.providers import generate_run
from lean_compression.runner import evaluate_run
from lean_compression.store import create_run, generation_path, records

PROJECT = Path(__file__).resolve().parents[1]
ENGINE = PROJECT / "lean/Evaluator.lean"
DEMO_RUN = PROJECT / "examples/pipeline-demo"
MODEL = "pinned-model-2026-01-01"
IMAGE = "sha256:" + "0" * 64
AUDITED = dict.fromkeys(("reference_verified", "explicit_signature_frozen", "answer_access_audited",
                         "family_reviewed", "metric_closure_verified"), True)


def task(name, split="pilot", index=0, **changes):
    return {"task_id": name, "family_id": "family_" + name, "domain": "sets_logic", "split": split, "origin": "fresh",
            "stratum": "provisional_easy", "imports": ["Init"], "context": "", "premise_context": "",
            "forbidden_declarations": [], "target_name": "target_" + name, "binders": f"(P : Prop) (h{index} : P)",
            "proposition": "P", "audit": dict(AUDITED), **changes}


def production(tasks, split="pilot"):
    """A production configuration, container environment, and matching verified references for one split."""
    config = read_json(PROJECT / "configs/pilot.json")
    config["validation"].update(isolation="docker", docker_image=IMAGE)
    config["generation"].update(snapshot_pinned=True, settings_resolved=True)
    env = {"environment_hash": "container", "lean_toolchain": config["environment"]["lean_toolchain"],
           "mathlib_commit": config["environment"]["mathlib_commit"], "hash_mode": "full",
           "runtime_backend": "docker", "docker_image": IMAGE,
           "source_exclusion_audit": {"lean_sources": 0, "git_directories": 0}}
    policy = {"kind": "verify-references", "environment_hash": "container", "engine_hash": "reference-engine",
              "measurement_hash": measurement_hash(config)}
    refs = {t["task_id"]: {"task_id": t["task_id"], "record_kind": "verified_reference", "validity_status": "success",
                           "metric_status": "available", "environment_hash": "container", "engine_hash": "reference-engine",
                           "verification_request_hash": verification_request_hash(t), "proof": "by assumption"}
            for t in tasks}
    return {"config": config, "env": env, "tasks": tasks, "refs": refs, "policy": policy, "split": split}


def freeze(path, fixture, *, development=False, **options):
    return create_run(path, fixture["tasks"], fixture["config"], fixture["env"], split=fixture["split"], model=MODEL,
                      development=development, reference_records=fixture["refs"], reference_policy=fixture["policy"],
                      **options)


def run_cli(*argv):
    """Exit status, printed JSON result (None on failure) and stderr; environment records here are synthetic."""
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr), \
            patch("lean_compression.cli.load_environment", read_json):
        try:
            cli.main([str(arg) for arg in argv])
        except SystemExit as stopped:
            return stopped.code, None, stderr.getvalue()
    return 0, json.loads(stdout.getvalue()), stderr.getvalue()


def response(text="by exact h", status="completed", model=MODEL, **fields):
    """Response bytes as the provider sends them; json.dumps writes a lone surrogate as its \\u escape."""
    return json.dumps({"id": "resp_BILLED", "model": model, "status": status,
                       "output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}], **fields}).encode()


def replies(*bodies):
    """urlopen side effect returning these exact bytes in turn; the last repeats."""
    queue = list(bodies)
    def reply(*args, **kwargs):
        value = io.BytesIO(queue.pop(0) if len(queue) > 1 else queue[0])
        value.headers = {"x-request-id": "req_unit"}
        return value
    return reply


def unavailable(body):
    """urlopen side effect: a 503 whose error body happens to look like a completed response."""
    def reply(*args, **kwargs):
        raise urllib.error.HTTPError("https://api.openai.com/v1/responses", 503, "error", {}, io.BytesIO(body))
    return reply


class ResponseCaptureTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.run = Path(temp.name) / "run"
        fixture = production([task("one", index=1)])
        # Bound reference checks fix the engine evaluate accepts.
        for record in (fixture["policy"], *fixture["refs"].values()):
            record["engine_hash"] = file_hash(ENGINE)
        self.slots = freeze(self.run, fixture, development=True)["slots"]

    def generate(self, side_effect, limit=None):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "unit-test-only"}), \
             patch("lean_compression.providers.subprocess.run", return_value=SimpleNamespace(returncode=0)), \
             patch("lean_compression.providers.time.sleep"), \
             patch("lean_compression.providers.urllib.request.urlopen", side_effect=side_effect) as request:
            try:
                outcome = generate_run(self.run, limit=limit)
            except Exception as error:  # the outcome under test, including unexpected failures
                outcome = error
        return outcome, request

    def captured(self, index):
        return [base64.b64decode(line["body_base64"])
                for line in read_jsonl(self.run / f"captures/{self.slots[index]['attempt_id']}.jsonl")]

    def generation(self, index):
        return read_json(generation_path(self.run, self.slots[index]["attempt_id"]))

    def markers(self):
        return sorted((self.run / "inflight").glob("*.json"))

    def test_received_bytes_are_captured_before_parsing_and_linked(self):
        data = response()
        outcome, request = self.generate(replies(data), limit=1)
        self.assertEqual(outcome["new_generation_records"], 1)
        attempt = self.slots[0]["attempt_id"]
        line, = read_jsonl(self.run / f"captures/{attempt}.jsonl")
        self.assertEqual((line["attempt_id"], line["transport_index"], line["request_id"]), (attempt, 0, "req_unit"))
        self.assertEqual(base64.b64decode(line["body_base64"]), data)
        capture = {"path": f"captures/{attempt}.jsonl", "body_sha256": hashlib.sha256(data).hexdigest()}
        received, = [event for event in read_jsonl(self.run / "transport.jsonl") if event["event"] == "response_received"]
        self.assertEqual(received["capture"], capture)
        self.assertEqual(self.generation(0)["response_capture"], capture)
        # Even an unforeseen parsing failure leaves the billed bytes captured and the slot behind its marker.
        with patch("lean_compression.providers.parse_response", side_effect=KeyError("unforeseen")):
            outcome, request = self.generate(replies(response("by simp")))
        self.assertIsInstance(outcome, KeyError)
        self.assertEqual(self.captured(1), [response("by simp")])
        self.assertEqual([marker.stem for marker in self.markers()], [self.slots[1]["attempt_id"]])

    def test_lone_surrogate_is_recorded_as_a_model_failure_and_the_slot_moves_on(self):
        # The audited case: a truncated output ending in half of an astral symbol such as 𝕜.
        usage, details = {"input_tokens": 812, "output_tokens": 4096}, {"reason": "max_output_tokens"}
        data = response("by exact \ud835", status="incomplete", usage=usage, incomplete_details=details)
        outcome, request = self.generate(replies(data, response()))
        self.assertIsInstance(outcome, RuntimeError)
        self.assertRegex(str(outcome), "malformed response .* further model calls stopped")
        self.assertEqual(request.call_count, 1)
        self.assertEqual(self.markers(), [])
        self.assertEqual(self.captured(0), [data])
        generation = self.generation(0)
        self.assertEqual(generation["generation_status"], "malformed_response")
        self.assertIn('"id": "resp_BILLED"', generation["raw_response"])
        self.assertIn("by exact \\ud835", generation["raw_response"])
        # Only the model's text is unusable: the billed usage and the other fields are kept as for any response.
        self.assertEqual({key: generation.get(key) for key in ("provider_response_id", "returned_model", "provider_status",
                                                              "usage", "incomplete_details")},
                         {"provider_response_id": "resp_BILLED", "returned_model": MODEL, "provider_status": "incomplete",
                          "usage": usage, "incomplete_details": details})
        # Only Lean decides success; this generation label is a final model failure that Lean never sees.
        with patch("lean_compression.runner.evaluate_proof") as checked:
            self.assertEqual(evaluate_run(self.run, ENGINE)["new_evaluations"], 1)
        checked.assert_not_called()
        row, = [row for row in records(self.run) if row["attempt_id"] == self.slots[0]["attempt_id"]]
        self.assertEqual((row["validity_status"], row["scheduled_outcome_incomplete"]), ("malformed_response", False))
        self.assertEqual(outcome_class(row["validity_status"]), "model_failure")
        # Rerunning generate continues with the next slot; the recorded one is never resent.
        outcome, request = self.generate(replies(response()), limit=1)
        self.assertEqual(outcome["new_generation_records"], 1)
        self.assertEqual(self.generation(1)["generation_status"], "completed")
        self.assertEqual(self.captured(0), [data])

    def test_a_prompt_rejection_is_captured_and_the_run_moves_on(self):
        # A `failed` response refusing the prompt itself is a body like any other: captured before it is read.
        failed = json.dumps({"id": "resp_FLAGGED", "model": MODEL, "status": "failed", "output": [],
                             "error": {"code": "invalid_prompt", "message": "Invalid prompt: flagged"}}).encode()
        # The rejected request uses up one of the two the limit allows.
        outcome, request = self.generate(replies(failed, response()), limit=2)
        self.assertEqual((outcome["new_generation_records"], outcome["prompt_rejections"]), (1, 1))
        self.assertEqual((self.captured(0), self.captured(1)), ([failed], [response()]))
        self.assertFalse(generation_path(self.run, self.slots[0]["attempt_id"]).exists())
        self.assertEqual(self.markers(), [])
        rejected, = [event for event in read_jsonl(self.run / "transport.jsonl") if event["event"] == "no_model_outcome"]
        self.assertEqual((rejected["attempt_id"], rejected["prompt_rejected"], rejected["provider_response"]["id"]),
                         (self.slots[0]["attempt_id"], True, "resp_FLAGGED"))

    def test_unstorable_text_of_the_pinned_model_is_a_malformed_response(self):
        refusal = [{"type": "message", "content": [{"type": "refusal", "refusal": "I cannot \ud835"}]}]
        bodies = {"encoded surrogate in the text": response().replace(b"by exact h", b"by exact \xed\xa0\xb5"),
                  "lone surrogate in a refusal": response(output=refusal)}
        for index, (name, data) in enumerate(bodies.items()):
            with self.subTest(name):
                outcome, request = self.generate(replies(data))
                self.assertRegex(str(outcome), "malformed response")
                self.assertEqual(self.generation(index)["generation_status"], "malformed_response")
                self.assertEqual(self.generation(index)["returned_model"], MODEL)
                self.assertEqual(self.captured(index), [data])
                self.assertEqual(self.markers(), [])

    def test_unreadable_bodies_record_nothing_and_keep_the_slot_for_reconciliation(self):
        # Nothing shows what the pinned model returned, so no outcome is recorded and the possibly billed call is not resent unasked.
        bodies = {"empty": b"", "HTML error page": b"<html><body>502 Bad Gateway</body></html>", "not an object": b"[]",
                  "null output": response(output=None), "output not a list": response(output=""),
                  "content not a list": response(output=[{"type": "message", "content": ""}]),
                  "text not a string": response().replace(b'"by exact h"', b"5"),
                  "NaN usage": response().replace(b'"status"', b'"usage": {"input_tokens": NaN}, "status"'),
                  "lone surrogate outside the text": response(id="resp_\ud835"),
                  "another model's lone surrogate": response("by exact \ud835", status="incomplete", model="other-model-2027-01-01")}
        for index, (name, data) in enumerate(bodies.items()):
            with self.subTest(name):
                attempt = self.slots[index]["attempt_id"]
                outcome, request = self.generate(replies(data))
                self.assertRegex(str(outcome), "outcome is unknown .* in-flight marker")
                self.assertEqual(request.call_count, 1)
                self.assertFalse(generation_path(self.run, attempt).exists())
                self.assertEqual(self.captured(index), [data])
                self.assertEqual([marker.stem for marker in self.markers()], [attempt])
                event = read_jsonl(self.run / "transport.jsonl")[-1]
                self.assertEqual((event["event"], event["capture"]), ("outcome_unknown", {
                    "path": f"captures/{attempt}.jsonl", "body_sha256": hashlib.sha256(data).hexdigest()}))
                row, = [row for row in records(self.run) if row["attempt_id"] == attempt]
                self.assertEqual((row["validity_status"], row["scheduled_outcome_incomplete"]), ("not_generated", True))
                # The marker holds the slot until the transport log is reconciled; then the same attempt ID is sent again.
                outcome, request = self.generate(replies(response()))
                self.assertRegex(str(outcome), "Unresolved in-flight call")
                request.assert_not_called()
                self.markers()[0].unlink()
                outcome, request = self.generate(replies(response()), limit=1)
                self.assertEqual(outcome["new_generation_records"], 1)
                self.assertEqual(self.generation(index)["generation_status"], "completed")
                self.assertEqual(self.captured(index), [data, response()])

    def test_provider_failures_are_not_model_outcomes_whatever_their_output_holds(self):
        error, refusal = {"code": "server_error"}, [{"type": "message", "content": [{"type": "refusal", "refusal": "No."}]}]
        nan = b'"usage": {"input_tokens": NaN}, "status"'
        bodies = {"failed, text ending in a lone surrogate": (response("by exact \ud835", status="failed", error=error), False),
                  "failed, null output": (response(status="failed", output=None, error=error), True),
                  "failed, refusal": (response(status="failed", output=refusal, error=error), True),
                  "failed, NaN usage": (response(status="failed", error=error).replace(b'"status"', nan), False),
                  "cancelled, null output": (response(status="cancelled", output=None), True)}
        for index, (name, (data, logged)) in enumerate(bodies.items()):
            with self.subTest(name):
                attempt = self.slots[index]["attempt_id"]
                outcome, request = self.generate(replies(data))
                self.assertRegex(str(outcome), "No model outcome")
                self.assertFalse(generation_path(self.run, attempt).exists())
                self.assertEqual(self.markers(), [])
                self.assertEqual(self.captured(index), [data])
                event = read_jsonl(self.run / "transport.jsonl")[-1]
                # The log holds the response when a record can; the capture always does.
                self.assertEqual((event["event"], event["provider_response"]),
                                 ("no_model_outcome", json.loads(data) if logged else None))
                # Rerunning generate sends the same attempt ID again.
                outcome, request = self.generate(replies(response()), limit=1)
                self.assertEqual(outcome["new_generation_records"], 1)
                self.assertEqual(self.generation(index)["generation_status"], "completed")
                self.assertEqual(self.captured(index), [data, response()])

    def test_http_error_bodies_are_never_read_as_responses(self):
        outcome, request = self.generate(unavailable(response()))
        self.assertRegex(str(outcome), "No model outcome")
        self.assertEqual(request.call_count, 3)
        self.assertFalse((self.run / "generations").exists())
        self.assertFalse((self.run / "captures").exists())
        self.assertIsNone(read_jsonl(self.run / "transport.jsonl")[-1]["provider_response"])


class FrozenEngineTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        config = read_json(PROJECT / "configs/development.json")
        write_json(self.root / "config.json", config)
        write_json(self.root / "env.json", {"environment_hash": "fixture", "hash_mode": "development_metadata_only",
                                            "lean_toolchain": config["environment"]["lean_toolchain"],
                                            "mathlib_commit": config["environment"]["mathlib_commit"]})
        (self.root / "tasks.jsonl").write_text(json.dumps(task("one", index=1)) + "\n")
        self.engine = self.root / "engine/Evaluator.lean"
        self.engine.parent.mkdir()
        self.engine.write_text("-- stand-in for an archived engine\n")

    def freeze(self, name, *engine):
        return run_cli("freeze", "--development", "--tasks", self.root / "tasks.jsonl", "--environment", self.root / "env.json",
                       "--config", self.root / "config.json", "--split", "pilot", "--model", "reference_fixture",
                       "--run", self.root / name, *engine)

    def test_every_freeze_records_its_engine_and_evaluate_refuses_another(self):
        code, result, log = self.freeze("run", "--engine", self.engine)
        self.assertEqual(code, 0, log)
        manifest = read_json(self.root / "run/manifest.json")
        self.assertEqual(result["engine_hash"], manifest["engine_hash"])
        self.assertEqual(manifest["engine_hash"], file_hash(self.engine))
        with self.assertRaisesRegex(ValueError, "frozen with this run"):
            evaluate_run(self.root / "run", ENGINE)
        self.assertFalse((self.root / "run/evaluations").exists())
        self.assertEqual(evaluate_run(self.root / "run", self.engine)["new_evaluations"], 0)
        # Without --engine, freeze binds lean/Evaluator.lean in the working directory, as evaluate's own default
        # names it. A working directory with its own engine keeps this independent of where the tests run from.
        defaults = [cli.parser().parse_args(argv).engine for argv in (
            ["freeze", "--tasks", "t", "--environment", "e", "--config", "c", "--run", "r", "--split", "pilot", "--model", "m"],
            ["evaluate", "--run", "r"])]
        self.assertEqual(defaults, [Path("lean/Evaluator.lean")] * 2)
        workdir = self.root / "workdir"
        (workdir / "lean").mkdir(parents=True)
        (workdir / "lean/Evaluator.lean").write_text("-- the working directory's engine\n")
        with contextlib.chdir(workdir):
            code, result, log = self.freeze("default")
        self.assertEqual(code, 0, log)
        self.assertEqual(read_json(self.root / "default/manifest.json")["engine_hash"],
                         file_hash(workdir / "lean/Evaluator.lean"))
        # Manifests frozen before the field existed keep today's behavior.
        legacy = {key: value for key, value in manifest.items() if key not in ("engine_hash", "run_hash")}
        write_json(self.root / "legacy/manifest.json", {**legacy, "run_hash": digest(legacy)})
        self.assertEqual(evaluate_run(self.root / "legacy", ENGINE)["new_evaluations"], 0)

    def test_reference_checks_bind_the_engine_that_verified_them(self):
        fixture = production([task("one", index=1)])
        # Development binds bound checks' engine too (reference_engine_hash), so freeze refuses another there as well.
        for development in (False, True):
            with self.subTest(development=development):
                with self.assertRaisesRegex(ValueError, "Engine differs from the one that verified the reference checks"):
                    freeze(self.root / "other", fixture, development=development, engine_hash="other-engine")
                self.assertFalse((self.root / "other").exists())
                for name, engine_hash in (("given", "reference-engine"), ("implied", None)):
                    manifest = freeze(self.root / f"{name}-{development}", fixture, development=development,
                                      engine_hash=engine_hash)
                    self.assertEqual((manifest["engine_hash"], manifest["reference_engine_hash"]), ("reference-engine",) * 2)


class PilotReportGateTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.base = production([task("one", "main", 1), task("two", "main", 2)], split="main")
        config = self.base["config"]
        self.pilot = {"analysis_version": "task_weighted_family_bootstrap_v1",
                      "run_provenance": {"run_hash": "pilot-run", "development": False, "split": "pilot", "model_snapshot": MODEL,
                                         "experiment": config["experiment"], "protocol_version": config["protocol_version"]},
                      "models": {MODEL: {"run_complete": True}}}

    def report(self, name, analysis):
        """An analyze output directory, and the fixture whose preregistration cites its analysis.json."""
        write_json(self.root / name / "analysis.json", analysis)
        fixture = copy.deepcopy(self.base)
        fixture["config"]["preregistration"] = {"pilot_report_hash": file_hash(self.root / name / "analysis.json")}
        return pilot_report(self.root / name), fixture

    def test_main_freeze_requires_the_preregistered_complete_production_pilot(self):
        report, fixture = self.report("pilot", self.pilot)
        self.assertEqual(freeze(self.root / "main", fixture, pilot_report=report)["split"], "main")
        provenance = lambda **changes: {**self.pilot, "run_provenance": {**self.pilot["run_provenance"], **changes}}
        legacy = {key: value for key, value in self.pilot["run_provenance"].items()
                  if key not in ("split", "experiment", "protocol_version")}
        other = "other-model-2026-01-01"
        cases = [("development pilot", provenance(development=True), r"\(development\)"),
                 ("main split", provenance(split="main"), r"\(split\)"),
                 ("other experiment", provenance(experiment="other"), r"\(experiment\)"),
                 ("other protocol", provenance(protocol_version="draft_v2"), r"\(protocol_version\)"),
                 ("another model's complete pilot", {**provenance(model_snapshot=other), "models": {other: {"run_complete": True}}},
                  r"\(model_snapshot\)"),
                 ("provenance of another model", provenance(model_snapshot=other), r"\(model_snapshot\)"),
                 ("this model not analyzed", {**self.pilot, "models": {other: {"run_complete": True}}}, r"\(model_snapshot\)"),
                 ("incomplete run", {**self.pilot, "models": {MODEL: {"run_complete": False}}}, r"\(run_complete\)"),
                 ("no models", {**self.pilot, "models": {}}, r"\(model_snapshot, run_complete\)"),
                 ("older report", {**self.pilot, "run_provenance": legacy}, r"\(split, experiment, protocol_version\)"),
                 ("not an analysis", [], r"\(development, split, experiment, protocol_version, model_snapshot, run_complete\)")]
        for index, (name, analysis, message) in enumerate(cases):
            with self.subTest(name):
                report, fixture = self.report(f"case{index}", analysis)
                with self.assertRaisesRegex(ValueError, "not the analysis of a complete production pilot of this model and protocol "
                                            + message):
                    freeze(self.root / f"run{index}", fixture, pilot_report=report)
                self.assertFalse((self.root / f"run{index}").exists())
        report, fixture = self.report("pilot", self.pilot)
        other, _ = self.report("other", {**self.pilot, "evaluated_at": "later"})
        for name, cited in (("no report", None), ("another report", other)):
            with self.subTest(name), self.assertRaisesRegex(ValueError, "--pilot-report"):
                freeze(self.root / "refused", fixture, pilot_report=cited)
        del fixture["config"]["preregistration"]
        with self.assertRaisesRegex(ValueError, "completed pilot report hash"):
            freeze(self.root / "refused", fixture, pilot_report=report)
        self.assertFalse((self.root / "refused").exists())
        # Development freezes are unaffected.
        self.assertTrue(freeze(self.root / "development", self.base, development=True)["development"])
        with self.assertRaisesRegex(ValueError, "must be an analyze output directory"):
            pilot_report(self.root / "absent")

    def test_cli_main_freeze_reads_the_pilot_report_directory(self):
        tasks = []
        for split, count in (("pilot", 10), ("main", 40)):
            for domain in DOMAINS:
                for index in range(count):
                    tasks.append(task(f"{split}_{domain}_{index:02d}", split, len(tasks), domain=domain,
                                      origin="library" if split == "main" and index < 30 else "fresh"))
        fixture = production([item for item in tasks if item["split"] == "main"], split="main")
        report, _ = self.report("pilot", self.pilot)
        fixture["config"]["preregistration"] = {"pilot_report_hash": report["sha256"]}
        (self.root / "tasks.jsonl").write_text("".join(json.dumps(item) + "\n" for item in tasks))
        write_json(self.root / "config.json", fixture["config"])
        write_json(self.root / "env.json", fixture["env"])
        write_json(self.root / "refs/policy.json", {**fixture["policy"], "engine_hash": file_hash(ENGINE)})
        for task_id, ref in fixture["refs"].items():
            write_json(self.root / "refs" / f"{digest(task_id)}.json", {**ref, "engine_hash": file_hash(ENGINE)})
        def freeze_main(name, *pilot):
            return run_cli("freeze", "--tasks", self.root / "tasks.jsonl", "--environment", self.root / "env.json",
                           "--config", self.root / "config.json", "--split", "main", "--model", MODEL, "--engine", ENGINE,
                           "--reference-checks", self.root / "refs", "--run", self.root / name, *pilot)
        code, result, log = freeze_main("unregistered")
        self.assertEqual(code, 2)
        self.assertIn("--pilot-report", log)
        self.assertFalse((self.root / "unregistered").exists())
        code, result, log = freeze_main("main", "--pilot-report", self.root / "pilot")
        self.assertEqual(code, 0, log)
        self.assertEqual((result["development"], result["scheduled_attempts"]), (False, 2400))

    def test_analyze_records_the_provenance_a_main_freeze_checks(self):
        run = self.root / "pipeline-demo"
        shutil.copytree(DEMO_RUN, run)
        code, result, log = run_cli("analyze", "--run", run, "--version", "v2", "--output", self.root / "report")
        self.assertEqual(code, 0, log)
        manifest = read_json(run / "manifest.json")
        provenance = read_json(self.root / "report/analysis.json")["run_provenance"]
        self.assertEqual({key: provenance[key] for key in ("split", "experiment", "protocol_version", "development", "model_snapshot")},
                         {"split": "pilot", "experiment": manifest["config"]["experiment"],
                          "protocol_version": manifest["config"]["protocol_version"], "development": True,
                          "model_snapshot": manifest["model_snapshot"]})
        # The demo is a complete pilot of its model and protocol, but a development run cannot be preregistered.
        config = {**manifest["config"], "preregistration": {"pilot_report_hash": file_hash(self.root / "report/analysis.json")}}
        with self.assertRaisesRegex(ValueError, r"protocol \(development\)$"):
            require_completed_pilot(config, pilot_report(self.root / "report"), manifest["model_snapshot"])


class CreateRunGateTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.base = production([task("one", index=1)])

    def assert_refused(self, fixture, message, name, **options):
        with self.assertRaisesRegex(ValueError, message):
            freeze(self.root / name, fixture, **options)
        self.assertFalse((self.root / name).exists())

    def test_only_implemented_response_adapters_freeze(self):
        for adapter in ("single_fence", "identity", None):
            with self.subTest(adapter=adapter):
                fixture = copy.deepcopy(self.base)
                fixture["config"]["generation"]["response_adapter"] = adapter
                self.assert_refused(fixture, f"Unsupported generation.response_adapter {adapter!r}", "refused", development=True)
        fixture = copy.deepcopy(self.base)
        del fixture["config"]["generation"]["response_adapter"]
        self.assert_refused(fixture, "Unsupported generation.response_adapter None", "refused", development=True)
        for adapter in ("identity_v1", "single_fence_v1"):
            fixture = copy.deepcopy(self.base)
            fixture["config"]["generation"]["response_adapter"] = adapter
            self.assertTrue(freeze(self.root / adapter, fixture, development=True)["development"])

    def test_lean_runtime_and_environment_record_agree_in_both_directions(self):
        host = {"runtime_backend": None, "docker_image": None}
        tag, digest_reference = "lean-compression-eval:latest", "registry.example/lean@sha256:" + "1" * 64
        cases = [("docker isolation, host record", "docker", IMAGE, host, "does not match validation.isolation 'docker'"),
                 ("native isolation, container record", "macos_sandbox", None, {}, "does not match validation.isolation 'macos_sandbox'"),
                 ("absent isolation is Docker", None, None, host, "does not match validation.isolation 'docker'"),
                 ("mutable tag", "docker", tag, {"docker_image": tag}, "not an immutable content digest"),
                 ("no image", "docker", None, {"docker_image": None}, "not an immutable content digest"),
                 ("other image", "docker", IMAGE, {"docker_image": digest_reference}, "same pinned image")]
        for index, (name, isolation, image, env, message) in enumerate(cases):
            with self.subTest(name):
                fixture = copy.deepcopy(self.base)
                fixture["config"]["validation"].update(isolation=isolation, docker_image=image)
                if isolation is None:
                    del fixture["config"]["validation"]["isolation"]
                fixture["env"].update(env)
                self.assert_refused(fixture, message, f"run{index}", development=True)
        for name, isolation, image, env in (("repository digest", "docker", digest_reference, {"docker_image": digest_reference}),
                                            ("native host record", "macos_sandbox", None, host)):
            fixture = copy.deepcopy(self.base)
            fixture["config"]["validation"].update(isolation=isolation, docker_image=image)
            fixture["env"].update(env)
            fixture["policy"]["measurement_hash"] = measurement_hash(fixture["config"])
            self.assertTrue(freeze(self.root / name, fixture, development=True)["development"])

    def test_production_freeze_screens_the_checked_proofs_for_answer_access(self):
        proof = "exact helper_lemma_for_one h1"
        leaked = copy.deepcopy(self.base)
        leaked["tasks"][0]["premise_context"] = f"theorem helper : P := {proof}"
        leaked["refs"]["one"].update(proof=proof, verification_request_hash=verification_request_hash(leaked["tasks"][0]))
        self.assert_refused(leaked, "copyable from the model prompts: one: private reference proof", "leaked")
        # The screen reads only the checked proofs: below its length threshold, or in development, nothing is refused.
        self.assertTrue(freeze(self.root / "development", leaked, development=True)["development"])
        short = copy.deepcopy(leaked)
        short["tasks"][0]["premise_context"] = "theorem helper : P := h1"
        short["refs"]["one"].update(proof="h1", verification_request_hash=verification_request_hash(short["tasks"][0]))
        self.assertFalse(freeze(self.root / "short", short)["development"])
        for name, proof in (("no proof", None), ("blank proof", "  ")):
            unscreened = copy.deepcopy(self.base)
            unscreened["refs"]["one"]["proof"] = proof
            self.assert_refused(unscreened, r"one \(proof\)", name)


if __name__ == "__main__":
    unittest.main()
