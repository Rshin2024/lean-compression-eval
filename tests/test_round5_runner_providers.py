"""Round 5: malformed Lean result records, reconcile's reading of logged outcomes and captured lines, and the labels of
the process-RSS rows.

R3-runtime-claims-1: a result record whose heartbeats, diagnostics, candidate_declarations, statement_auxiliaries or
validity_status, or (when measured) source_tokens, root or nodes, is not shaped as the engine writes it, or that holds
a non-finite number, is unreadable: an infrastructure_error checker incident that keeps the process evidence, never a
crash and never a verdict.
R3-providers-reconcile-2: a call whose no_model_outcome generate logged before it stopped is not logged again, and a
release logs the call's last error and the prompt rejection a logged HTTP 400 holds, as generate would have.
R3-providers-reconcile-3: each resolution hides one captured line, so a later call's byte-identical body is still read.
R3-engine-analysis-1: the process-RSS rows say no runner of this harness records them.

Lean, the Docker client, the provider (HTTP) and the docker preflight are stand-ins; no Lean runtime, Docker daemon,
network or model is used.
"""
from datetime import datetime, timedelta
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parent))  # sibling fixtures, also when run as tests.test_round5_...

import test_reconcile as reconcile_fixtures
import test_reporting_remaining as reporting
import test_runner_remaining as remaining

from lean_compression import providers
from lean_compression.io import digest, read_json, read_jsonl, write_json
from lean_compression.runner import evaluate_run, execute_engine
from lean_compression.store import create_run, generation_path, record_generation, rejected_prompts

PROJECT = Path(__file__).resolve().parents[1]
VALID, native = remaining.VALID, remaining.native
FLAGGED, http_error, fail = reconcile_fixtures.FLAGGED, reconcile_fixtures.http_error, reconcile_fixtures.fail
body, response = reconcile_fixtures.body, reconcile_fixtures.response
MEASURED = {**VALID, "metric_status": "available", "root": "remaining_target", "source_tokens": {"W_S": 3, "W_B": 5},
            "nodes": [{"name": "remaining_target", "primitive": False, "signature": {}, "body": {}}]}
# Each field the runner reads, with values the engine never writes there.
MALFORMED = {
    "heartbeats": (5, [1, 2], {}, {"max_heartbeats_units": 5}, {"max_heartbeats_units": {}},
                   {"max_heartbeats_units": {"total": "5"}}, {"max_heartbeats_units": {"total": True}},
                   {"max_heartbeats_units": {"total": -1}}, {"max_heartbeats_units": {"total": 2 ** 60}},
                   {"max_heartbeats_units": {"total": 1e300}}),
    "diagnostics": ([1], None, "kinds", {"syntax_kinds": None}, {"syntax_kinds": "tactic"}, {"syntax_kinds": [1]}),
    "candidate_declarations": (5, None, "remaining_target", [1], [None]),
    "statement_auxiliaries": (5, "remaining_target", {}, [1]),
    "validity_status": (None, ["success"], 1)}
MALFORMED_MEASURED = {"source_tokens": (None, [3, 5], {"W_S": 3}, {"W_B": 5}), "root": (None, ["remaining_target"], 1),
                      "nodes": (5, {"remaining_target": {"primitive": True}}, [5], [{"primitive": True}], [{"name": 1}])}
NON_FINITE = ("NaN", "Infinity", "-Infinity", "1e999")


class Killed(BaseException):
    """The command is killed at this point: nothing after it runs."""


class MalformedResultTests(remaining.TemporaryTest):
    """R3-runtime-claims-1: a record of an unexpected shape is an incident on every path, whatever its verdict says."""

    def assert_unreadable(self, result, error, code=0):
        self.assertEqual((result["validity_status"], result["metric_status"]), ("infrastructure_error", "unavailable"))
        self.assertEqual(result["process_returncode"], code)
        self.assertIn(error, result["error"])
        self.assertEqual(result["stderr"], "said")
        self.assertIn("elapsed_seconds", result["resources"])
        self.assertIsNone(result["resources"]["heartbeats_used"])

    def test_a_malformed_field_is_an_incident_not_a_crash_or_a_verdict(self):
        for field, values in MALFORMED.items():
            for value in values:
                for verdict in ({**VALID}, {**VALID, "validity_status": "elaboration_failure", "metric_status": "unavailable"}):
                    with self.subTest(field=field, value=value, verdict=verdict["validity_status"]):
                        record = {**verdict, field: value}
                        self.assert_unreadable(native('printf "said" >&2; exit 0', json.dumps(record)),
                                               f"Lean's result record has a malformed {field} field")

    def test_a_value_no_record_can_store_is_an_incident(self):
        # A lone surrogate parses, but no record, event or digest can hold it.
        records = {"verdict": {**VALID, "failure_reason": "\ud800"},
                   "checker error": {"validity_status": "evaluator_error", "metric_status": "unavailable", "error": "\udfff"},
                   "syntax kind": {**VALID, "diagnostics": {"syntax_kinds": ["\ud800"]}},
                   "key": {**VALID, "\ud800": 1}}
        for name, record in records.items():
            with self.subTest(record=name):
                self.assert_unreadable(native('printf "said" >&2; exit 0', json.dumps(record)), "surrogates not allowed")
        measured = {**MEASURED, "nodes": [{**MEASURED["nodes"][0], "name": "\ud800"}]}
        self.assert_unreadable(native('printf "said" >&2; exit 0', json.dumps(measured), mode="extract"),
                               "surrogates not allowed")

    def test_real_heartbeat_totals_stay_readable(self):
        # The engine reports thousands of heartbeats, fractional; the largest stored total is about 5245.5.
        for total in (0, 398.278, 5245.544, 2 ** 53):
            with self.subTest(total=total):
                record = {**VALID, "heartbeats": {"max_heartbeats_units": {"total": total}}}
                result = native("exit 0", json.dumps(record))
                self.assertEqual(result["validity_status"], "success")
                self.assertEqual(result["resources"]["heartbeats_used"], total)

    def test_a_measured_extraction_must_name_its_tokens_root_and_nodes(self):
        self.assertEqual(native("exit 0", json.dumps(MEASURED), mode="extract")["validity_status"], "success")
        for field, values in MALFORMED_MEASURED.items():
            for value in (*values, "absent"):
                with self.subTest(field=field, value=value):
                    record = {key: item for key, item in {**MEASURED, field: value}.items() if item != "absent"}
                    self.assert_unreadable(native('printf "said" >&2; exit 0', json.dumps(record), mode="extract"),
                                           f"Lean's result record has a malformed {field} field")
        # An extraction that reports no measurement carries no graph to read: the engine's own outcome stands.
        unmeasured = {**VALID, "metric_status": "unavailable", "metric_error": "closure exceeds max_graph_nodes=1"}
        self.assertEqual(native("exit 0", json.dumps(unmeasured), mode="extract")["metric_error"],
                         "closure exceeds max_graph_nodes=1")

    def test_a_number_no_record_can_store_is_unreadable(self):
        for number in NON_FINITE:
            for record in ('{"validity_status": "elaboration_failure", "metric_status": "unavailable", "failure_reason": %s}',
                           json.dumps({**VALID, "heartbeats": {"max_heartbeats_units": {"total": 0}}}).replace("0}}", "%s}}")):
                with self.subTest(number=number, record=record[:40]):
                    self.assert_unreadable(native('printf "said" >&2; exit 0', record % number),
                                           f"Lean's result record holds {number}, which is not a finite number")

    def test_the_shapes_the_engines_write_are_still_verdicts(self):
        full = {"raw": {"elaboration": 1500, "kernel_replay": 500, "total": 2000},
                "max_heartbeats_units": {"elaboration": 1.5, "kernel_replay": 0.5, "total": 2.0}}
        for change, used in (({}, None), ({"heartbeats": None}, None), ({"heartbeats": full}, 2.0),
                             ({"statement_auxiliaries": None}, None), ({"statement_auxiliaries": ["remaining_target.match_1"]}, None),
                             ({"diagnostics": {"proof_nodes": 7, "syntax_kinds": ["Lean.Parser.Tactic.exact"]}}, None),
                             ({"candidate_declarations": [], "diagnostics": {"proof_nodes": None}}, None)):
            with self.subTest(change=change):
                result = native("exit 0", json.dumps({**VALID, **change}))
                self.assertEqual((result["validity_status"], result["resources"]["heartbeats_used"]), ("success", used))
        # The engine's own error record, as every engine version writes it.
        failure = native("exit 0", json.dumps({"validity_status": "elaboration_failure", "metric_status": "unavailable",
                                               "failure_reason": "unknown identifier 'x'"}))
        self.assertEqual((failure["validity_status"], failure["failure_reason"]), ("elaboration_failure", "unknown identifier 'x'"))

    def test_a_resource_exit_is_still_the_resource_limit_whatever_shape_it_left(self):
        result = native("kill -s KILL $$", json.dumps({**VALID, "heartbeats": [1]}))
        self.assertEqual((result["validity_status"], result["process_returncode"]), ("verification_resource_limit", -9))
        self.assertIn("malformed heartbeats field", result["error"])

    @unittest.skipUnless(remaining.SHELLS, "POSIX sh is unavailable")
    def test_a_container_returning_a_malformed_record_is_an_incident(self):
        record = json.dumps({**VALID, "heartbeats": {"max_heartbeats_units": 5}})
        stand_in = remaining.ContainerStandIn(self.temp, f"printf '%s' {remaining.shlex_quote(record)} > \"$4\"\n"
                                                         "printf said >&2\nexit 0", cgroup_files={"memory.peak": b"1048576\n"})
        with patch("subprocess.Popen", stand_in.popen):
            result = execute_engine(remaining.REQUEST, remaining.ENVIRONMENT, remaining.ENGINE,
                                    {"isolation": "docker", "docker_image": remaining.IMAGE}, mode="validate", wall_seconds=30)
        self.assert_unreadable(result, "Lean's result record has a malformed heartbeats field")
        self.assertEqual(result["resources"]["peak_memory_mib"], 1.0)

    def test_evaluate_logs_each_as_an_incident_and_checks_every_slot(self):
        """The reviewer's scenario: evaluate stopped at the first such record, logged nothing, and so on every rerun."""
        run = self.temp / "run"
        config = read_json(PROJECT / "configs/development.json")
        config["samples_per_goal_condition"] = 1
        environment = {**remaining.ENVIRONMENT, "lean_toolchain": config["environment"]["lean_toolchain"],
                       "mathlib_commit": config["environment"]["mathlib_commit"], "hash_mode": "development_metadata_only"}
        manifest = create_run(run, [remaining.TASK], config, environment, split="pilot", model="reference_fixture",
                              development=True)
        for slot in manifest["slots"]:
            record_generation(run, slot, "⟨h, h⟩", generation_status="completed")
        incidents, directory = run / "evaluations/v1/incidents.jsonl", run / "evaluations/v1"
        nan = '{"validity_status": "elaboration_failure", "metric_status": "unavailable", "failure_reason": NaN}'
        cases = ((json.dumps({**VALID, "heartbeats": {"max_heartbeats_units": 5}}), None, None, "malformed heartbeats field"),
                 (json.dumps({**VALID, "diagnostics": [1]}), None, None, "malformed diagnostics field"),
                 (json.dumps({**VALID, "statement_auxiliaries": 5}), None, None, "malformed statement_auxiliaries field"),
                 (nan, None, None, "holds NaN"),
                 (json.dumps(VALID), json.dumps({**MEASURED, "nodes": 5}), "extraction", "malformed nodes field"),
                 (json.dumps(VALID), json.dumps({**MEASURED, "source_tokens": [3, 5]}), "extraction", "malformed source_tokens field"))
        for validate, extract, phase, error in cases:
            with self.subTest(error=error):
                def popen(cmd, *args, **kwargs):
                    mode = json.loads(Path(cmd[-2]).read_text(encoding="utf-8"))["mode"]
                    return remaining.lean_stand_in("exit 0", validate if mode == "validate" else extract)(cmd, *args, **kwargs)
                logged = len(read_jsonl(incidents)) if incidents.exists() else 0
                with patch("platform.system", return_value="Darwin"), patch("subprocess.Popen", popen):
                    self.assertEqual(evaluate_run(run, remaining.ENGINE),
                                     {"new_evaluations": 0, "evaluation_version": "v1", "checker_incidents": 3})
                rows = read_jsonl(incidents)[logged:]
                self.assertEqual(sorted(row["attempt_id"] for row in rows), sorted(slot["attempt_id"] for slot in manifest["slots"]))
                for row in rows:
                    checked = row["validation" if phase is None else phase]
                    self.assertEqual((row["validity_status"], row.get("failure_phase")), ("infrastructure_error", phase))
                    self.assertEqual((checked["validity_status"], checked["process_returncode"]), ("infrastructure_error", 0))
                    self.assertIn(error, checked["error"])
                self.assertEqual([path.name for path in directory.glob("*.json")], ["policy.json"])
        # None of them held a slot back.
        with patch("platform.system", return_value="Darwin"), \
             patch("subprocess.Popen", remaining.lean_stand_in("exit 0", json.dumps(VALID))):
            self.assertEqual(evaluate_run(run, remaining.ENGINE)["new_evaluations"], 3)


class LoggedOutcomeTests(reconcile_fixtures.ReconcileFixture):
    """R3-providers-reconcile-2: reconcile honours the outcome generate logged, and the error a call stopped after."""

    def killed(self, answer, *, before):
        """generate killed just after appending its no_model_outcome event, or, if before, just before appending it."""
        real = providers.append_event
        def append(path, event, **options):
            if before and event.get("event") == "no_model_outcome":
                raise Killed
            real(path, event, **options)
            if event.get("event") == "no_model_outcome":
                raise Killed
        with patch("lean_compression.providers.append_event", side_effect=append), self.assertRaises(Killed):
            self.generate(answer)
        self.assertEqual([marker.stem for marker in self.markers()], [self.attempt(0)])

    def test_an_outcome_generate_logged_before_it_was_killed_is_not_logged_again(self):
        failed = response(status="failed", error={"code": "server_error"})
        for name, answer, rejected in (("rejected", lambda request: fail(http_error(400, FLAGGED)), True),
                                       ("failed-body", body(failed), False),
                                       ("server-errors", lambda request: fail(http_error(500)), False)):
            with self.subTest(name):
                self.fresh(name)
                self.killed(answer, before=False)
                events, before = self.transport(), self.files()
                self.assertEqual(self.outages()["no_model_outcome_events"], 1)
                entry, = self.reconcile()["attempts"]
                self.assertEqual((entry["action"], entry["finding"]), ("remove_marker", "transport.jsonl already logs its outcome"))
                self.assertEqual((entry["call"][0]["event"], entry["call"][-1]["event"]), ("request_started", "no_model_outcome"))
                self.assertEqual(self.files(), before)
                # Neither --apply nor --release-unreadable logs the call a second time.
                self.assertEqual(self.reconcile("--apply", "--release-unreadable")["actions"], {"remove_marker": 1})
                self.assertEqual((self.markers(), self.transport(), self.outages()["no_model_outcome_events"]), ([], events, 1))
                amendment, = self.amendments()
                self.assertEqual({key: amendment[key] for key in amendment if key != "recorded_at"}, {
                    "kind": "in_flight_reconciliation", "attempt_id": self.attempt(0), "resolution": "outcome_already_logged",
                    "marker": entry["marker"]})
                self.assertEqual(list(rejected_prompts(self.run, self.slots)), [self.slots[0]["prompt_hash"]] if rejected else [])
                # generate goes on as if it had removed the marker itself.
                self.assertEqual(self.generate(body(response()), limit=1)["new_generation_records"], 1)
                self.assertEqual(generation_path(self.run, self.attempt(0)).exists(), not rejected)

    def test_a_marker_written_after_the_logged_outcome_sent_nothing(self):
        self.assertRegex(str(self.generate(lambda request: fail(http_error(500)))), "No model outcome")
        logged = datetime.fromisoformat(self.transport()[-1]["recorded_at"])
        # A later generate killed between writing its marker and logging its request.
        write_json(self.run / "inflight" / f"{self.attempt(0)}.json",
                   {"started_at": (logged + timedelta(seconds=1)).isoformat(), "attempt_id": self.attempt(0)})
        entry, = self.reconcile()["attempts"]
        self.assertEqual((entry["action"], entry["finding"], entry["call"]),
                         ("keep", "no request is logged for this call, so none was sent", []))
        # Its marker unread, the earlier call is not taken for this one, nor ruled out.
        (self.run / "inflight" / f"{self.attempt(0)}.json").write_text("{torn", encoding="utf-8")
        entry, = self.reconcile()["attempts"]
        self.assertEqual((entry["marker"], entry["action"], entry["finding"]),
                         (None, "keep", "its marker cannot be read, so whether its call sent a request is unknown"))
        # Without any request logged for the attempt, none was sent.
        (self.run / "inflight" / f"{self.attempt(1)}.json").write_text("", encoding="utf-8")
        entry = self.reconcile("--attempt", self.attempt(1))["attempts"][0]
        self.assertEqual(entry["finding"], "no request is logged for this call, so none was sent")

    def released_and_logged(self, name, answer):
        """The no_model_outcome generate logs for this call, and the one a release logs for it when generate was killed
        just before logging its own, each without the fields only one of them has."""
        fields = lambda event: {key: event[key] for key in event if key not in ("recorded_at", "reconciliation")}
        self.fresh(name + "-generate")
        self.assertIsInstance(self.generate(answer, limit=1), (dict, RuntimeError))
        own = fields(self.outcomes()[-1])
        self.fresh(name + "-killed")
        self.killed(answer, before=True)
        entry, = self.reconcile("--apply", "--release-unreadable")["attempts"]
        released = self.transport()[-1]
        self.assertEqual(released["reconciliation"]["reason"], entry["finding"])
        return own, fields(released), entry["finding"]

    def test_a_call_killed_after_its_error_is_released_as_generate_would_log_it(self):
        own, released, finding = self.released_and_logged("rejected", lambda request: fail(http_error(400, FLAGGED)))
        self.assertEqual(released, own)
        self.assertEqual((released["prompt_rejected"], released["last_error"]),
                         (True, {"http_status": 400, "body": FLAGGED, "retry_after": None}))
        self.assertEqual(finding, "no response body was captured, and the call stopped without a logged outcome; "
                                  "its logged HTTP 400 rejected the prompt as invalid_prompt")
        # Remembered like generate's own rejection: plain generate sends none of that prompt's slots.
        self.assertEqual(list(rejected_prompts(self.run, self.slots)), [self.slots[0]["prompt_hash"]])
        self.assertEqual(self.outages()["prompt_rejected_attempts"], 4)
        outcome = self.generate(body(response()))
        self.assertEqual((outcome["prompt_rejections"], outcome["skipped_rejected_prompt_slots"]), (0, 4))
        self.assertNotIn(self.slots[0]["prompt_hash"], [digest(json.loads(request.data)["input"]) for request in self.requests])
        unsent = lambda request: fail(urllib.error.URLError("connection refused"))
        for name, answer, error in (("server-errors", lambda request: fail(http_error(503)), {"http_status": 503}),
                                    ("unsent", unsent, {"type": "URLError"})):
            with self.subTest(name):
                own, released, finding = self.released_and_logged(name, answer)
                self.assertEqual(released, own)
                self.assertEqual((released["prompt_rejected"], {key: released["last_error"][key] for key in error}),
                                 (False, error))
                self.assertEqual(finding, "no response body was captured, and the call stopped without a logged outcome")
                # Not a rejection: the next generate sends the same attempt ID again.
                self.assertEqual(self.generate(body(response()), limit=1)["new_generation_records"], 1)
                self.assertTrue(generation_path(self.run, self.attempt(0)).exists())


class IdenticalBodyTests(reconcile_fixtures.ReconcileFixture):
    """R3-providers-reconcile-3: a resolution hides one captured line, not every line holding the same bytes."""

    def test_a_later_calls_identical_body_is_still_read(self):
        page = b"<html><body>502 Bad Gateway</body></html>"
        for call in (1, 2, 3):
            with self.subTest(call=call):
                self.assertRegex(str(self.generate(body(page))), "outcome is unknown")
                entry, = self.reconcile()["attempts"]
                self.assertEqual(([summary["entry"] for summary in entry["captures"]], entry["finding"]),
                                 ([call], "unreadable: JSONDecodeError: Expecting value: line 1 column 1 (char 0)"))
                self.reconcile("--apply", "--release-unreadable")
                self.assertEqual((self.transport()[-1]["capture"], self.amendments()[-1]["capture"]),
                                 (self.capture(0, page), self.capture(0, page)))
        self.assertEqual(len(read_jsonl(self.run / self.capture(0, page)["path"])), 3)

    def test_identical_bodies_generate_resolved_hide_only_their_own_lines(self):
        failed = response(status="failed", error={"code": "server_error"})
        self.assertRegex(str(self.generate(body(failed))), "No model outcome")
        self.crash(failed)
        entry, = self.reconcile()["attempts"]
        self.assertEqual(([summary["entry"] for summary in entry["captures"]], entry["finding"]),
                         ([2], "a provider failure, not a model outcome"))
        self.reconcile("--apply", "--release-unreadable")
        released = self.transport()[-1]
        self.assertEqual((released["capture"], released["provider_response"]["status"], released["prompt_rejected"]),
                         (self.capture(0, failed), "failed", False))
        # Each body was resolved once: the next call's response is read from its own line and recorded.
        self.crash(response())
        entry, = self.reconcile("--apply")["attempts"]
        self.assertEqual(([summary["entry"] for summary in entry["captures"]], entry["action"]), ([3], "record"))
        self.assertEqual(read_json(generation_path(self.run, self.attempt(0)))["response_capture"], self.capture(0, response()))


class ProcessPeakLabelTests(unittest.TestCase):
    """R3-engine-analysis-1: no runner of this harness records a process's RSS, and the rows reading one say so."""

    def test_the_process_rss_rows_name_no_records_this_harness_never_wrote(self):
        resources = native("exit 0", json.dumps(VALID))["resources"]
        self.assertEqual((resources["peak_memory_mib"], resources["peak_memory_reason"]), (None, "not_instrumented"))
        self.assertEqual([key for key in resources if "rss" in key], [])
        measurements, rows, page = reporting.resource_report([reporting.scheduled(0, resources, resources)])
        for stage in ("validation", "extraction"):
            self.assertEqual(measurements[f"{stage}_peak_rss_mib"]["observed_count"], 0)
            self.assertIn("no runner of this harness records them", rows[f"{stage}_peak_rss_mib"]["description"])
            self.assertIn(f"<td>{stage} peak RSS MiB, Lean process (not recorded by this harness)</td>", page)
        documented = " ".join((PROJECT / "docs/analysis.md").read_text(encoding="utf-8").split())
        self.assertIn("No runner of this harness records them", documented)
        for text in (page, json.dumps(rows), documented):
            self.assertNotIn("older native records", text)


if __name__ == "__main__":
    unittest.main()
