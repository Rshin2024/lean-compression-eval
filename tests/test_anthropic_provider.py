"""Anthropic Messages mapping, provider isolation, captured outcomes and offline reconciliation.

Every HTTP request and container preflight is mocked. These tests never use credentials or make paid calls.
"""
import base64
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_provider_followups import RunFixture

from lean_compression.io import digest, read_json, read_jsonl
from lean_compression.providers import generate_run, inspect_captures, parse_body, reconcile_run, response_payload
from lean_compression.store import create_run, generation_path

MODEL = "claude-sonnet-5-5"
KEY = "anthropic-unit-test-key"


def response(text="by exact h", stop="end_turn", model=MODEL, **fields):
    return json.dumps({"id": "msg_test", "type": "message", "role": "assistant", "model": model,
                       "content": [{"type": "thinking", "thinking": "private reasoning", "signature": "opaque"},
                                   {"type": "text", "text": text}],
                       "stop_reason": stop, "stop_sequence": None,
                       "usage": {"input_tokens": 73, "output_tokens": 120}, **fields}).encode()


def reply(data):
    value = io.BytesIO(data)
    value.headers = {"request-id": "anthropic-request", "x-request-id": "wrong-provider-header"}
    return value


class AnthropicProviderTests(RunFixture):
    def setUp(self):
        super().setUp()
        self.fresh("anthropic")

    def fresh(self, name):
        config = json.loads(json.dumps(self.manifest["config"]))
        config["generation"].update(provider="anthropic", model_snapshot=MODEL, reasoning_setting="high",
                                     thinking={"type": "adaptive"}, temperature=None, max_output_tokens=8192)
        self.run = self.run.parent / name
        self.manifest = create_run(self.run, self.manifest["tasks"], config, self.manifest["environment"],
                                   split="pilot", model=MODEL, development=True)
        self.slots = self.manifest["slots"]

    @contextlib.contextmanager
    def provider(self, answer):
        self.requests, self.sleeps, self.timeouts = [], [], []
        def urlopen(request, timeout=None):
            self.requests.append(request)
            self.timeouts.append(timeout)
            return answer(request)
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": KEY, "OPENAI_API_KEY": "openai-must-not-be-used"}, clear=True), \
             patch("lean_compression.providers.subprocess.run", return_value=SimpleNamespace(returncode=0)), \
             patch("lean_compression.providers.time.sleep", side_effect=self.sleeps.append), \
             patch("lean_compression.providers.urllib.request.urlopen", side_effect=urlopen):
            yield

    def generation(self):
        return read_json(generation_path(self.run, self.slots[0]["attempt_id"]))

    def transport(self):
        return read_jsonl(self.run / "transport.jsonl")

    def test_frozen_prompt_and_effort_are_mapped_and_hashed_without_tools_or_history(self):
        slot = self.slots[0]
        outcome = self.generate(lambda request: reply(response()), limit=1)
        self.assertEqual(outcome["new_generation_records"], 1)
        request, = self.requests
        self.assertEqual(request.full_url, "https://api.anthropic.com/v1/messages")
        self.assertEqual({key.lower(): value for key, value in request.header_items()}, {
            "x-api-key": KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"})
        self.assertEqual(self.timeouts, [180])
        payload = json.loads(request.data)
        self.assertEqual(payload, {"model": MODEL, "system": slot["messages"][0]["content"],
                                  "messages": [slot["messages"][1]], "max_tokens": 8192,
                                  "thinking": {"type": "adaptive"}, "output_config": {"effort": "high"}})
        self.assertEqual(self.transport()[0]["request_hash"], digest(payload))
        self.assertEqual(self.transport()[1]["request_id"], "anthropic-request")
        generation = self.generation()
        self.assertEqual(generation["raw_response"], "by exact h")
        self.assertEqual(generation["provider_response"]["content"][0]["thinking"], "private reasoning")
        self.assertEqual(generation["usage"], {"input_tokens": 73, "output_tokens": 120})
        self.assertEqual(generation["generation_settings"], self.manifest["config"]["generation"])
        capture, = read_jsonl(self.run / generation["response_capture"]["path"])
        self.assertEqual(base64.b64decode(capture["body_base64"]), response())
        self.assertEqual(capture["body_sha256"], hashlib.sha256(response()).hexdigest())

    def test_only_matching_provider_key_is_accepted_without_echoing_secrets(self):
        for environment, reason in (({"OPENAI_API_KEY": "only-openai"}, "ANTHROPIC_API_KEY is not configured"),
                                    ({"ANTHROPIC_API_KEY": "invalid secret"}, "value not shown")):
            with self.subTest(environment=list(environment)):
                with patch.dict(os.environ, environment, clear=True), \
                     patch("lean_compression.providers.urllib.request.urlopen") as http:
                    with self.assertRaisesRegex(ValueError, reason) as caught:
                        generate_run(self.run, limit=1)
                    http.assert_not_called()
                self.assertNotIn("invalid secret", str(caught.exception))
                self.assertEqual(self.markers(), [])

    def test_unresolved_thinking_or_sampling_settings_fail_before_request_or_marker(self):
        baseline = self.manifest["config"]["generation"]
        for name, value in (("thinking", None), ("reasoning_setting", None), ("temperature", 0.7),
                            ("top_p", 0.8), ("top_k", 40)):
            with self.subTest(name=name), self.assertRaises(ValueError):
                response_payload(self.slots[0], {**baseline, name: value})
        # The live path performs mapping before creating an in-flight marker.
        config = json.loads(json.dumps(self.manifest["config"]))
        config["generation"]["temperature"] = 0.7
        self.run = self.run.parent / "bad-sampling"
        self.manifest = create_run(self.run, self.manifest["tasks"], config, self.manifest["environment"],
                                   split="pilot", model=MODEL, development=True)
        outcome = self.generate(lambda request: reply(response()), limit=1)
        self.assertIsInstance(outcome, ValueError)
        self.assertEqual((self.requests, self.markers()), ([], []))
        self.assertFalse((self.run / "transport.jsonl").exists())

    def test_end_turn_max_tokens_and_refusal_preserve_native_stop_and_full_response(self):
        for stop, status in (("end_turn", "completed"), ("max_tokens", "truncated_output"), ("refusal", "refusal")):
            with self.subTest(stop=stop):
                self.fresh(stop)
                body = response(stop=stop)
                self.assertEqual(self.generate(lambda request: reply(body), limit=1)["new_generation_records"], 1)
                generation = self.generation()
                self.assertEqual((generation["generation_status"], generation["provider_status"],
                                  generation["stop_reason"], generation["provider_response"]),
                                 (status, stop, stop, json.loads(body)))
                self.assertEqual(generation["refusals"], ["by exact h"] if stop == "refusal" else [])

    def test_all_text_parts_are_kept_and_thinking_is_excluded(self):
        body = response(content=[{"type": "thinking", "thinking": "analysis"},
                                 {"type": "text", "text": "first"},
                                 {"type": "redacted_thinking", "data": "opaque"},
                                 {"type": "text", "text": "second"}])
        self.assertEqual(parse_body(body, MODEL, "anthropic")["raw_response"], "first\nsecond")

    def test_different_model_is_recorded_and_stops_further_calls(self):
        result = self.generate(lambda request: reply(response(model="claude-opus-5-5")))
        self.assertRegex(str(result), "different model snapshot")
        generation = self.generation()
        self.assertEqual((generation["generation_status"], generation["returned_model"],
                          generation["model_version_mismatch"]), ("model_version_mismatch", "claude-opus-5-5", True))
        self.assertEqual((len(self.requests), self.markers()), (1, []))

    def test_unknown_response_never_retries_and_keeps_exact_capture(self):
        cases = [b"not JSON", b"{}", response(stop="tool_use"), response(stop=None), response(content={}),
                 response(content=[{"type": "text", "text": 5}]), response(content=[{"type": "tool_use"}]),
                 response(usage={"output_tokens": float("nan")}), response(id="msg_\ud835")]
        for index, body in enumerate(cases):
            with self.subTest(index=index):
                self.fresh(f"unknown-{index}")
                self.assertRegex(str(self.generate(lambda request: reply(body))), "outcome is unknown")
                self.assertEqual((len(self.requests), self.sleeps, len(self.markers()), self.generations()), (1, [], 1, []))
                capture, = read_jsonl(self.run / "captures" / f"{self.slots[0]['attempt_id']}.jsonl")
                self.assertEqual(base64.b64decode(capture["body_base64"]), body)
                self.assertRegex(str(self.generate(lambda request: reply(response()))), "Unresolved in-flight call")
                self.assertEqual(self.requests, [])

    def test_lone_surrogate_in_pinned_candidate_is_a_recorded_malformed_outcome(self):
        body = response("by exact \ud835")
        self.assertRegex(str(self.generate(lambda request: reply(body))), "malformed response")
        generation = self.generation()
        self.assertEqual((generation["generation_status"], generation["raw_response"], generation["stop_reason"]),
                         ("malformed_response", body.decode(), "end_turn"))
        self.assertEqual((len(self.requests), self.markers()), (1, []))
        self.assertIsNone(parse_body(response("\ud835", model="other"), MODEL, "anthropic")["generation_status"])

    def test_provider_error_is_not_a_model_failure_and_http_error_redacts_key(self):
        error_body = json.dumps({"type": "error", "error": {"type": "api_error", "message": "Unavailable"}}).encode()
        self.assertRegex(str(self.generate(lambda request: reply(error_body))), "No model outcome")
        self.assertEqual((self.generations(), self.markers()), ([], []))
        self.assertEqual(self.outcomes()[0]["provider_response"], json.loads(error_body))
        def rejected(request):
            raise urllib.error.HTTPError(request.full_url, 401, "bad key", {}, io.BytesIO(KEY.encode()))
        self.assertRegex(str(self.generate(rejected)), "configuration or access")
        self.assertEqual(len(self.requests), 1)
        self.assertNotIn(KEY, (self.run / "transport.jsonl").read_text())
        self.assertIn("[REDACTED_API_KEY]", (self.run / "transport.jsonl").read_text())

    def test_lost_response_keeps_marker_without_retry(self):
        def lost(request):
            raise TimeoutError("response read timed out")
        self.assertRegex(str(self.generate(lost)), "outcome is unknown")
        self.assertEqual((len(self.requests), self.sleeps, len(self.markers()), self.generations()), (1, [], 1, []))

    def test_overloaded_529_retries_identical_payload_with_retry_after(self):
        def overloaded_once(request):
            if len(self.requests) == 1:
                raise urllib.error.HTTPError(request.full_url, 529, "overloaded", {"Retry-After": "2"},
                                             io.BytesIO(b'{"type":"error","error":{"type":"overloaded_error"}}'))
            return reply(response())
        outcome = self.generate(overloaded_once, limit=1)
        self.assertEqual((outcome["new_generation_records"], len(self.requests), self.sleeps), (1, 2, [2.0]))
        self.assertEqual(self.requests[0].data, self.requests[1].data)
        starts = [event for event in self.transport() if event["event"] == "request_started"]
        self.assertEqual(starts[0]["request_hash"], starts[1]["request_hash"])

    def test_reconcile_dispatches_using_frozen_provider_and_preserves_evidence(self):
        body = response(stop="max_tokens")
        with self.provider(lambda request: reply(body)), \
             patch("lean_compression.providers.parse_body", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                generate_run(self.run, limit=1)
        with patch("lean_compression.providers.urllib.request.urlopen", side_effect=AssertionError("network forbidden")):
            preview = inspect_captures(self.run)["captures"][0]["entries"][0]
            self.assertEqual((preview["generation_status"], preview["stop_reason"], preview["recoverable"]),
                             ("truncated_output", "max_tokens", True))
            self.assertNotIn("private reasoning", json.dumps(preview))
            self.assertEqual(reconcile_run(self.run)["actions"], {"record": 1})
            self.assertEqual(reconcile_run(self.run, apply=True)["actions"], {"record": 1})
        generation = self.generation()
        self.assertEqual((generation["generation_status"], generation["provider_response"]),
                         ("truncated_output", json.loads(body)))
        self.assertEqual(generation["reconciliation"]["request_hash"],
                         digest(response_payload(self.slots[0], self.manifest["config"]["generation"])))
        self.assertEqual(self.markers(), [])

    def test_reconcile_does_not_recover_an_unknown_stop_or_another_snapshot(self):
        for index, body in enumerate((response(stop="pause_turn"), response(model="other"))):
            self.fresh(f"held-reconcile-{index}")
            with self.provider(lambda request: reply(body)), \
                 patch("lean_compression.providers.parse_body", side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):
                    generate_run(self.run, limit=1)
            with patch("lean_compression.providers.urllib.request.urlopen", side_effect=AssertionError("network forbidden")):
                self.assertEqual(reconcile_run(self.run, apply=True)["actions"], {"keep": 1})
            self.assertEqual((len(self.markers()), self.generations()), (1, []))


if __name__ == "__main__":
    unittest.main()
