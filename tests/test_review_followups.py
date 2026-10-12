"""Review follow-ups: extraction-phase checker incidents, incident visibility, helper counts, the
evaluate_run gates, and the doctor and readiness client details.

The process runner, the engine or the Docker client is replaced by fakes; no network, model or Docker
daemon is used. Lean classes use the pinned local runtime and skip without it.
"""
import contextlib
import errno
import io
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from lean_compression import cli
from lean_compression.analysis import analyze
from lean_compression.environment import doctor
from lean_compression.io import append_event, file_hash, read_json, read_jsonl, write_json
from lean_compression.outcomes import outcome_class
from lean_compression.providers import import_responses
from lean_compression.runner import docker_cli_env, evaluate_proof, evaluate_run
from lean_compression.store import create_run, logged_outages, record_generation, records

try:
    from lean_runtime import manifest_path, pinned_runtime
except ModuleNotFoundError:  # python3 -m unittest tests.test_review_followups, run from the repository root
    from tests.lean_runtime import manifest_path, pinned_runtime

PROJECT = Path(__file__).resolve().parents[1]
ENGINE = PROJECT / "lean/Evaluator.lean"
DEMO_RUN = PROJECT / "examples/pipeline-demo"
MODEL = "pinned-model-2026-01-01"
REAL_POPEN = subprocess.Popen
IMAGE = "sha256:" + "0" * 64
FAKE_ENVIRONMENT = {"environment_hash": "fixture", "lean_binary": "/opt/lean/bin/lean", "toolchain_root": "/opt/lean",
                    "lean_search_paths": ["/opt/lean/lib/lean"]}
TASK = {"task_id": "one", "family_id": "family", "domain": "sets_logic", "split": "pilot", "origin": "fresh",
        "stratum": "provisional_easy", "target_name": "testTarget", "binders": "(P : Prop) (h : P)",
        "proposition": "P", "imports": ["Init"], "premise_context": "", "context": "", "forbidden_declarations": []}
VALID = {"validity_status": "success", "metric_status": "not_requested", "kernel_replay": True,
         "candidate_declarations": ["testTarget"], "diagnostics": {}}
MEASURED = {**VALID, "metric_status": "available", "source_tokens": {"W_S": 1, "W_B": 1}, "root": "testTarget",
            "nodes": [{"name": "p", "primitive": True},
                      {"name": "testTarget", "primitive": False, "signature": {"p": 1}, "body": {"p": 2}}]}
# Extraction results that are checker incidents: (result, status the incident reports).
EXTRACTION_INCIDENTS = [
    ({"validity_status": "infrastructure_error", "metric_status": "unavailable",
      "failure_reason": "container_cleanup_unconfirmed", "error": "docker rm -f timed out"}, "infrastructure_error"),
    ({"validity_status": "infrastructure_error", "metric_status": "unavailable", "process_returncode": 125,
      "error": "Lean did not produce its result record"}, "infrastructure_error"),
    ({"validity_status": "infrastructure_error", "metric_status": "unavailable",
      "error": "[Errno 35] Resource temporarily unavailable"}, "infrastructure_error"),
    ({"validity_status": "evaluator_error", "metric_status": "unavailable", "failure_reason": "uncaught"}, "evaluator_error"),
    ({"validity_status": "environment_error", "metric_status": "unavailable"}, "environment_error"),
    ({"validity_status": "forbidden_environment", "metric_status": "unavailable"}, "forbidden_environment"),
    ({"validity_status": "unexpected_engine_status", "metric_status": "unavailable"}, "unexpected_engine_status"),
    ({"metric_status": "unavailable"}, "infrastructure_error")]
# Extraction results that leave a final valid record without metrics: (result, metric_unavailable_reason).
EXTRACTION_FINAL = [
    ({"validity_status": "extractor_timeout", "metric_status": "unavailable"}, "extractor_timeout"),
    ({"validity_status": "extractor_resource_limit", "metric_status": "unavailable"}, "extractor_resource_limit"),
    ({**VALID, "metric_status": "unavailable", "metric_error": "unknown dependency Foo"}, "unknown dependency Foo"),
    ({"validity_status": "kernel_replay_failure", "metric_status": "unavailable",
      "failure_reason": "kernel replay rejected testTarget"}, "kernel replay rejected testTarget")]


def quiet(argv):
    """cli.main: (exit code or parsed stdout, stderr).

    Most environment records here are synthetic; test_environment_integrity covers the CLI's check of real ones.
    """
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
            patch("lean_compression.cli.load_environment", read_json), patch("lean_compression.environment.verify_environment"):
        try:
            cli.main([str(part) for part in argv])
        except SystemExit as stopped:
            return stopped.code, err.getvalue()
    return json.loads(out.getvalue()), err.getvalue()


def docker_run(path, *, model=MODEL, isolation="docker", samples=4):
    config = read_json(PROJECT / "configs/pilot.json")
    config["validation"].update(isolation=isolation, docker_image=IMAGE)
    config["generation"].update(snapshot_pinned=True, settings_resolved=True)
    config["samples_per_goal_condition"] = samples
    env = {**FAKE_ENVIRONMENT, "lean_toolchain": config["environment"]["lean_toolchain"],
           "mathlib_commit": config["environment"]["mathlib_commit"], "hash_mode": "development_metadata_only"}
    if isolation == "docker":
        # freeze takes a record collected in the image exactly when Lean runs in Docker.
        env.update(runtime_backend="docker", docker_image=IMAGE,
                   source_exclusion_audit={"lean_sources": 0, "git_directories": 0})
    return create_run(path, [TASK], config, env, split="pilot", model=model, development=True)


def analyze_model(manifest, run):
    settings = {**manifest["config"], "analysis": {**manifest["config"]["analysis"], "bootstrap_replicates": 0}}
    return analyze(manifest["tasks"], records(run), {}, settings)["models"][manifest["model_snapshot"]]


class FakeLean:
    """Plays the engine against the job files of the native runner; a validation succeeds.

    The extraction writes `extract` as its result, raises it from the spawn, or writes nothing.
    """

    def __init__(self, extract, exit_code=0):
        self.extract, self.exit_code, self.modes = extract, exit_code, []

    def popen(self, cmd, *args, **kwargs):
        request, result = Path(cmd[-2]), Path(cmd[-1])
        mode = json.loads(request.read_text())["mode"]
        self.modes.append(mode)
        outcome = VALID if mode == "validate" else self.extract
        if isinstance(outcome, BaseException):
            raise outcome
        if outcome is not None:
            result.write_text(json.dumps(outcome))
        return REAL_POPEN(["/bin/sh", "-c", f"exit {self.exit_code if mode == 'extract' else 0}"], *args, **kwargs)

    @contextlib.contextmanager
    def active(self):
        with patch("platform.system", return_value="Darwin"), patch("lean_compression.runner.subprocess.Popen", self.popen):
            yield self


class ExtractionIncidentTests(unittest.TestCase):
    """A checker failure in the extraction process is an incident to retry, as in validation."""

    def setUp(self):
        self.config = read_json(PROJECT / "configs/development.json")

    def evaluate(self, extracted):
        with patch("lean_compression.runner.execute_engine", side_effect=[VALID, extracted]) as engine:
            result = evaluate_proof(TASK, "by exact h", FAKE_ENVIRONMENT, ENGINE, self.config)
        self.assertEqual([call.kwargs["mode"] for call in engine.call_args_list], ["validate", "extract"])
        return result

    def test_checker_failures_in_extraction_are_incidents_that_keep_the_evidence(self):
        for extracted, status in EXTRACTION_INCIDENTS:
            with self.subTest(extracted=extracted):
                result = self.evaluate(extracted)
                self.assertEqual((result["validity_status"], result["failure_phase"]), (status, "extraction"))
                self.assertEqual(outcome_class(result["validity_status"]), "incomplete")
                self.assertEqual(result["validation"], VALID)
                self.assertEqual(result["extraction"], extracted)
                self.assertIsNone(result["metrics"])

    def test_extractor_verdicts_stay_final_and_leave_the_proof_valid(self):
        for extracted, reason in EXTRACTION_FINAL:
            with self.subTest(extracted=extracted):
                result = self.evaluate(extracted)
                self.assertEqual((result["validity_status"], result["metric_status"]), ("success", "unavailable"))
                self.assertEqual(result["metric_unavailable_reason"], reason)
                self.assertNotIn("failure_phase", result)
        result = self.evaluate(MEASURED)
        self.assertEqual((result["validity_status"], result["metric_status"]), ("success", "available"))

    def test_evaluate_leaves_the_slot_pending_and_a_rerun_measures_it(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory) / "run"
            manifest = docker_run(run, model="reference_fixture", isolation="macos_sandbox", samples=1)
            for slot in manifest["slots"]:
                record_generation(run, slot, "by exact h", generation_status="completed")
            outage = FakeLean(OSError(errno.EAGAIN, "Resource temporarily unavailable"))
            with outage.active():
                first = evaluate_run(run, ENGINE)
            self.assertEqual((first["new_evaluations"], first["checker_incidents"]), (0, 3))
            self.assertEqual(outage.modes, ["validate", "extract"] * 3)
            incidents = read_jsonl(run / "evaluations/v1/incidents.jsonl")
            self.assertEqual({(row["validity_status"], row["failure_phase"], row["validation"]["validity_status"])
                              for row in incidents}, {("infrastructure_error", "extraction", "success")})
            self.assertIn("Resource temporarily unavailable", incidents[0]["extraction"]["error"])
            model = analyze_model(manifest, run)
            self.assertFalse(model["run_complete"])
            self.assertEqual({row["validity_status"] for row in records(run)}, {"not_evaluated"})
            # A crashed extractor without a result is the same kind of incident.
            with FakeLean(None, exit_code=1).active():
                second = evaluate_run(run, ENGINE)
            self.assertEqual((second["new_evaluations"], second["checker_incidents"]), (0, 3))
            with FakeLean(MEASURED).active():
                third = evaluate_run(run, ENGINE)
            self.assertEqual((third["new_evaluations"], third["checker_incidents"]), (3, 0))
            model = analyze_model(manifest, run)
            self.assertTrue(model["run_complete"])
            self.assertEqual({c: v["metric_coverage_among_valid"] for c, v in model["conditions"].items()},
                             {"standard": 1, "mathematical_compression": 1, "source_concision": 1})

    def test_reference_and_baseline_checks_leave_the_task_pending_for_a_rerun(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "tasks.jsonl").write_text(json.dumps(TASK) + "\n")
            (root / "refs.jsonl").write_text('{"task_id": "one", "proof": "h"}\n')
            write_json(root / "env.json", FAKE_ENVIRONMENT)
            for command, kind in (("verify-references", "verified_reference"), ("baseline", "trusted_tactic_baseline")):
                with self.subTest(command=command):
                    output = root / command
                    argv = [command, "--tasks", root / "tasks.jsonl", "--environment", root / "env.json",
                            "--config", PROJECT / "configs/development.json", "--engine", ENGINE, "--output", output]
                    if command == "verify-references":
                        argv += ["--references", root / "refs.jsonl"]
                    with FakeLean({"validity_status": "infrastructure_error", "metric_status": "unavailable",
                                   "failure_reason": "container_cleanup_unconfirmed"}).active():
                        result, log = quiet(argv)
                    self.assertEqual((result["new_checks"], result["checker_incidents"], result["checked"]), (0, 1, 0), log)
                    self.assertIn("one: infrastructure_error during extraction (checker incident; task left pending)", log)
                    incident, = read_jsonl(output / "incidents.jsonl")
                    self.assertEqual((incident["task_id"], incident["failure_phase"], incident["extraction"]["failure_reason"]),
                                     ("one", "extraction", "container_cleanup_unconfirmed"))
                    with FakeLean(MEASURED).active():
                        result, log = quiet(argv)
                    self.assertEqual((result["new_checks"], result["verified"], result["measurable"]), (1, 1, 1), log)
                    record, = [read_json(path) for path in output.glob("*.json") if path.name != "policy.json"]
                    self.assertEqual((record["record_kind"], record["metric_status"]), (kind, "available"))


class EngineChangeTests(unittest.TestCase):
    """An engine edited mid-run never stamps its records with the old hash."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.engine = self.root / "engine" / "Evaluator.lean"
        self.engine.parent.mkdir()
        shutil.copyfile(ENGINE, self.engine)
        self.original = self.engine.read_bytes()
        self.config = read_json(PROJECT / "configs/development.json")

    def edit_engine_during(self, edited_mode):
        def engine(request, environment, engine, policy, *, mode, wall_seconds):
            if mode == edited_mode:
                with self.engine.open("a") as stream:
                    stream.write("-- edited mid-run\n")
            return VALID if mode == "validate" else MEASURED
        return patch("lean_compression.runner.execute_engine", side_effect=engine)

    def test_engine_edited_during_verification_is_an_incident(self):
        before = file_hash(self.engine)
        with self.edit_engine_during("validate") as engine:
            result = evaluate_proof(TASK, "by exact h", FAKE_ENVIRONMENT, self.engine, self.config)
        self.assertEqual(engine.call_count, 1)
        self.assertEqual((result["validity_status"], result["failure_reason"], result["engine_hash"]),
                         ("infrastructure_error", "engine_changed_during_verification", before))

    def test_engine_edited_during_extraction_is_an_incident(self):
        before = file_hash(self.engine)
        with self.edit_engine_during("extract") as engine:
            result = evaluate_proof(TASK, "by exact h", FAKE_ENVIRONMENT, self.engine, self.config)
        self.assertEqual(engine.call_count, 2)
        self.assertEqual((result["validity_status"], result["failure_reason"], result["failure_phase"], result["engine_hash"]),
                         ("infrastructure_error", "engine_changed_during_extraction", "extraction", before))
        self.assertIsNone(result["metrics"])

    def test_failed_validation_is_never_extracted(self):
        with patch("lean_compression.runner.execute_engine",
                   return_value={"validity_status": "elaboration_failure", "metric_status": "unavailable"}) as engine:
            result = evaluate_proof(TASK, "by exact h", FAKE_ENVIRONMENT, self.engine, self.config)
        engine.assert_called_once()
        self.assertEqual(engine.call_args.kwargs["mode"], "validate")
        self.assertNotIn("extraction", result)

    def test_evaluate_refuses_a_record_from_another_engine(self):
        run = self.root / "run"
        manifest = docker_run(run)
        record_generation(run, manifest["slots"][0], "by exact h", generation_status="completed")
        other = {"validity_status": "success", "metric_status": "unavailable", "engine_hash": "another-engine",
                 "validation": {"validity_status": "success"}}
        with patch("lean_compression.runner.evaluate_proof", return_value=other):
            with self.assertRaisesRegex(RuntimeError, "Engine changed during this run; create a new evaluation version"):
                evaluate_run(run, self.engine)
        self.assertEqual(sorted(path.name for path in (run / "evaluations/v1").iterdir()), ["policy.json"])

    def test_engine_edited_during_extraction_stops_the_run_and_retries_after_restoring(self):
        run = self.root / "run"
        manifest = docker_run(run, samples=1)
        for slot in manifest["slots"]:
            record_generation(run, slot, "by exact h", generation_status="completed")
        with self.edit_engine_during("extract"):
            with self.assertRaisesRegex(RuntimeError, "Engine changed during this run"):
                evaluate_run(run, self.engine)
        incident, = read_jsonl(run / "evaluations/v1/incidents.jsonl")
        self.assertEqual((incident["failure_reason"], incident["failure_phase"]), ("engine_changed_during_extraction", "extraction"))
        self.assertEqual(sorted(path.name for path in (run / "evaluations/v1").glob("*.json")), ["policy.json"])
        self.engine.write_bytes(self.original)
        with patch("lean_compression.runner.execute_engine", side_effect=lambda *a, mode, **k: VALID if mode == "validate" else MEASURED):
            result = evaluate_run(run, self.engine)
        self.assertEqual((result["new_evaluations"], result["checker_incidents"]), (3, 0))
        self.assertTrue(analyze_model(manifest, run)["run_complete"])


class DockerGateTests(unittest.TestCase):
    """Only trusted reference fixtures may run outside the container."""

    def test_model_output_is_refused_before_anything_is_written(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory) / "run"
            manifest = docker_run(run, isolation="macos_sandbox")
            import_responses(run, [{"attempt_id": slot["attempt_id"], "prompt_hash": slot["prompt_hash"],
                                    "model_snapshot": MODEL, "raw_response": "by exact h"} for slot in manifest["slots"][:1]])
            with patch("lean_compression.runner.evaluate_proof") as check:
                with self.assertRaisesRegex(ValueError, "Model-generated proofs require Docker isolation"):
                    evaluate_run(run, ENGINE)
                code, log = quiet(["evaluate", "--run", run, "--engine", ENGINE])
            check.assert_not_called()
            self.assertEqual(code, 2)
            self.assertIn("Model-generated proofs require Docker isolation", log)
            self.assertFalse((run / "evaluations").exists())

    def test_reference_fixtures_natively_and_model_output_in_docker_are_evaluated(self):
        failure = {"validity_status": "parse_failure", "metric_status": "unavailable", "engine_hash": file_hash(ENGINE)}
        for model, isolation in (("reference_fixture", "macos_sandbox"), (MODEL, "docker")):
            with self.subTest(model=model), tempfile.TemporaryDirectory() as directory:
                run = Path(directory) / "run"
                manifest = docker_run(run, model=model, isolation=isolation)
                record_generation(run, manifest["slots"][0], "by exact h", generation_status="completed")
                with patch("lean_compression.runner.evaluate_proof", return_value=failure) as check:
                    result = evaluate_run(run, ENGINE)
                check.assert_called_once()
                self.assertEqual(result["new_evaluations"], 1)


class HelperCountTests(unittest.TestCase):
    """Statement auxiliaries come from the fixed signature, so they are not proof helpers."""

    def diagnostics(self, validation):
        config = read_json(PROJECT / "configs/development.json")
        with patch("lean_compression.runner.execute_engine",
                   side_effect=[validation, {"validity_status": "extractor_timeout", "metric_status": "unavailable"}]):
            return evaluate_proof(TASK, "by exact h", FAKE_ENVIRONMENT, ENGINE, config)["supporting_diagnostics"]

    def test_reported_statement_auxiliaries_are_not_helpers(self):
        declarations = ["testTarget.match_1", "testTarget._proof_1", "testTarget"]
        current = self.diagnostics({**VALID, "candidate_declarations": declarations,
                                    "statement_auxiliaries": ["testTarget.match_1"]})
        self.assertEqual(current["helper_count"], 1)
        self.assertIn("except statement auxiliaries", current["helper_count_scope"])
        none = self.diagnostics({**VALID, "candidate_declarations": declarations[:1] + declarations[2:],
                                 "statement_auxiliaries": ["testTarget.match_1", "__compression_trusted_signature.match_1"]})
        self.assertEqual(none["helper_count"], 0)

    def test_older_engine_output_keeps_the_previous_count(self):
        legacy = self.diagnostics({**VALID, "candidate_declarations": ["testTarget.match_1", "testTarget"]})
        self.assertEqual(legacy["helper_count"], 1)
        self.assertIn("including any statement auxiliaries", legacy["helper_count_scope"])
        self.assertIsNone(self.diagnostics({"validity_status": "parse_failure"})["helper_count"])


class IncidentVisibilityTests(unittest.TestCase):
    """analyze names the logged checker incidents and provider outages behind pending slots."""

    def test_incidents_and_outages_reach_analysis_json_and_the_html_notice(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = root / "run"
            manifest = docker_run(run)
            slots, engine_hash = manifest["slots"], file_hash(ENGINE)
            for slot in slots[:10]:
                record_generation(run, slot, "by exact h", generation_status="completed")
            transport = run / "transport.jsonl"
            for event in ("request_started", "http_error", "no_model_outcome"):
                append_event(transport, {"attempt_id": slots[10]["attempt_id"], "event": event})
            append_event(transport, {"attempt_id": slots[11]["attempt_id"], "event": "outcome_unknown"})
            write_json(run / "inflight" / f"{slots[11]['attempt_id']}.json", {"attempt_id": slots[11]["attempt_id"]})
            daemon = {"validity_status": "infrastructure_error", "metric_status": "unavailable", "engine_hash": engine_hash,
                      "validation": {"validity_status": "infrastructure_error",
                                     "error": "Cannot connect to the Docker daemon\nIs the docker daemon running?"}}
            extraction = {"validity_status": "evaluator_error", "metric_status": "unavailable", "engine_hash": engine_hash,
                          "failure_phase": "extraction", "validation": {"validity_status": "success"},
                          "extraction": {"validity_status": "evaluator_error", "failure_reason": "uncaught exception"}}
            success = {"validity_status": "success", "metric_status": "unavailable", "engine_hash": engine_hash,
                       "validation": {"validity_status": "success"}}
            with patch("lean_compression.runner.evaluate_proof", side_effect=[daemon] * 3 + [extraction] + [success] * 6):
                self.assertEqual(evaluate_run(run, ENGINE)["checker_incidents"], 4)
            with (run / "evaluations/v1/incidents.jsonl").open("a") as log:
                log.write("{not json\n")
            code, log = quiet(["analyze", "--run", run, "--output", root / "first"])
            self.assertIsInstance(code, dict, log)
            provenance = read_json(root / "first/analysis.json")["run_provenance"]
            self.assertEqual(provenance["checker_incidents"], {
                "log": "evaluations/v1/incidents.jsonl", "events": 4, "unreadable_lines": 1, "attempts": 4,
                "unresolved_attempts": 4, "unresolved_statuses": {"evaluator_error": 1, "infrastructure_error": 3},
                "unresolved_phases": {"extraction": 1, "validation": 3},
                "unresolved_reasons": {"Cannot connect to the Docker daemon": 3, "uncaught exception": 1}})
            self.assertEqual(provenance["provider_outages"], {
                "log": "transport.jsonl", "no_model_outcome_events": 1, "outcome_unknown_events": 1,
                "unreadable_lines": 0, "unresolved_attempts": 2, "prompt_rejected_attempts": 0, "in_flight_markers": 1})
            page = (root / "first/report.html").read_text(encoding="utf-8")
            self.assertIn("Attempts with unresolved checker incidents: 4 (evaluator_error: 1, infrastructure_error: 3; "
                          "phases: extraction: 1, validation: 3), logged in <code>evaluations/v1/incidents.jsonl</code>", page)
            self.assertIn("Attempts whose provider calls have no recorded model outcome: 2, logged in "
                          "<code>transport.jsonl</code>", page)
            # Retried incidents stay in the log but are no longer unresolved; the outages still are.
            with patch("lean_compression.runner.evaluate_proof", return_value=success):
                self.assertEqual(evaluate_run(run, ENGINE)["new_evaluations"], 4)
            summary = logged_outages(run, "v1")
            self.assertEqual({key: summary["checker_incidents"][key] for key in ("events", "attempts", "unresolved_attempts")},
                             {"events": 4, "attempts": 4, "unresolved_attempts": 0})
            self.assertEqual(summary["provider_outages"]["unresolved_attempts"], 2)
            code, log = quiet(["analyze", "--run", run, "--output", root / "second"])
            page = (root / "second/report.html").read_text(encoding="utf-8")
            self.assertNotIn("unresolved checker incidents", page)
            self.assertIn("Attempts whose provider calls have no recorded model outcome: 2", page)

    def test_runs_without_logs_report_zero_counts(self):
        summary = logged_outages(DEMO_RUN, "v2")
        self.assertEqual((summary["checker_incidents"]["events"], summary["checker_incidents"]["unresolved_attempts"],
                          summary["provider_outages"]["unresolved_attempts"], summary["provider_outages"]["in_flight_markers"]),
                         (0, 0, 0, 0))
        self.assertEqual(summary["checker_incidents"]["log"], "evaluations/v2/incidents.jsonl")


class DoctorTests(unittest.TestCase):
    """doctor queries the daemon that runs candidates, through the same client environment."""

    def which(self, name):
        return "/opt/docker/bin/docker" if name == "docker" else None

    def test_doctor_uses_the_runner_client_environment(self):
        calls = []
        def run(cmd, **kwargs):
            calls.append((cmd, kwargs))
            return SimpleNamespace(returncode=0)
        parent = {"PATH": "/usr/bin:/bin", "DOCKER_HOST": "unix:///tmp/unit-docker.sock", "DOCKER_CONTEXT": "unit",
                  "OPENAI_API_KEY": "sk-unit-test-canary", "AWS_SECRET_ACCESS_KEY": "aws-unit-test-canary"}
        with patch.dict(os.environ, parent, clear=True), \
             patch("lean_compression.environment.shutil.which", side_effect=self.which), \
             patch("lean_compression.environment.subprocess.run", side_effect=run):
            report = doctor()
            expected = docker_cli_env()
        (cmd, kwargs), = calls
        self.assertEqual(cmd[:2], ["/opt/docker/bin/docker", "info"])
        self.assertEqual(kwargs["env"], expected)
        self.assertEqual(kwargs["env"]["DOCKER_HOST"], "unix:///tmp/unit-docker.sock")
        self.assertNotIn("canary", json.dumps(kwargs["env"]))
        self.assertTrue(report["docker_running"])
        self.assertIs(report["openai_api_key_configured"], True)
        self.assertNotIn("canary", json.dumps(report))

    def test_an_unlaunchable_or_hung_client_means_not_running(self):
        for error in (PermissionError(13, "Permission denied"), OSError(8, "Exec format error"),
                      subprocess.TimeoutExpired(["docker", "info"], 5)):
            with self.subTest(error=type(error).__name__), \
                 patch("lean_compression.environment.shutil.which", side_effect=self.which), \
                 patch("lean_compression.environment.subprocess.run", side_effect=error):
                report = doctor()
            self.assertEqual((report["docker_installed"], report["docker_running"]), (True, False))


READINESS_CHILD = r"""
import locale, sys
from pathlib import Path
from lean_compression.readiness import build_readiness_report, write_readiness_report
print(locale.getpreferredencoding(False), flush=True)
task = {"task_id": "ensembles_\u2205_01", "family_id": "famille_\u00e9", "domain": "sets_logic", "split": "pilot",
        "origin": "fresh", "stratum": "provisional_easy", "imports": ["Init"], "context": "", "premise_context": "",
        "forbidden_declarations": [], "target_name": "target_empty", "binders": "(P : Prop) (h : P)", "proposition": "P"}
for _ in range(2):
    write_readiness_report(build_readiness_report([task], [], {"enforce_quotas": False}), Path(sys.argv[1]))
"""


class ReadinessEncodingTests(unittest.TestCase):
    def test_readiness_report_is_written_and_rewritten_as_utf8_under_an_ascii_locale(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "readiness"
            env = {"PATH": "/usr/bin:/bin", "LC_ALL": "C", "LANG": "C", "PYTHONUTF8": "0", "PYTHONCOERCECLOCALE": "0",
                   "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(Path(cli.__file__).resolve().parents[1])}
            child = subprocess.run([sys.executable, "-c", READINESS_CHILD, str(out)], env=env, capture_output=True, timeout=120)
            encoding = child.stdout.decode("ascii", "replace").split("\n")[0]
            if encoding.replace("-", "").upper() == "UTF8":
                self.skipTest("This interpreter uses UTF-8 regardless of the C locale")
            self.assertEqual(child.returncode, 0, child.stderr.decode("utf-8", "replace"))
            self.assertIn("ensembles_∅_01", (out / "readiness.html").read_bytes().decode("utf-8"))
            self.assertIn("ensembles_∅_01", (out / "readiness.json").read_bytes().decode("utf-8"))


class LeanExtractionIncidentTests(unittest.TestCase):
    """Real Lean under the development sandbox; only the extraction spawn fails."""

    @classmethod
    def setUpClass(cls):
        if platform.system() != "Darwin":
            raise unittest.SkipTest("Needs the macOS development sandbox")
        cls.environment, _ = pinned_runtime()

    def test_reference_extraction_outage_is_retried_and_then_measured(self):
        class ExtractionCannotFork(REAL_POPEN):
            def __init__(self, cmd, *args, **kwargs):
                if json.loads(Path(cmd[-2]).read_text())["mode"] == "extract":
                    raise OSError(errno.EAGAIN, "Resource temporarily unavailable")
                super().__init__(cmd, *args, **kwargs)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task = {**TASK, "task_id": "tiny_01", "family_id": "fam_a", "target_name": "tiny_target_01", "proposition": "P ∧ P"}
            (root / "tasks.jsonl").write_text(json.dumps(task) + "\n")
            (root / "refs.jsonl").write_text('{"task_id": "tiny_01", "proof": "⟨h, h⟩"}\n', encoding="utf-8")
            argv = ["verify-references", "--tasks", root / "tasks.jsonl", "--references", root / "refs.jsonl",
                    "--environment", manifest_path(), "--config", PROJECT / "configs/development.json", "--engine", ENGINE,
                    "--output", root / "checks"]
            with patch("lean_compression.runner.subprocess.Popen", ExtractionCannotFork):
                result, log = quiet(argv)
            self.assertEqual((result["new_checks"], result["checker_incidents"]), (0, 1), log)
            incident, = read_jsonl(root / "checks/incidents.jsonl")
            self.assertEqual((incident["validation"]["validity_status"], incident["validation"]["kernel_replay"]), ("success", True))
            self.assertEqual((incident["validity_status"], incident["failure_phase"]), ("infrastructure_error", "extraction"))
            result, log = quiet(argv)
            self.assertEqual((result["new_checks"], result["verified"], result["measurable"]), (1, 1, 1), log)

    def test_statement_matcher_is_not_a_proof_helper(self):
        task = {**TASK, "target_name": "evaluation_target", "binders": "(n : Nat)",
                "proposition": "(match n with | 0 => 0 | k + 1 => k) ≤ n"}
        result = evaluate_proof(task, "by cases n <;> simp", self.environment, ENGINE, read_json(PROJECT / "configs/development.json"))
        self.assertEqual(result["validity_status"], "success", result.get("validation", {}).get("failure_reason"))
        self.assertIn("evaluation_target.match_1", result["validation"]["candidate_declarations"])
        reported = "statement_auxiliaries" in result["validation"]
        self.assertEqual(result["supporting_diagnostics"]["helper_count"], 0 if reported else 1)


if __name__ == "__main__":
    unittest.main()
