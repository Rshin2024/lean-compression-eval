"""Round 3 regressions: partially re-anchored versions, withheld metrics, interrupted container attempts, the
extraction incident documentation, incidents inherited by rescored versions, log line parsing, the
prompt-rejection breaker, import nesting, and the replay override bound.

Lean, the Docker client and the provider are fakes, except in the last class, which runs the pinned local
Lean runtime and skips without it. No network, model or Docker daemon is used.
"""
from concurrent.futures import ThreadPoolExecutor
import contextlib
import io
import json
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))  # sibling fixtures, also when run as tests.test_round3

import test_docker_command as docker_fixtures
import test_lean_integration as lean_fixtures
import test_provider_followups as provider_fixtures
import test_review_followups as followups
import test_review_followups_provenance as provenance

from lean_compression import cli
from lean_compression.io import append_event, canonical, digest, file_hash, read_json, read_jsonl, write_json
from lean_compression.providers import import_responses, nesting_depth
from lean_compression.runner import docker_cli_env, evaluate_run, execute_engine
from lean_compression.store import _log_events, create_run, logged_outages, record_generation, records

PROJECT = Path(__file__).resolve().parents[1]
REAL_POPEN = subprocess.Popen
FLAGGED = provider_fixtures.FLAGGED


def record_of(directory, task_id):
    return next(path for path in Path(directory).glob("*.json") if read_json(path).get("task_id") == task_id)


def withheld(record):
    """What older evaluate_proof wrote when the engine file changed during extraction: the graph, no metrics."""
    return {**{key: value for key, value in record.items() if key not in ("graph_hash", "source_token_policy_hash")},
            "metric_status": "unavailable", "metrics": None, "metric_unavailable_reason": "engine_changed_during_extraction"}


class PartialRecheckBindingTests(provenance.Pipeline):
    """R2-binding-remedy-publish-1: a frozen proof the changed engine rejects unanchors only its own task."""

    def setUp(self):
        super().setUp()
        self.verify("checks")
        self.assertEqual(self.freeze()[0], 0)
        self.generate()
        self.assertEqual(self.evaluate("v1")[0], 0)

    def amendments(self):
        path = self.root / "run/amendments.jsonl"
        return read_jsonl(path) if path.exists() else []

    def test_a_rejected_frozen_proof_is_bound_unanchored_and_analyzed_as_missing(self):
        self.verify("rechecks", engine="E2", failures=("tiny_02",))
        code, result, log, checks = self.evaluate("v3", engine="E2", checks="rechecks")
        self.assertEqual((code, checks), (0, 6), log)
        self.assertEqual(result, {"new_evaluations": 6, "evaluation_version": "v3", "checker_incidents": 0,
                                  "unanchored_reference_tasks": ["tiny_02"]})
        digests = self.anchors("rechecks")
        anchors = {"tiny_01": digests["tiny_01"]}
        verdict = {"tiny_02": {"validity_status": "elaboration_failure", "metric_status": "unavailable",
                               "record_digest": digests["tiny_02"]}}
        policy = read_json(self.root / "run/evaluations/v3/policy.json")
        self.assertEqual((policy["reference_anchors"], policy["unanchored_reference_tasks"]), (anchors, verdict))
        event, = self.amendments()
        self.assertEqual((event["kind"], event["version"], event["reference_anchors"], event["unanchored_reference_tasks"]),
                         ("reference_recheck", "v3", anchors, verdict))
        # Resuming keeps the binding and logs no second amendment.
        self.assertEqual(self.evaluate("v3", engine="E2", checks="rechecks")[1]["unanchored_reference_tasks"], ["tiny_02"])
        self.assertEqual(len(self.amendments()), 1)
        # The rescored copy keeps the binding; every analysis of it counts the task's reference as missing.
        self.assertEqual(provenance.run_cli("rescore", "--run", self.root / "run", "--from-version", "v3", "--version", "v4")[0], 0)
        self.assertEqual(read_json(self.root / "run/evaluations/v4/policy.json")["unanchored_reference_tasks"], verdict)
        for version in ("v3", "v4"):
            with self.subTest(version=version):
                code, report, log = self.analyze(version, "rechecks", f"{version}-report")
                self.assertEqual(code, 0, log)
                run = report["run_provenance"]
                self.assertEqual((run["reference_anchor_binding"], run["reference_anchor_digests"],
                                  run["unanchored_reference_tasks"], report["missing_reference_task_ids"]),
                                 ("amended_per_task", anchors, verdict, ["tiny_02"]))
                model = report["models"]["reference_fixture"]
                attainment = model["conditions"]["standard"]["attainment"]
                self.assertEqual({t: (attainment[t]["rate"], attainment[t]["reason"]) for t in "012"},
                                 dict.fromkeys("012", (None, "missing_reference_metrics")))
                self.assertFalse(model["compression_conclusions_ready"])
                self.assertEqual(model["conditions"]["standard"]["success_rate"], 1)
        code, report, log = self.analyze("v3", None, "v3-plain")
        self.assertEqual((code, report["run_provenance"]["unanchored_reference_tasks"]), (0, verdict), log)
        # Other versions keep every anchor frozen with the run.
        code, report, log = self.analyze("v1", "checks", "v1-report")
        self.assertEqual(code, 0, log)
        self.assertEqual((report["run_provenance"]["unanchored_reference_tasks"], report["missing_reference_task_ids"],
                          self.attainment(report)), ({}, [], [1, 0, 0]))

    def test_a_task_without_a_recheck_record_still_refuses_with_rerun_advice(self):
        self.verify("rechecks", engine="E2", incidents=("tiny_01",), failures=("tiny_02",))
        code, result, log, checks = self.evaluate("v3", engine="E2", checks="rechecks")
        self.assertEqual((code, checks), (2, 0))
        self.assertIn("Missing verified reference: tiny_01 (no record). Finish verify-references first: every task needs "
                      "a verified record, so rerun it until it reports no checker incidents", log)
        self.assertNotIn("tiny_02", log)
        self.assertFalse((self.root / "run/evaluations/v3").exists())
        self.assertEqual(self.amendments(), [])
        # The rerun the message asks for completes the directory; the rejected proof stays unanchored.
        self.assertEqual(self.verify("rechecks", engine="E2", failures=("tiny_02",))["checker_incidents"], 0)
        code, result, log, checks = self.evaluate("v3", engine="E2", checks="rechecks")
        self.assertEqual((code, result["unanchored_reference_tasks"]), (0, ["tiny_02"]), log)

    def test_only_the_engines_own_verdict_unanchors_a_task(self):
        self.verify("rechecks", engine="E2", failures=("tiny_02",))
        for name, change, fields in (
                ("legacy-incident", {"validity_status": "infrastructure_error"}, "record_kind, validity_status"),
                ("other-environment", {"environment_hash": "other"}, "record_kind, validity_status, environment_hash"),
                ("other-task", dict.fromkeys(("verification_request_hash", "task_input_hash", "task_hash"), "other"),
                 "record_kind, validity_status, task identity")):
            with self.subTest(name):
                shutil.copytree(self.root / "rechecks", self.root / name)
                path = record_of(self.root / name, "tiny_02")
                write_json(path, {**read_json(path), **change}, immutable=False)
                code, result, log, checks = self.evaluate("v3", engine="E2", checks=name)
                self.assertEqual((code, checks), (2, 0))
                self.assertIn(f"Incompatible verified reference: tiny_02 ({fields}). Rerunning verify-references keeps "
                              "these records", log)
                self.assertFalse((self.root / "run/evaluations/v3").exists())
        self.assertEqual(self.amendments(), [])

    def test_checks_from_another_engine_are_named_as_such_before_any_rerun_advice(self):
        self.verify("other-engine", incidents=("tiny_01",), failures=("tiny_02",))
        code, result, log, checks = self.evaluate("v3", engine="E2", checks="other-engine")
        self.assertEqual((code, checks), (2, 0))
        self.assertIn("Re-checked references must come from the engine being evaluated", log)
        self.assertNotIn("rerun", log)

    def test_analyze_checks_the_unanchored_record_and_never_uses_it(self):
        self.verify("rechecks", engine="E2", failures=("tiny_02",))
        self.assertEqual(self.evaluate("v3", engine="E2", checks="rechecks")[0], 0)
        shutil.copytree(self.root / "rechecks", self.root / "edited")
        path = record_of(self.root / "edited", "tiny_02")
        write_json(path, {**read_json(path), "note": "edited"}, immutable=False)
        code, report, log = self.analyze("v3", "edited", "edited-report")
        self.assertEqual(code, 2)
        self.assertIn("Reference record for tiny_02 is not the one bound to this evaluation version", log)

    def test_a_development_recheck_verified_but_unmeasurable_still_binds_as_at_freeze(self):
        self.verify("rechecks", engine="E2", unmeasurable=("tiny_02",))
        code, result, log, checks = self.evaluate("v3", engine="E2", checks="rechecks")
        self.assertEqual(code, 0, log)
        self.assertNotIn("unanchored_reference_tasks", result)
        policy = read_json(self.root / "run/evaluations/v3/policy.json")
        self.assertEqual((policy["reference_anchors"], policy["unanchored_reference_tasks"]), (self.anchors("rechecks"), {}))


class FreezeRefusalMessageTests(provenance.Pipeline):
    """R2-binding-remedy-publish-1: freeze still refuses an incomplete set, saying what a rerun can and cannot fix."""

    def test_failed_records_are_named_apart_from_pending_ones(self):
        self.verify("failed", failures=("tiny_02",))
        code, result, log = self.freeze("failed", run="run-failed")
        self.assertEqual(code, 2)
        self.assertIn("Incompatible verified reference: tiny_02 (record_kind, validity_status). Rerunning verify-references "
                      "keeps these records: correct or leave out a reference proof that failed or cannot be measured, and "
                      "check the references into a new directory", log)
        self.assertNotIn("checker incidents", log)
        self.verify("both", incidents=("tiny_01",), failures=("tiny_02",))
        code, result, log = self.freeze("both", run="run-both")
        self.assertEqual(code, 2)
        self.assertIn("Missing verified reference: tiny_01 (no record). Finish verify-references first", log)
        self.assertIn("Incompatible verified reference: tiny_02 (record_kind, validity_status)", log)
        self.assertFalse((self.root / "run-failed").exists() or (self.root / "run-both").exists())


class WithheldMetricsTests(provenance.Pipeline):
    """R2-binding-remedy-publish-2: rescoring never turns metrics the evaluator withheld into measurements."""

    def test_rescored_keeps_withheld_metrics(self):
        record = withheld({"validity_status": "success", "extraction": provenance.GRAPH})
        self.assertIs(cli.rescored(record), record)
        bare = {key: value for key, value in record.items() if key != "metric_unavailable_reason"}
        self.assertIs(cli.rescored(bare), bare)
        # Metrics older code scored as unavailable are still recomputed.
        older = {"validity_status": "success", "metric_status": "unavailable", "extraction": provenance.GRAPH,
                 "metrics": {"metric_status": "unavailable", "scorer_hash": "older-scorer"}}
        self.assertEqual(cli.rescored(older)["metric_status"], "available")

    def test_a_withheld_frozen_anchor_stays_missing_when_anchors_are_rescored(self):
        self.verify("checks")
        shutil.copytree(self.root / "checks", self.root / "legacy")
        path = record_of(self.root / "legacy", "tiny_01")
        write_json(path, withheld(read_json(path)), immutable=False)
        # A development freeze binds a verified anchor without metrics.
        self.assertEqual(self.freeze("legacy")[0], 0)
        self.generate()
        self.assertEqual(self.evaluate("v1")[0], 0)
        # Rescoring the attempts makes analyze rescore the anchors other code scored.
        self.assertEqual(provenance.run_cli("rescore", "--run", self.root / "run", "--from-version", "v1", "--version", "v2")[0], 0)
        code, report, log = self.analyze("v2", "legacy", "report")
        self.assertEqual(code, 0, log)
        run = report["run_provenance"]
        self.assertEqual((report["missing_reference_task_ids"], run["reference_metrics_recomputed"],
                          sorted(run["reference_anchor_digests"])), (["tiny_01"], ["tiny_02"], ["tiny_02"]))
        self.assertFalse(report["models"]["reference_fixture"]["compression_conclusions_ready"])

    def test_rescore_keeps_an_attempt_with_withheld_metrics(self):
        self.verify("checks")
        self.assertEqual(self.freeze()[0], 0)
        self.generate()
        self.assertEqual(self.evaluate("v1")[0], 0)
        first, *others = sorted(path for path in (self.root / "run/evaluations/v1").glob("*.json") if path.name != "policy.json")
        write_json(first, withheld(read_json(first)), immutable=False)
        self.assertEqual(provenance.run_cli("rescore", "--run", self.root / "run", "--from-version", "v1", "--version", "v2")[0], 0)
        copy = read_json(self.root / "run/evaluations/v2" / first.name)
        self.assertEqual((copy["metric_status"], copy["metrics"], copy["metric_unavailable_reason"]),
                         ("unavailable", None, "engine_changed_during_extraction"))
        self.assertEqual({read_json(self.root / "run/evaluations/v2" / path.name)["metric_status"] for path in others},
                         {"available"})


class InterruptedClient(REAL_POPEN):
    """A client that hangs until the harness is interrupted while waiting for it, as by Ctrl-C."""

    def wait(self, timeout=None):
        if timeout is not None and not getattr(self, "interrupted", False):
            self.interrupted = True
            time.sleep(0.2)
            raise KeyboardInterrupt
        return super().wait(timeout)


class InterruptedAttemptTests(unittest.TestCase):
    """R2-incidents-docker-3: an interrupted container attempt removes the container before its bind source."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.temp = Path(temp.name).resolve()
        self.scratch_root = self.temp / "scratch"
        self.scratch_root.mkdir()
        tempdir = patch.object(tempfile, "tempdir", str(self.scratch_root))
        tempdir.start()
        self.addCleanup(tempdir.stop)
        self.engine = self.temp / "engines" / "Evaluator.lean"
        self.engine.parent.mkdir()
        self.engine.write_text("-- engine\n")

    def interrupt(self, isolation="docker", rm_exit=(0, "")):
        """Interrupt an attempt while its client runs; return the error and what docker rm -f found."""
        self.calls, self.clients, self.at_removal = [], [], []

        def popen(cmd, *args, **kwargs):
            self.calls.append({"cmd": list(cmd), "env": kwargs.get("env")})
            if cmd[:3] == ["docker", "rm", "-f"]:
                self.at_removal.append({"bind_source": [path.is_dir() for path in self.scratch_root.glob("*/job")],
                                        "client_running": [client.poll() is None for client in self.clients]})
                code, message = rm_exit
                return REAL_POPEN(["/bin/sh", "-c", 'printf "%s\\n" "$1" >&2; exit "$2"', "docker", message, str(code)],
                                  *args, **kwargs)
            self.clients.append(InterruptedClient(["/bin/sh", "-c", "exec sleep 60"], *args, **kwargs))
            return self.clients[-1]
        policy = {"isolation": isolation, "docker_image": docker_fixtures.IMAGE, "memory_mib": 1024, "cpu_quota": 0.5}
        with patch("subprocess.Popen", popen), patch("platform.system", return_value="Darwin"):
            with self.assertRaises(KeyboardInterrupt) as raised:
                execute_engine(docker_fixtures.REQUEST, docker_fixtures.ENVIRONMENT, self.engine, policy,
                               mode="validate", wall_seconds=30)
        client, = self.clients
        self.assertEqual(client.returncode, -signal.SIGKILL, "the client's own session never saw the interrupt")
        return raised.exception

    def test_the_container_is_removed_before_its_scratch_directory(self):
        error = self.interrupt()
        run, removal = (call["cmd"] for call in self.calls)
        self.assertEqual(run[:2], ["docker", "run"])
        self.assertEqual(removal, ["docker", "rm", "-f", run[4]])
        self.assertEqual(self.calls[1]["env"], docker_cli_env())
        self.assertEqual(self.at_removal, [{"bind_source": [True], "client_running": [False]}])
        self.assertEqual(list(self.scratch_root.iterdir()), [])
        self.assertFalse(getattr(error, "__notes__", None))

    def test_an_unconfirmed_removal_keeps_the_bind_source_and_says_so(self):
        message = "Error response from daemon: could not kill container"
        error = self.interrupt(rm_exit=(1, message))
        scratch, = self.scratch_root.iterdir()
        self.assertTrue((scratch / "job" / "request.json").is_file())
        name = self.calls[0]["cmd"][4]
        self.assertEqual(error.__notes__, [f"Kept scratch directory {scratch}: removal of container {name} unconfirmed: "
                                           f"docker rm -f exited 1: {message}"])

    def test_an_interrupted_native_attempt_stops_lean_and_removes_its_scratch(self):
        self.interrupt(isolation="macos_sandbox")
        self.assertEqual([call["cmd"][0] for call in self.calls], ["/usr/bin/sandbox-exec"])
        self.assertEqual(list(self.scratch_root.iterdir()), [])


class ExtractionIncidentDocumentationTests(unittest.TestCase):
    """R2-incidents-docker-4: the engine guide describes checker failures of the extraction process as incidents."""

    def test_lean_engine_guide_matches_the_runner(self):
        text = " ".join((PROJECT / "docs/lean-engine.md").read_text(encoding="utf-8").split())
        self.assertNotIn("If the separate extraction process times out or fails, the runner leaves compression unavailable", text)
        self.assertIn("an extraction timeout or resource limit, a graph error, or an engine verdict reported in extract mode "
                      "leaves compression unavailable while retaining correctness success", text)
        self.assertIn("it is logged as a checker incident, no record is written, and the slot or reference stays pending "
                      "until a rerun measures it", text)


class InheritedIncidentTests(unittest.TestCase):
    """R2-incidents-docker-5: a rescored version reports the incidents still pending from its source version."""

    def test_rescored_versions_report_and_explain_the_source_incidents(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = root / "run"
            manifest = followups.docker_run(run, samples=1)
            for slot in manifest["slots"]:
                record_generation(run, slot, "by exact h", generation_status="completed")
            engine_hash = file_hash(followups.ENGINE)
            daemon = {"validity_status": "infrastructure_error", "metric_status": "unavailable", "engine_hash": engine_hash,
                      "validation": {"validity_status": "infrastructure_error", "error": "Cannot connect to the Docker daemon"}}
            final = {"validity_status": "success", "metric_status": "unavailable", "metrics": None, "engine_hash": engine_hash,
                     "validation": {"validity_status": "success"}, "metric_unavailable_reason": "extractor_timeout"}
            with patch("lean_compression.runner.evaluate_proof", side_effect=[daemon, final, final]):
                self.assertEqual(evaluate_run(run, followups.ENGINE)["checker_incidents"], 1)
            for source, target in (("v1", "v2"), ("v2", "v3")):
                self.assertIsInstance(followups.quiet(["rescore", "--run", run, "--from-version", source, "--version", target])[0], dict)
            result, log = followups.quiet(["analyze", "--run", run, "--version", "v3", "--output", root / "v3"])
            self.assertIsInstance(result, dict, log)
            outages = read_json(root / "v3/analysis.json")["run_provenance"]
            self.assertEqual(outages["checker_incidents"]["unresolved_attempts"], 0)
            self.assertEqual(outages["inherited_checker_incidents"], {
                "source_version": "v1", "log": "evaluations/v1/incidents.jsonl", "events": 1, "unreadable_lines": 0,
                "attempts": 1, "unresolved_attempts": 1, "unresolved_statuses": {"infrastructure_error": 1},
                "unresolved_phases": {"validation": 1}, "unresolved_reasons": {"Cannot connect to the Docker daemon": 1}})
            page = (root / "v3/report.html").read_text(encoding="utf-8")
            self.assertIn("Attempts still pending after checker incidents in source version <code>v1</code>: 1 "
                          "(infrastructure_error: 1; phases: validation: 1), logged in <code>evaluations/v1/incidents.jsonl"
                          "</code>; rescore copies only records, so rerun evaluate on that version and rescore it into a "
                          "new version.", page)
            self.assertIsNone(logged_outages(run, "v1")["inherited_checker_incidents"])
            # Retrying the source resolves it there; the rescored copies still lack the record until a new rescore.
            with patch("lean_compression.runner.evaluate_proof", return_value=final):
                self.assertEqual(evaluate_run(run, followups.ENGINE)["new_evaluations"], 1)
            self.assertEqual(logged_outages(run, "v1")["checker_incidents"]["unresolved_attempts"], 0)
            self.assertEqual(logged_outages(run, "v3")["inherited_checker_incidents"]["unresolved_attempts"], 1)
            self.assertIsInstance(followups.quiet(["rescore", "--run", run, "--from-version", "v1", "--version", "v4"])[0], dict)
            self.assertEqual(logged_outages(run, "v4")["inherited_checker_incidents"]["unresolved_attempts"], 0)
            self.assertEqual({row["validity_status"] for row in records(run, "v4")}, {"success"})


class LogLineTests(unittest.TestCase):
    """R2-incidents-docker-2: events end only at a newline, and a torn line never swallows a later event."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def test_unicode_line_breaks_stay_inside_their_event(self):
        log = self.root / "incidents.jsonl"
        texts = ["by exact h --  ", "by exact h", "by exact h\u0085", "\x1c\x1d\x1e\x0b\x0c\r"]
        for index, text in enumerate(texts):
            append_event(log, {"attempt_id": f"a{index}", "adapted_response": text})
        self.assertEqual(log.read_bytes().count(b"\n"), len(texts))
        events, unreadable = _log_events(log)
        self.assertEqual(([event["adapted_response"] for event in events], unreadable), (texts, 0))

    def test_an_event_appended_after_a_torn_line_is_kept(self):
        log = self.root / "incidents.jsonl"
        append_event(log, {"attempt_id": "a1"})
        self.assertTrue(log.read_bytes().startswith(b"{"))
        with log.open("ab") as out:
            out.write(canonical({"attempt_id": "a2", "stderr": "x" * 9000})[:5000])  # a writer killed mid-line
        append_event(log, {"attempt_id": "a3"})
        append_event(log, {"attempt_id": "a4"})
        events, unreadable = _log_events(log)
        self.assertEqual(([event["attempt_id"] for event in events], unreadable), (["a1", "a3", "a4"], 1))
        self.assertEqual(log.read_bytes().count(b"\n"), 4)

    def test_every_incident_reaches_the_summary_and_the_notice(self):
        run = self.root / "run"
        manifest = followups.docker_run(run, samples=1)
        for slot, raw in zip(manifest["slots"], ("by exact h --  ", "by exact h", "by exact h -- \u0085")):
            record_generation(run, slot, raw, generation_status="completed")
        outage = {"validity_status": "infrastructure_error", "metric_status": "unavailable",
                  "error": "Cannot connect to the Docker daemon", "resources": {"isolation": "docker"}}
        with patch("lean_compression.runner.execute_engine", return_value=outage):
            self.assertEqual(evaluate_run(run, followups.ENGINE)["checker_incidents"], 3)
        with (run / "evaluations/v1/incidents.jsonl").open("ab") as out:
            out.write(b'{"attempt_id": "torn')
        summary = logged_outages(run, "v1")["checker_incidents"]
        self.assertEqual({key: summary[key] for key in ("events", "unreadable_lines", "attempts", "unresolved_attempts")},
                         {"events": 3, "unreadable_lines": 1, "attempts": 3, "unresolved_attempts": 3})
        result, log = followups.quiet(["analyze", "--run", run, "--output", self.root / "report"])
        self.assertIsInstance(result, dict, log)
        page = (self.root / "report/report.html").read_text(encoding="utf-8")
        self.assertIn("Attempts with unresolved checker incidents: 3 (infrastructure_error: 3; phases: validation: 3)", page)
        self.assertIn("Unreadable log lines, whose events are not counted here: 1.", page)


class PromptRejectionBreakerTests(provider_fixtures.RunFixture):
    """R2-providers-1: rejected prompts use up --limit, and a run of them stops the command."""

    def reject(self, request=None):
        raise provider_fixtures.http_error(400, FLAGGED)

    def test_a_rejection_of_every_prompt_stops_after_three_requests(self):
        for label, rejection in (("HTTP 400", self.reject),
                                 ("failed response", lambda request: provider_fixtures.reply("failed", error={"code": "invalid_prompt"}))):
            with self.subTest(label):
                outcome = self.generate(rejection)
                self.assertIsInstance(outcome, RuntimeError)
                # Round 4: the breaker counts different prompts, which this schedule rejects one after another.
                self.assertIn("Provider rejected 3 different prompts in a row as invalid_prompt", str(outcome))
                self.assertIn("Their slots stay pending", str(outcome))
                self.assertEqual((len(self.requests), self.generations(), self.markers()), (3, [], []))
        self.assertEqual([event["prompt_rejected"] for event in self.outcomes()], [True] * 6)
        self.assertEqual({row["validity_status"] for row in records(self.run)}, {"not_generated"})

    def test_the_cli_exits_nonzero_with_the_reason(self):
        stderr = io.StringIO()
        with self.provider(self.reject), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as stopped:
                cli.main(["generate", "--run", str(self.run)])
        self.assertEqual((stopped.exception.code, len(self.requests)), (2, 3))
        self.assertIn("Provider rejected 3 different prompts in a row as invalid_prompt", stderr.getvalue())  # round 4

    def test_rejections_use_up_the_limit(self):
        asked = []
        for limit in (1, 2):
            with self.subTest(limit=limit):
                outcome = self.generate(self.reject, limit=limit)
                # Round 4 adds skipped_rejected_prompt_slots, which never use up the limit.
                self.assertEqual({key: outcome[key] for key in ("new_generation_records", "prompt_rejections", "scheduled_attempts")},
                                 {"new_generation_records": 0, "prompt_rejections": limit, "scheduled_attempts": 24})
                self.assertEqual(len(self.requests), limit)
                asked += [digest(json.loads(request.data)["input"]) for request in self.requests]
        self.assertEqual(len(set(asked)), 3)  # the second command passed over the prompt the first one had rejected

    def test_only_consecutive_rejections_stop_the_command(self):
        # Two rejections, then an answer, until all six prompts are rejected: never three in a row. Round 4 sends a
        # rejected prompt's other slots no more, so eight requests cover the schedule.
        outcome = self.generate(lambda request: provider_fixtures.reply() if len(self.requests) % 3 == 0 else self.reject())
        self.assertEqual(outcome, {"new_generation_records": 2, "prompt_rejections": 6, "skipped_rejected_prompt_slots": 16,
                                   "scheduled_attempts": 24})

    def test_the_cap_is_a_frozen_retry_setting(self):
        manifest = read_json(self.run / "manifest.json")
        config = {**manifest["config"], "generation": {**manifest["config"]["generation"], "retry": {
            **manifest["config"]["generation"]["retry"], "max_consecutive_prompt_rejections": 1}}}
        self.run = self.run.parent / "strict"
        create_run(self.run, manifest["tasks"], config, manifest["environment"], split="pilot",
                   model=provider_fixtures.MODEL, development=True)
        outcome = self.generate(self.reject)
        self.assertIn("Provider rejected a prompt as invalid_prompt", str(outcome))  # round 4 wording
        self.assertEqual(len(self.requests), 1)


class ImportNestingTests(provider_fixtures.RunFixture):
    """R2-providers-2: a row the evidence writer could not store is refused before anything is written."""

    def row(self, slot, **changes):
        return {"attempt_id": slot["attempt_id"], "prompt_hash": slot["prompt_hash"],
                "model_snapshot": slot["model_snapshot"], "raw_response": "by exact h", **changes}

    def nested(self, depth):
        value = {}
        for _ in range(depth - 1):
            value = {"k": value}
        return value

    def test_nesting_depth_counts_levels_without_recursion(self):
        self.assertEqual([nesting_depth(value) for value in (1, "x", None, {}, [[]], {"a": [1, {"b": 2}]})], [0, 0, 0, 1, 2, 3])
        self.assertEqual(nesting_depth(self.nested(100000)), 100000)

    def test_deep_rows_import_nothing(self):
        good = [self.row(slot, usage={"output_tokens": 3}) for slot in self.slots[:2]]
        for field in ("usage", "provenance", "generation_settings"):
            for depth in (65, 1200, 100000):
                with self.subTest(field=field, depth=depth):
                    with self.assertRaisesRegex(ValueError, r"Import row 3 cannot be stored as JSON evidence \(nested more "
                                                            r"than 64 levels deep\)"):
                        import_responses(self.run, [*good, self.row(self.slots[2], **{field: self.nested(depth)})])
                    self.assertEqual(self.generations(), [])
        result = import_responses(self.run, [*good, self.row(self.slots[2], provenance=self.nested(64))])
        self.assertEqual(result, {"imported": 3, "already_imported": 0})

    def test_the_cli_imports_nothing_from_a_deep_file_on_any_interpreter(self):
        path = self.run.parent / "responses.jsonl"
        for depth in (65, 1200):
            with self.subTest(depth=depth):
                deep = json.dumps(self.row(self.slots[2]))[:-1] + ', "provenance": ' + "[" * depth + "]" * depth + "}"
                path.write_text("".join(json.dumps(self.row(slot)) + "\n" for slot in self.slots[:2]) + deep + "\n")
                code, log = followups.quiet(["import-responses", "--run", self.run, "--responses", path])
                self.assertEqual(code, 2, log)
                self.assertEqual(self.generations(), [])

    def test_any_serialization_failure_imports_nothing(self):
        good = [self.row(slot) for slot in self.slots[:2]]
        with self.assertRaisesRegex(ValueError, "Import row 3 cannot be stored as JSON evidence"):
            import_responses(self.run, [*good, self.row(self.slots[2], usage={"tokens": {1, 2}})])
        with patch("lean_compression.providers.json_text", side_effect=RecursionError("maximum recursion depth exceeded")):
            with self.assertRaisesRegex(ValueError, r"Import row 1 cannot be stored as JSON evidence \(maximum recursion"):
                import_responses(self.run, good)
        self.assertEqual(self.generations(), [])
        self.assertEqual(import_responses(self.run, good), {"imported": 2, "already_imported": 0})


class ReplayOverrideBoundTests(unittest.TestCase):
    """R2-lean-additions-2: an override at or above 2^64 is an evaluator_error, never a wrapped limit."""

    @classmethod
    def setUpClass(cls):
        fixture = lean_fixtures.LeanIntegrationTests
        fixture.setUpClass()
        cls.lean, cls.process_env = fixture.lean, fixture.process_env

    def evaluate(self, overrides):
        return lean_fixtures.LeanIntegrationTests.evaluate(self, "⟨h, h⟩", mode="validate", **overrides)

    def test_replay_overrides_must_be_natural_numbers_below_two_to_the_64(self):
        refused = [{key: value} for key in ("replay_max_heartbeats", "replay_max_rec_depth")
                   for value in (2**64, 2**64 + 1, 2**65 + 2, -1, None)]
        accepted = [{"replay_max_heartbeats": 2**64 - 1}, {"replay_max_rec_depth": 2**64 - 1}]
        with ThreadPoolExecutor(4) as pool:
            results = list(pool.map(self.evaluate, refused + accepted))
        for overrides, result in zip(refused, results):
            key, = overrides
            with self.subTest(overrides=overrides):
                self.assertEqual((result["validity_status"], result.get("failure_reason")),
                                 ("evaluator_error", f"{key} must be a natural number below 2^64"), result)
        for overrides, result in zip(accepted, results[len(refused):]):
            with self.subTest(overrides=overrides):
                self.assertEqual(result["validity_status"], "success", result)


if __name__ == "__main__":
    unittest.main()
