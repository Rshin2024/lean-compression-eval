"""Held in-flight markers and captured bodies: reconcile resolves a call whose outcome generate could not know from the
logged evidence alone, recording a captured model response or, only when asked, releasing the attempt for another
request; inspect-capture decodes captures read-only; a failed invalid_prompt response is a prompt rejection whatever
else it holds, and every no_model_outcome event names its capture.

The provider (HTTP) and the docker preflight are mocked; no network, model, Docker daemon or Lean runtime is used.
Every reconcile and inspect-capture runs with an HTTP layer that fails the test if it is called.
"""
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))  # sibling fixtures, also when run as tests.test_reconcile

import test_provider_followups as provider_fixtures
import test_review_followups as followups

from lean_compression import providers
from lean_compression.io import append_event, digest, read_json, read_jsonl, write_json
from lean_compression.providers import generate_run, in_flight, response_payload
from lean_compression.store import create_run, generation_path, logged_outages, record_generation, records, rejected_prompts

MODEL = provider_fixtures.MODEL
FLAGGED = provider_fixtures.FLAGGED
http_error, fail = provider_fixtures.http_error, provider_fixtures.fail
KEY = "sk-unit-SECRET-key-0123456789"


def response(text="by exact h", status="completed", model=MODEL, **fields):
    """Response bytes as the provider sends them; json.dumps writes a lone surrogate as its \\u escape."""
    return json.dumps({"id": "resp_BILLED", "model": model, "status": status,
                       "output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}],
                       **fields}).encode()


def body(data):
    """An answer returning exactly these bytes."""
    def answer(request):
        value = io.BytesIO(data)
        value.headers = {"x-request-id": "req_unit"}
        return value
    return answer


def no_call(*args, **kwargs):
    raise AssertionError("reconcile and inspect-capture never make a model call")


class ReconcileFixture(provider_fixtures.RunFixture):
    """Two tasks, 24 slots; generate asks them in schedule order, so the next call is for the first slot without a
    generation, a released one included."""

    def fresh(self, name):
        """The same tasks and configuration frozen into a new run directory (the same attempt IDs)."""
        self.run = self.run.parent / name
        self.manifest = create_run(self.run, self.manifest["tasks"], self.manifest["config"], self.manifest["environment"],
                                   split="pilot", model=MODEL, development=True)
        self.slots = self.manifest["slots"]

    def command(self, *argv):
        """A reconcile or inspect-capture CLI call: (parsed stdout or exit code, stderr)."""
        with patch("lean_compression.providers.urllib.request.urlopen", side_effect=no_call), \
             patch.dict(os.environ, {"OPENAI_API_KEY": KEY}):
            return followups.quiet([argv[0], "--run", self.run, *argv[1:]])

    def reconcile(self, *options):
        result, log = self.command("reconcile", *options)
        self.assertIsInstance(result, dict, log)
        return result

    def crash(self, data):
        """generate stopped just after capturing this body, as by a kill: the marker is kept, nothing more is logged."""
        with patch("lean_compression.providers.parse_body", side_effect=KeyError("stopped")), self.provider(body(data)):
            with self.assertRaises(KeyError):
                generate_run(self.run)

    def files(self):
        """Every file of the run and its bytes, to show a command changed nothing."""
        return {path.relative_to(self.run).as_posix(): path.read_bytes() for path in sorted(self.run.rglob("*"))
                if path.is_file()}

    def attempt(self, index):
        return self.slots[index]["attempt_id"]

    def capture(self, index, data):
        return {"path": f"captures/{self.attempt(index)}.jsonl", "body_sha256": hashlib.sha256(data).hexdigest()}

    def amendments(self):
        path = self.run / "amendments.jsonl"
        return read_jsonl(path) if path.exists() else []

    def transport(self):
        return read_jsonl(self.run / "transport.jsonl")

    def outages(self):
        return logged_outages(self.run)["provider_outages"]


class HeldMarkerTests(ReconcileFixture):
    """Issue 1: a held marker is resolved only by reconcile, never released silently, and nothing is resent by it."""

    def test_an_unreadable_body_is_released_only_when_asked(self):
        data = b"<html><body>502 Bad Gateway</body></html>"
        reason = "unreadable: JSONDecodeError: Expecting value: line 1 column 1 (char 0)"
        self.assertRegex(str(self.generate(body(data))), "outcome is unknown .* retained for reconcile")
        self.assertEqual([marker.stem for marker in self.markers()], [self.attempt(0)])
        before = self.files()
        # A dry run, --apply alone and --release-unreadable alone all leave every file as it was.
        for options, action in (((), "keep"), (("--apply",), "keep"), (("--release-unreadable",), "release")):
            with self.subTest(options=options):
                result = self.reconcile(*options)
                self.assertEqual((result["applied"], result["in_flight_markers"], result["actions"]),
                                 ("--apply" in options, 1, {action: 1}))
                entry, = result["attempts"]
                self.assertEqual((entry["attempt_id"], entry["finding"]), (self.attempt(0), reason))
                self.assertEqual([event["event"] for event in entry["call"]],
                                 ["request_started", "response_received", "outcome_unknown"])
                summary, = entry["captures"]
                self.assertEqual((summary["body_sha256"], summary["bytes"], summary["json_object"], summary["recoverable"]),
                                 (hashlib.sha256(data).hexdigest(), len(data), False, False))
                self.assertEqual(self.files(), before)
        # The double-billing guard: generate still sends nothing for it, and names the remedy.
        outcome = self.generate(lambda request: provider_fixtures.reply())
        self.assertRegex(str(outcome), f"Unresolved in-flight call for {self.attempt(0)}; .*reconcile --run")
        self.assertEqual(self.requests, [])
        self.assertEqual(self.outages()["in_flight_markers"], 1)
        result = self.reconcile("--apply", "--release-unreadable")
        self.assertEqual(result["actions"], {"release": 1})
        self.assertEqual(self.markers(), [])
        marker = result["attempts"][0]["marker"]
        self.assertEqual(sorted(marker), ["attempt_id", "started_at"])
        amendment, = self.amendments()
        self.assertEqual({key: amendment[key] for key in amendment if key != "recorded_at"}, {
            "kind": "in_flight_reconciliation", "attempt_id": self.attempt(0), "resolution": "released_without_model_outcome",
            "marker": marker, "reason": reason, "capture": self.capture(0, data)})
        released = self.transport()[-1]
        self.assertEqual({key: released[key] for key in released if key != "recorded_at"}, {
            "attempt_id": self.attempt(0), "event": "no_model_outcome", "capture": self.capture(0, data),
            "provider_response": None, "last_error": None, "prompt_rejected": False,
            "reconciliation": {"marker": marker, "reason": reason}})
        # Still pending, and counted as a provider call without a model outcome until generated.
        self.assertEqual({key: self.outages()[key] for key in ("unresolved_attempts", "in_flight_markers")},
                         {"unresolved_attempts": 1, "in_flight_markers": 0})
        self.assertEqual(list(rejected_prompts(self.run, self.slots)), [])
        # Only a later generate sends the same attempt ID again.
        outcome = self.generate(body(response()), limit=1)
        self.assertEqual((outcome["new_generation_records"], len(self.requests)), (1, 1))
        self.assertEqual(read_json(generation_path(self.run, self.attempt(0)))["generation_status"], "completed")
        self.assertEqual(len(read_jsonl(self.run / self.capture(0, data)["path"])), 2)
        self.assertEqual(self.outages()["unresolved_attempts"], 0)
        self.assertEqual(self.reconcile("--apply", "--release-unreadable")["attempts"], [])

    def test_a_lost_or_interrupted_call_is_released_only_when_asked(self):
        def lost(request):
            raise TimeoutError("The read operation timed out")
        def interrupted(request):
            raise KeyboardInterrupt
        for answer, finding in ((lost, "no response body was captured; the call logged outcome_unknown "
                                       "(TimeoutError: The read operation timed out)"),
                                (interrupted, "no response body was captured, and the call stopped without a logged outcome")):
            with self.subTest(finding=finding):
                self.fresh(answer.__name__)
                try:
                    outcome = self.generate(answer)
                except KeyboardInterrupt:
                    outcome = "interrupted"
                self.assertEqual([marker.stem for marker in self.markers()], [self.attempt(0)], outcome)
                entry, = self.reconcile()["attempts"]
                self.assertEqual((entry["action"], entry["finding"], entry["captures"]), ("keep", finding, []))
                self.assertEqual(self.reconcile("--apply")["actions"], {"keep": 1})
                self.assertEqual(len(self.markers()), 1)
                self.assertEqual(self.reconcile("--apply", "--release-unreadable")["actions"], {"release": 1})
                self.assertEqual((self.markers(), self.transport()[-1]["event"], self.transport()[-1]["capture"]),
                                 ([], "no_model_outcome", None))
                self.assertEqual((self.amendments()[-1]["capture"], self.amendments()[-1]["reason"]), (None, finding))
                # A later generate asks this attempt ID again first.
                self.assertEqual(self.generate(body(response()), limit=1)["new_generation_records"], 1)
                self.assertTrue(generation_path(self.run, self.attempt(0)).exists())
                self.assertEqual(self.outages()["unresolved_attempts"], 0)

    def test_a_captured_model_response_is_recorded_with_its_provenance(self):
        usage, details = {"input_tokens": 812, "output_tokens": 4096}, {"reason": "max_output_tokens"}
        refusal = [{"type": "message", "content": [{"type": "refusal", "refusal": "I cannot help with that."}]}]
        for index, (data, status) in enumerate((
                (response(usage={"input_tokens": 9, "output_tokens": 3}), "completed"),
                (response(output=refusal), "refusal"),
                (response("by simp", status="incomplete", usage=usage, incomplete_details=details), "truncated_output"),
                (response("by exact \ud835", status="incomplete", usage=usage, incomplete_details=details),
                 "malformed_response"))):
            with self.subTest(status=status):
                self.crash(data)
                attempt = self.attempt(index)
                self.assertEqual([marker.stem for marker in self.markers()], [attempt])
                transport, before = self.transport(), self.files()
                entry, = self.reconcile()["attempts"]
                self.assertEqual((entry["action"], entry["capture"], entry["finding"]),
                                 ("record", self.capture(index, data),
                                  f"captured entry 1 reads as {status!r} from the pinned snapshot"))
                self.assertEqual(self.files(), before)
                entry, = self.reconcile("--apply")["attempts"]
                self.assertEqual((entry["action"], self.markers()), ("record", []))
                generation = read_json(generation_path(self.run, attempt))
                self.assertEqual((generation["generation_status"], generation["response_capture"],
                                  generation["provider_response_id"], generation["returned_model"]),
                                 (status, self.capture(index, data), "resp_BILLED", MODEL))
                # What generate records from the same bytes, but for its workflow timing, plus its provenance.
                self.assertEqual(generation["generation_settings"], self.manifest["config"]["generation"])
                self.assertNotIn("generation_elapsed_seconds", generation)
                self.assertEqual(generation["reconciliation"], {
                    "marker": entry["marker"], "transport_index": 0, "request_id": "req_unit",
                    "captured_at": read_jsonl(self.run / self.capture(index, data)["path"])[0]["recorded_at"],
                    "request_hash": digest(response_payload(self.slots[index], self.manifest["config"]["generation"]))})
                if status == "malformed_response":
                    self.assertIn("by exact \\ud835", generation["raw_response"])
                elif status != "refusal":
                    self.assertEqual(generation["raw_response"], "by exact h" if status == "completed" else "by simp")
                amendment = self.amendments()[-1]
                self.assertEqual({key: amendment[key] for key in amendment if key != "recorded_at"}, {
                    "kind": "in_flight_reconciliation", "attempt_id": attempt, "resolution": "generation_recorded",
                    "marker": entry["marker"], "capture": self.capture(index, data), "generation_status": status,
                    "raw_response_hash": generation["raw_response_hash"], **{
                        key: generation["reconciliation"][key]
                        for key in ("transport_index", "request_id", "captured_at", "request_hash")}})
                # Recording adds nothing to the transport log, and the slot counts as generated.
                self.assertEqual(self.transport(), transport)
                row, = [row for row in records(self.run) if row["attempt_id"] == attempt]
                self.assertEqual(row["validity_status"], "not_evaluated" if status == "completed" else status)
        self.assertEqual((self.outages()["unresolved_attempts"], self.outages()["in_flight_markers"]), (0, 0))
        # generate moves on to the next slot; a reconciled attempt is never asked again.
        outcome = self.generate(body(response()), limit=1)
        self.assertEqual((outcome["new_generation_records"], len(self.requests)), (1, 1))
        self.assertTrue(generation_path(self.run, self.attempt(4)).exists())
        self.assertEqual(len(self.amendments()), 4)

    def test_bodies_that_are_not_the_attempts_model_response_are_never_recorded(self):
        payload_hash = lambda: digest(response_payload(self.slots[0], self.manifest["config"]["generation"]))
        def edit(path, text):
            path.write_text(text)
        cases = (
            ("another model", response(model="other-model-2027-01-01"), None,
             "the response is from 'other-model-2027-01-01', not the pinned snapshot"),
            ("provider failure", response(status="failed", error={"code": "server_error"}), None,
             "a provider failure, not a model outcome"),
            # Edited evidence: other bytes than those hashed, a torn or misfiled line, or another request than the frozen one.
            ("other bytes", response(), lambda capture, line: edit(capture, json.dumps(
                {**line, "body_base64": base64.b64encode(response("by trivial")).decode()}) + "\n"),
             "the body does not match its body_sha256"),
            ("torn line", response(), lambda capture, line: edit(capture, "{torn\n"), "the capture line is not a JSON object"),
            ("another attempt's line", response(), lambda capture, line: edit(capture, json.dumps(
                {**line, "attempt_id": self.attempt(5)}) + "\n"), "the capture line names another attempt"),
            ("another request", response(), lambda capture, line: edit(self.run / "transport.jsonl", (
                self.run / "transport.jsonl").read_text().replace(payload_hash(), "0" * 64)),
             "a logged request for this attempt is not the frozen one"))
        for name, data, change, finding in cases:
            with self.subTest(name):
                self.fresh(name.replace(" ", "-").replace("'", ""))
                self.crash(data)
                capture = self.run / self.capture(0, data)["path"]
                if change:
                    change(capture, read_jsonl(capture)[0])
                entry, = self.reconcile()["attempts"]
                self.assertEqual((entry["action"], entry["finding"]), ("keep", finding))
                before = self.files()
                self.assertEqual(self.reconcile("--apply")["actions"], {"keep": 1})
                self.assertEqual(self.files(), before)
                self.assertEqual(self.reconcile("--apply", "--release-unreadable")["actions"], {"release": 1})
                self.assertEqual((generation_path(self.run, self.attempt(0)).exists(), self.markers()), (False, []))

    def test_bodies_already_resolved_are_not_considered_again(self):
        failed = response(status="failed", error={"code": "server_error"})
        def lost(request):
            raise TimeoutError("The read operation timed out")
        for legacy in (True, False):
            with self.subTest(legacy=legacy):
                self.fresh(f"legacy-{legacy}")
                self.assertRegex(str(self.generate(body(failed))), "No model outcome")
                if legacy:
                    # As logged before no_model_outcome events named their capture.
                    log = self.run / "transport.jsonl"
                    log.write_text("".join(json.dumps({key: value for key, value in event.items() if key != "capture"
                                                       or event["event"] != "no_model_outcome"}) + "\n"
                                           for event in read_jsonl(log)))
                self.assertRegex(str(self.generate(lost)), "outcome is unknown")
                # The provider failure was this attempt's earlier call, resolved when generate logged it.
                entry, = self.reconcile("--apply", "--release-unreadable")["attempts"]
                self.assertEqual((entry["captures"], entry["finding"], self.transport()[-1]["capture"]), (
                    [], "no response body was captured; the call logged outcome_unknown "
                        "(TimeoutError: The read operation timed out)", None))
        self.crash(b"not json")
        self.assertEqual(self.reconcile("--apply", "--release-unreadable")["actions"], {"release": 1})
        # Neither body the attempt's calls were released with is read again: only the newest call's response is.
        self.crash(response())
        entry, = self.reconcile("--apply")["attempts"]
        self.assertEqual(([summary["entry"] for summary in entry["captures"]], entry["action"]), ([3], "record"))
        self.assertEqual(read_json(generation_path(self.run, self.attempt(0)))["response_capture"], self.capture(0, response()))

    def test_evidence_is_read_once_the_markers_are_locked(self):
        write_json(self.run / "inflight" / f"{self.attempt(0)}.json",
                   {"started_at": "2026-10-01T00:00:00+00:00", "attempt_id": self.attempt(0)})
        request_hash = digest(response_payload(self.slots[0], self.manifest["config"]["generation"]))
        locked = providers._abandoned
        def meanwhile(marker, **options):
            # Another command's call for this attempt ends just before the marker is locked.
            for event in ({"transport_index": 0, "event": "request_started", "request_hash": request_hash},
                          {"event": "outcome_unknown", "type": "TimeoutError", "message": "timed out"}):
                append_event(self.run / "transport.jsonl", {"attempt_id": self.attempt(0), **event})
            return locked(marker, **options)
        with patch("lean_compression.providers._abandoned", meanwhile):
            entry, = self.reconcile()["attempts"]
        self.assertEqual(entry["finding"],
                         "no response body was captured; the call logged outcome_unknown (TimeoutError: timed out)")

    def test_a_released_rejection_of_the_prompt_is_remembered(self):
        flagged = json.dumps({"id": "resp_FLAGGED", "model": MODEL, "status": "failed", "output": [],
                              "error": {"code": "invalid_prompt", "message": "Invalid prompt: flagged"}}).encode()
        self.crash(flagged)
        entry, = self.reconcile("--apply", "--release-unreadable")["attempts"]
        self.assertEqual(entry["captures"][0]["prompt_rejected"], True)
        released = self.transport()[-1]
        self.assertEqual((released["prompt_rejected"], released["provider_response"]["id"], released["capture"]),
                         (True, "resp_FLAGGED", self.capture(0, flagged)))
        self.assertEqual(list(rejected_prompts(self.run, self.slots)), [self.slots[0]["prompt_hash"]])

    def test_a_running_call_is_never_touched(self):
        asked, answer, outcome = threading.Event(), threading.Event(), []
        def slow(request):
            asked.set()
            answer.wait(10)
            return body(response())(request)
        with self.provider(slow):
            worker = threading.Thread(target=lambda: outcome.append(generate_run(self.run, limit=1)))
            worker.start()
            try:
                self.assertTrue(asked.wait(10))
                for options in ((), ("--apply", "--release-unreadable")):
                    self.assertEqual(self.reconcile(*options)["attempts"], [{
                        "attempt_id": self.attempt(0), "action": "none",
                        "finding": "a running generate or reconcile holds its lock"}])
                self.assertEqual(([marker.stem for marker in self.markers()], self.amendments()), ([self.attempt(0)], []))
            finally:
                answer.set()
                worker.join(10)
        self.assertEqual((outcome[0]["new_generation_records"], self.markers()), (1, []))
        # However a command ends, its lock goes with it: a kept marker is free once generate has stopped.
        self.assertRegex(str(self.generate(body(b""))), "outcome is unknown")
        self.assertEqual(self.reconcile()["actions"], {"keep": 1})

    def test_markers_without_a_request_or_with_a_generation(self):
        # An older generate's marker carries no lock; one stopped before its request was logged sent none.
        write_json(self.run / "inflight" / f"{self.attempt(0)}.json",
                   {"started_at": "2026-10-01T00:00:00+00:00", "attempt_id": self.attempt(0)})
        entry, = self.reconcile()["attempts"]
        self.assertEqual((entry["action"], entry["finding"]), ("keep", "no request is logged for this call, so none was sent"))
        # A command stopped between recording a generation and removing its marker: the marker is removed, and logged.
        record_generation(self.run, self.slots[1], "by exact h", generation_status="completed")
        in_flight(self.run / "inflight" / f"{self.attempt(1)}.json", self.attempt(1)).close()
        result = self.reconcile("--attempt", self.attempt(1))
        self.assertEqual((result["actions"], result["attempts"][0]["finding"]),
                         ({"remove_marker": 1}, "the attempt already has a generation"))
        self.assertEqual(len(self.markers()), 2)
        generation = read_json(generation_path(self.run, self.attempt(1)))
        self.assertEqual(self.reconcile("--apply", "--release-unreadable")["actions"], {"release": 1, "remove_marker": 1})
        self.assertEqual((self.markers(), read_json(generation_path(self.run, self.attempt(1)))), ([], generation))
        self.assertEqual(sorted((row["attempt_id"], row["resolution"]) for row in self.amendments()), sorted(
            [(self.attempt(0), "released_without_model_outcome"), (self.attempt(1), "generation_already_recorded")]))
        removed, = [row for row in self.amendments() if row["attempt_id"] == self.attempt(1)]
        self.assertEqual(removed["raw_response_hash"], generation["raw_response_hash"])
        self.assertEqual([event["attempt_id"] for event in self.transport()], [self.attempt(0)])

    def test_a_stopped_reconcile_finishes_without_logging_twice(self):
        self.generate(body(b"not json"))
        real_unlink = Path.unlink
        def stopped(path, *args, **kwargs):
            if path.parent.name == "inflight":
                raise OSError("stopped before removing the marker")
            return real_unlink(path, *args, **kwargs)
        with patch("pathlib.Path.unlink", stopped):
            code, log = self.command("reconcile", "--apply", "--release-unreadable")
        self.assertEqual((code, len(self.markers())), (2, 1))
        self.assertIn("stopped before removing the marker", log)
        events, amendments = self.transport(), self.amendments()
        entry, = self.reconcile()["attempts"]
        self.assertEqual((entry["action"], entry["finding"]), ("remove_marker", "transport.jsonl already logs its release"))
        self.assertEqual(self.reconcile("--apply")["actions"], {"remove_marker": 1})
        self.assertEqual((self.markers(), self.transport(), self.amendments()), ([], events, amendments))
        # Stopped between recording a captured response and removing the marker of the attempt, sent again.
        self.crash(response())
        with patch("pathlib.Path.unlink", stopped):
            code, log = self.command("reconcile", "--apply")
        self.assertEqual((code, generation_path(self.run, self.attempt(0)).exists()), (2, True))
        self.assertEqual(self.reconcile("--apply")["actions"], {"remove_marker": 1})
        self.assertEqual([row["resolution"] for row in self.amendments()],
                         ["released_without_model_outcome", "generation_recorded", "generation_already_recorded"])

    def test_only_scheduled_attempts_with_a_held_marker(self):
        self.assertEqual(self.reconcile()["attempts"], [])
        self.generate(body(b"not json"))
        (self.run / "inflight" / "unscheduled.json").write_text("{}")
        for attempt, message in (("unscheduled", "Not a scheduled attempt ID: unscheduled"),
                                 ("../inflight/x", "Not a scheduled attempt ID"),
                                 (self.attempt(1), f"Attempt {self.attempt(1)} has no in-flight marker")):
            code, log = self.command("reconcile", "--attempt", attempt, "--apply", "--release-unreadable")
            self.assertEqual(code, 2)
            self.assertIn(message, log)
        self.assertEqual(self.reconcile("--apply", "--release-unreadable", "--attempt", self.attempt(0))["actions"],
                         {"release": 1})
        self.assertEqual(self.reconcile("--apply", "--release-unreadable")["attempts"], [{
            "attempt_id": "unscheduled", "action": "none", "finding": "not a scheduled attempt"}])
        self.assertTrue((self.run / "inflight" / "unscheduled.json").exists())


class PromptRejectionCaptureTests(ReconcileFixture):
    """Issue 5: a failed invalid_prompt response is a counted prompt rejection even when the log cannot hold it, and
    every no_model_outcome event names the capture of the body it was read from."""

    def rejecting(self, data):
        """The provider answers the first slot's prompt with these bytes and every other one with a response."""
        def answer(request):
            rejected = digest(json.loads(request.data)["input"]) == self.slots[0]["prompt_hash"]
            return body(data if rejected else response())(request)
        return answer

    def test_an_unstorable_failed_rejection_is_a_counted_prompt_rejection(self):
        flagged = {"id": "resp_FLAGGED", "model": MODEL, "status": "failed", "output": [],
                   "error": {"code": "invalid_prompt", "message": "Invalid prompt: flagged"}}
        nan = json.dumps(flagged).encode().replace(b'"status"', b'"usage": {"input_tokens": NaN}, "status"')
        surrogate = json.dumps({**flagged, "error": {**flagged["error"], "message": "flagged \ud835"}}).encode()
        for name, data in (("nan-usage", nan), ("surrogate-message", surrogate)):
            with self.subTest(name):
                self.fresh(name)
                outcome = self.generate(self.rejecting(data))
                self.assertEqual(outcome, {"new_generation_records": 20, "prompt_rejections": 1,
                                           "skipped_rejected_prompt_slots": 3, "scheduled_attempts": 24})
                rejection, = self.outcomes()
                self.assertEqual({key: rejection[key] for key in ("attempt_id", "prompt_rejected", "provider_response",
                                                                  "capture", "last_error")},
                                 {"attempt_id": self.attempt(0), "prompt_rejected": True, "provider_response": None,
                                  "capture": self.capture(0, data), "last_error": None})
                self.assertEqual(list(rejected_prompts(self.run, self.slots)), [self.slots[0]["prompt_hash"]])
                self.assertEqual((self.outages()["prompt_rejected_attempts"], self.markers()), (4, []))

    def test_other_failures_still_stop_and_name_their_capture(self):
        nan = response(status="failed", error={"code": "server_error"}).replace(b'"status"', b'"usage": {"x": NaN}, "status"')
        self.assertRegex(str(self.generate(body(nan))), "No model outcome")
        event = self.outcomes()[-1]
        self.assertEqual((event["prompt_rejected"], event["provider_response"], event["capture"]),
                         (False, None, self.capture(0, nan)))
        outcome = self.generate(lambda request: fail(http_error(400, FLAGGED)), limit=1)
        self.assertEqual(outcome["prompt_rejections"], 1)
        event = self.outcomes()[-1]
        self.assertEqual((event["prompt_rejected"], event["capture"], event["last_error"]["http_status"]), (True, None, 400))


class InspectCaptureTests(ReconcileFixture):
    """Issue 10: captures are decoded for people, read-only, without the model's text or a credential."""

    def test_captures_are_decoded_read_only(self):
        self.assertEqual(self.command("inspect-capture")[0]["captures"], [])
        failed = response(status="failed", error={"code": "server_error", "message": f"echo {KEY}"}).replace(
            b'"status"', b'"usage": {"input_tokens": NaN}, "status"')
        self.generate(body(response("by exact SECRET_PROOF_TEXT")), limit=1)
        self.assertRegex(str(self.generate(body(failed))), "No model outcome")
        # The next call is the same attempt again; its body's ID holds a lone surrogate, so the marker is kept.
        self.assertRegex(str(self.generate(body(response(id="resp_\ud835")))), "outcome is unknown")
        (self.run / "captures" / "stray.jsonl").write_text("{}\n")
        before = self.files()
        result, log = self.command("inspect-capture")
        self.assertIsInstance(result, dict, log)
        self.assertEqual(self.files(), before)
        self.assertEqual(result["unscheduled_capture_files"], ["stray.jsonl"])
        captures = {capture["attempt_id"]: capture for capture in result["captures"]}
        self.assertEqual(list(captures), [self.attempt(0), self.attempt(1)])
        self.assertEqual([(captures[self.attempt(index)]["generation_recorded"],
                           captures[self.attempt(index)]["in_flight_marker"]) for index in (0, 1)], [(True, False), (False, True)])
        recorded, = captures[self.attempt(0)]["entries"]
        self.assertEqual({key: recorded[key] for key in ("provider_response_id", "returned_model", "provider_status",
                                                         "generation_status", "recoverable", "named_by",
                                                         "recorded_as_generation")},
                         {"provider_response_id": "resp_BILLED", "returned_model": MODEL, "provider_status": "completed",
                          "generation_status": "completed", "recoverable": True, "named_by": ["response_received"],
                          "recorded_as_generation": True})
        failure, held = captures[self.attempt(1)]["entries"]
        self.assertEqual((failure["entry"], failure["error_code"], failure["reason"], failure["named_by"],
                          failure["prompt_rejected"], failure["recorded_as_generation"]),
                         (1, "server_error", "a provider failure, not a model outcome",
                          ["response_received", "no_model_outcome"], False, False))
        self.assertRegex(failure["usage"]["unstorable"], "^ValueError: Out of range float values are not JSON compliant")
        self.assertEqual((held["entry"], held["recoverable"], held["named_by"]),
                         (2, False, ["response_received", "outcome_unknown"]))
        self.assertRegex(held["provider_response_id"]["unstorable"], "^UnicodeEncodeError")
        self.assertRegex(held["reason"], "^unreadable: UnicodeEncodeError")
        # No model text and no credential, not even one a body echoes.
        self.assertNotIn("SECRET_PROOF_TEXT", json.dumps(result))
        self.assertNotIn(KEY, json.dumps(result))
        result, log = self.command("inspect-capture", "--attempt", self.attempt(1))
        self.assertEqual(([capture["attempt_id"] for capture in result["captures"]], result["unscheduled_capture_files"]),
                         ([self.attempt(1)], []))
        code, log = self.command("inspect-capture", "--attempt", "stray")
        self.assertEqual(code, 2)
        self.assertIn("Not a scheduled attempt ID", log)


if __name__ == "__main__":
    unittest.main()
