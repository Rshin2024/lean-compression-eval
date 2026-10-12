"""Provider follow-ups: rejected prompts, HTTP-date Retry-After, storable imports, and the runtime preflight.

The HTTP layer, the docker CLI and Lean are mocked; no network, model, Docker daemon or
Lean runtime is used.
"""
import contextlib
import email.utils
import io
import json
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import urllib.error

from lean_compression import cli
from lean_compression.analysis import analyze
from lean_compression.io import digest, file_hash, read_json, read_jsonl
from lean_compression.providers import generate_run, import_responses, retry_after_seconds
from lean_compression.runner import docker_cli_env, evaluate_run
from lean_compression.store import create_run, records

PROJECT = Path(__file__).resolve().parents[1]
ENGINE = PROJECT / "lean/Evaluator.lean"
MODEL = "pinned-model-2026-01-01"
IMAGE = "sha256:" + "0" * 64
FLAGGED = json.dumps({"error": {"message": "Invalid prompt: your prompt was flagged as potentially violating our "
                                "usage policy.", "type": "invalid_request_error", "param": None, "code": "invalid_prompt"}})


def http_error(code, body="{}", headers=None):
    return urllib.error.HTTPError("https://api.openai.com/v1/responses", code, "error", headers or {},
                                  io.BytesIO(body.encode()))


def reply(status="completed", **fields):
    output = [{"type": "message", "content": [{"type": "output_text", "text": "by exact h"}]}] if status == "completed" else []
    value = io.BytesIO(json.dumps({"id": "mock-response", "model": MODEL, "status": status, "output": output,
                                   **fields}).encode())
    value.headers = {}
    return value


def fail(error):
    raise error


class RunFixture(unittest.TestCase):
    """Two tasks, so 24 scheduled slots; the four samples of one task and condition share a prompt."""

    def setUp(self):
        config = read_json(PROJECT / "configs/pilot.json")
        config["validation"].update(isolation="docker", docker_image=IMAGE)
        config["generation"].update(snapshot_pinned=True, settings_resolved=True)
        env = {"environment_hash": "fixture", "lean_toolchain": config["environment"]["lean_toolchain"],
               "mathlib_commit": config["environment"]["mathlib_commit"], "hash_mode": "development_metadata_only",
               "runtime_backend": "docker", "docker_image": IMAGE,
               "source_exclusion_audit": {"lean_sources": 0, "git_directories": 0}}
        tasks = [{"task_id": name, "family_id": "family_" + name, "domain": "sets_logic", "split": "pilot",
                  "origin": "fresh", "target_name": "target_" + name, "binders": "(P : Prop) (h : P)",
                  "proposition": "P", "imports": ["Init"], "premise_context": "", "context": ""} for name in ("one", "two")]
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.run = Path(temp.name) / "run"
        self.manifest = create_run(self.run, tasks, config, env, split="pilot", model=MODEL, development=True)
        self.slots = self.manifest["slots"]

    @contextlib.contextmanager
    def provider(self, answer):
        """Patch the docker preflight and HTTP layer; answer(request) returns a reply or raises."""
        self.requests, self.sleeps = [], []
        def urlopen(request, timeout=None):
            self.requests.append(request)
            return answer(request)
        with patch.dict(os.environ, {"OPENAI_API_KEY": "unit-test-only"}), \
             patch("lean_compression.providers.subprocess.run", return_value=SimpleNamespace(returncode=0)), \
             patch("lean_compression.providers.time.sleep", side_effect=self.sleeps.append), \
             patch("lean_compression.providers.urllib.request.urlopen", side_effect=urlopen):
            yield

    def generate(self, answer, limit=None, **options):
        with self.provider(answer):
            try:
                return generate_run(self.run, limit=limit, **options)
            except (RuntimeError, ValueError) as error:
                return error

    def generations(self):
        return sorted((self.run / "generations").glob("*.json"))

    def markers(self):
        return sorted((self.run / "inflight").glob("*.json"))

    def outcomes(self):
        return [event for event in read_jsonl(self.run / "transport.jsonl") if event["event"] == "no_model_outcome"]


class PromptRejectionTests(RunFixture):
    def setUp(self):
        super().setUp()
        self.prompt = next(slot["prompt_hash"] for slot in self.slots
                           if slot["task_id"] == "two" and slot["condition"] == "standard")
        self.flagged = {slot["attempt_id"] for slot in self.slots if slot["prompt_hash"] == self.prompt}
        self.assertEqual(len(self.flagged), 4)

    def rejecting(self, rejection):
        """The provider rejects only the flagged prompt and answers every other one."""
        def answer(request):
            return rejection() if digest(json.loads(request.data)["input"]) == self.prompt else reply()
        return answer

    def check_rejected_prompt_stays_pending_while_the_run_continues(self, rejection):
        outcome = self.generate(self.rejecting(rejection))
        # Round 4: the prompt's first slot is rejected, and its three other samples are passed over without a request.
        self.assertEqual(outcome, {"new_generation_records": 20, "prompt_rejections": 1, "skipped_rejected_prompt_slots": 3,
                                   "scheduled_attempts": 24})
        self.assertEqual((len(self.requests), self.sleeps, self.markers()), (21, [], []))
        rows = {row["attempt_id"]: row for row in records(self.run)}
        self.assertEqual({key for key, row in rows.items() if row["validity_status"] == "not_generated"}, self.flagged)
        self.assertTrue(all(rows[key]["scheduled_outcome_incomplete"] for key in self.flagged))
        logged = self.outcomes()
        first = next(slot["attempt_id"] for slot in self.slots if slot["attempt_id"] in self.flagged)
        self.assertEqual([event["attempt_id"] for event in logged], [first])
        self.assertTrue(all(event["prompt_rejected"] for event in logged))
        # Lean checks the other twenty; the rejections stay unresolved, never model failures.
        engine_hash = file_hash(ENGINE)
        verdict = lambda *args, **kwargs: {"validity_status": "success", "metric_status": "available",
                                           "engine_hash": engine_hash, "validation": {"validity_status": "success"},
                                           "metrics": {"metric_status": "available", "W_S": 1, "W_B": 3,
                                                       "U_S_decimal": "1", "U_B_decimal": "7", "D": 2}}
        with patch("lean_compression.runner.evaluate_proof", side_effect=verdict):
            self.assertEqual(evaluate_run(self.run, ENGINE)["new_evaluations"], 20)
        config = self.manifest["config"]
        config = {**config, "analysis": {**config["analysis"], "bootstrap_replicates": 0}}
        model = analyze(self.manifest["tasks"], records(self.run), {}, config)["models"][MODEL]
        self.assertFalse(model["run_complete"])
        self.assertFalse(model["correctness_conclusions_ready"])
        self.assertEqual(model["conditions"]["standard"]["failure_breakdown"],
                         {"model_failures": {}, "unresolved_outcomes": {"not_generated": 4}})
        # Round 4: a rerun remembers the rejection and sends none of those slots. Asked with retry_rejected, it sends
        # them again, and once the provider answers, they complete.
        outcome = self.generate(self.rejecting(rejection))
        self.assertEqual(outcome, {"new_generation_records": 0, "prompt_rejections": 0, "skipped_rejected_prompt_slots": 4,
                                   "scheduled_attempts": 24})
        self.assertEqual((len(self.requests), self.markers(), len(self.generations())), (0, [], 20))
        outcome = self.generate(lambda request: reply(), retry_rejected=True)
        self.assertEqual((outcome["new_generation_records"], outcome["prompt_rejections"], len(self.requests)), (4, 0, 4))
        self.assertEqual(len(self.generations()), 24)

    def test_http_400_invalid_prompt_leaves_its_slots_pending(self):
        self.check_rejected_prompt_stays_pending_while_the_run_continues(lambda: fail(http_error(400, FLAGGED)))

    def test_failed_response_with_invalid_prompt_leaves_its_slots_pending(self):
        self.check_rejected_prompt_stays_pending_while_the_run_continues(
            lambda: reply("failed", error={"code": "invalid_prompt", "message": "Invalid prompt: flagged"}))

    def test_rejections_use_up_the_limit(self):
        # Rejected prompts were requests too, so --limit bounds them like generations (round 3).
        first = next(index for index, slot in enumerate(self.slots) if slot["attempt_id"] in self.flagged)
        outcome = self.generate(self.rejecting(lambda: fail(http_error(400, FLAGGED))), limit=first + 1)
        self.assertEqual((outcome["new_generation_records"], outcome["prompt_rejections"]), (first, 1))
        self.assertEqual(len(self.requests), first + 1)

    def test_cli_reports_rejections_and_exits_normally(self):
        stdout = io.StringIO()
        with self.provider(self.rejecting(lambda: fail(http_error(400, FLAGGED)))), contextlib.redirect_stdout(stdout):
            cli.main(["generate", "--run", str(self.run)])
        result = json.loads(stdout.getvalue())
        self.assertEqual((result["prompt_rejections"], result["skipped_rejected_prompt_slots"]), (1, 3))  # round 4

    def test_configuration_access_and_other_failures_still_stop_every_call(self):
        for label, failure, calls, message in (
                ("400 unsupported parameter", lambda: fail(http_error(400, '{"error": {"code": "unsupported_parameter"}}')),
                 1, "rejected the configuration"),
                ("400 without a code", lambda: fail(http_error(400, '{"error": {"message": "bad"}}')), 1, "rejected the configuration"),
                ("400 unreadable body", lambda: fail(http_error(400, "<html>bad request</html>")), 1, "rejected the configuration"),
                ("400 deeply nested body", lambda: fail(http_error(400, "[" * 65536)), 1, "rejected the configuration"),
                ("401 naming invalid_prompt", lambda: fail(http_error(401, FLAGGED)), 1, "rejected the configuration"),
                ("403 naming invalid_prompt", lambda: fail(http_error(403, FLAGGED)), 1, "rejected the configuration"),
                ("503 naming invalid_prompt", lambda: fail(http_error(503, FLAGGED)), 3, "No model outcome"),
                ("failed server_error", lambda: reply("failed", error={"code": "server_error"}), 1, "No model outcome"),
                ("failed without an error", lambda: reply("failed"), 1, "No model outcome"),
                ("cancelled naming invalid_prompt", lambda: reply("cancelled", error={"code": "invalid_prompt"}), 1,
                 "No model outcome")):
            with self.subTest(label):
                outcome = self.generate(lambda request: failure())
                self.assertIsInstance(outcome, RuntimeError)
                self.assertRegex(str(outcome), message)
                self.assertEqual(len(self.requests), calls)
                self.assertEqual((self.generations(), self.markers()), ([], []))
                self.assertFalse(self.outcomes()[-1]["prompt_rejected"])


class RetryAfterDateTests(RunFixture):
    def throttled(self, value, then=None):
        """A 429 carrying Retry-After twice and then a reply; every time when there is no reply."""
        def answer(request):
            if then is None or len(self.requests) <= 2:
                raise http_error(429, headers={"Retry-After": value})
            return then()
        return answer

    def test_retry_after_forms(self):
        now = time.time()
        # GMT, -0000 and zoneless asctime dates are all UTC; read them where local time is not UTC.
        try:
            with patch.dict(os.environ, {"TZ": "IST-5:30"}):
                time.tzset()
                ahead = [retry_after_seconds(value) for value in (email.utils.formatdate(now + 30, usegmt=True),
                         email.utils.formatdate(now + 30), time.asctime(time.gmtime(now + 30)))]
        finally:
            time.tzset()
        self.assertTrue(all(25 < delay <= 30 for delay in ahead), ahead)
        self.assertGreater(retry_after_seconds(email.utils.formatdate(now + 7200, usegmt=True)), 7000)
        for value, delay in (("7", 7), ("0", 0), (None, 0), ("", 0), ("soon", 0), ("Mon, 12 Oct 99999 10:17:05 GMT", 0),
                             ("Mon, 12 Oct " + "9" * 30 + " 10:17:05 GMT", 0),
                             (email.utils.formatdate(now - 3600, usegmt=True), 0)):
            self.assertEqual(retry_after_seconds(value), delay, value)

    def test_http_date_is_honored_within_the_budget(self):
        outcome = self.generate(self.throttled(email.utils.formatdate(time.time() + 30, usegmt=True), then=reply), limit=1)
        self.assertEqual(outcome["new_generation_records"], 1)
        self.assertEqual(len(self.requests), 3)
        self.assertEqual(len(self.sleeps), 2)
        self.assertTrue(all(25 < delay <= 30 for delay in self.sleeps), self.sleeps)

    def test_http_date_beyond_the_budget_stops_at_once(self):
        outcome = self.generate(self.throttled(email.utils.formatdate(time.time() + 7200, usegmt=True)))
        self.assertIsInstance(outcome, RuntimeError)
        self.assertEqual((len(self.requests), self.sleeps, self.generations(), self.markers()), (1, [], [], []))
        self.assertRegex(read_jsonl(self.run / "transport.jsonl")[-2]["retry_after"], "GMT$")

    def test_past_or_unreadable_dates_use_the_fixed_backoff(self):
        for value in (email.utils.formatdate(time.time() - 3600, usegmt=True), "soon"):
            with self.subTest(value):
                outcome = self.generate(self.throttled(value))
                self.assertIsInstance(outcome, RuntimeError)
                self.assertEqual((len(self.requests), self.sleeps), (3, [1, 4]))


class StorableImportTests(RunFixture):
    def row(self, slot, **changes):
        return {"attempt_id": slot["attempt_id"], "prompt_hash": slot["prompt_hash"],
                "model_snapshot": slot["model_snapshot"], "raw_response": "by exact h", **changes}

    def import_file(self, lines):
        path = self.run.parent / "responses.jsonl"
        path.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                cli.main(["import-responses", "--run", str(self.run), "--responses", str(path)])
            except SystemExit as stopped:
                return stopped.code, stderr.getvalue()
        return json.loads(stdout.getvalue()), stderr.getvalue()

    def test_a_row_json_evidence_cannot_hold_rejects_the_whole_file(self):
        good = [json.dumps(self.row(slot, usage={"output_tokens": 3})) for slot in self.slots[:2]]
        slot = self.slots[2]
        # Python's json module reads and writes NaN, Infinity and lone-surrogate escapes in JSONL.
        for label, line in (
                ("NaN usage", json.dumps(self.row(slot, usage={"output_tokens": float("nan")}))),
                ("Infinity setting", json.dumps(self.row(slot, generation_settings={"temperature": float("inf")}))),
                ("-Infinity provenance", json.dumps(self.row(slot, provenance={"latency": float("-inf")}))),
                ("lone surrogate in raw_response", json.dumps(self.row(slot, raw_response="by exact h \ud83d"))),
                ("lone surrogate in provenance", json.dumps(self.row(slot, provenance={"note": "cut \udc00"}))),
                ("lone surrogate in a usage key", json.dumps(self.row(slot, usage={"\ud800": 1})))):
            with self.subTest(label):
                code, log = self.import_file([*good, line])
                self.assertEqual(code, 2)
                self.assertIn("Import row 3 cannot be stored as JSON evidence", log)
                self.assertIn(slot["attempt_id"], log)
                self.assertEqual(self.generations(), [])
        self.assertEqual(self.import_file(good)[0], {"imported": 2, "already_imported": 0})
        code, log = self.import_file([*good, json.dumps(self.row(slot, raw_response="by exact h \ud83d"))])
        self.assertEqual((code, len(self.generations())), (2, 2))
        with self.assertRaisesRegex(ValueError, "Import row 1 cannot be stored"):
            import_responses(self.run, [self.row(self.slots[3], usage={"output_tokens": float("nan")})])
        self.assertEqual(len(self.generations()), 2)


class RuntimePreflightTests(RunFixture):
    def test_preflight_uses_the_docker_client_environment(self):
        parent = {"PATH": "/opt/docker/bin:/usr/bin:/bin", "HOME": "/home/runner",
                  "DOCKER_HOST": "unix:///run/user/1000/docker.sock", "DOCKER_CONTEXT": "rootless",
                  "OPENAI_API_KEY": "sk-unit-test-canary", "AWS_SECRET_ACCESS_KEY": "aws-unit-test-canary"}
        with patch.dict(os.environ, parent, clear=True), \
             patch("lean_compression.providers.subprocess.run", return_value=SimpleNamespace(returncode=1)) as preflight, \
             patch("lean_compression.providers.urllib.request.urlopen") as request:
            with self.assertRaisesRegex(ValueError, "container runtime is not available; no model call was made"):
                generate_run(self.run)
            expected = docker_cli_env()
        request.assert_not_called()
        (command,), options = preflight.call_args
        self.assertEqual(command, ["docker", "image", "inspect", IMAGE])
        self.assertEqual(options["env"], expected)
        self.assertEqual((options["env"]["DOCKER_HOST"], options["env"]["DOCKER_CONTEXT"]),
                         (parent["DOCKER_HOST"], parent["DOCKER_CONTEXT"]))
        self.assertFalse({"OPENAI_API_KEY", "AWS_SECRET_ACCESS_KEY"} & set(options["env"]))
        self.assertFalse((self.run / "inflight").exists())


if __name__ == "__main__":
    unittest.main()
