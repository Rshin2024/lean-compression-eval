"""Offline transport and repeat-call safety checks. No provider/network calls."""
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).parent


def module(name):
    spec = importlib.util.spec_from_file_location(name, HERE / (name + ".py"))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


c = module("generate_parallel")
s = module("streaming_adapter")
r = module("rate_budget")
p = module("run_fast_evaluation")


def wire(events):
    return b"".join(b"event: " + e["type"].encode() + b"\ndata: " + json.dumps(e).encode() + b"\n\n" for e in events)


def anthropic_events():
    return [
        {"type": "message_start", "message": {"id": "msg_test", "type": "message", "role": "assistant", "model": "test-model",
         "content": [], "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 13, "output_tokens": 1}}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "Reasoning"}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": "signed"}},
        {"type": "content_block_stop", "index": 0},
        {"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "by trivial"}},
        {"type": "content_block_stop", "index": 1},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None}, "usage": {"output_tokens": 129}},
        {"type": "message_stop"}]


class StreamingTests(unittest.TestCase):
    def parse(self, events, provider):
        data = wire(events)
        with tempfile.TemporaryFile() as saved:
            result = s.read_sse(io.BytesIO(data), saved, provider)
            saved.seek(0)
            self.assertEqual(saved.read(), data)
        return result

    def test_anthropic_thinking_text_signature_usage(self):
        result = self.parse(anthropic_events(), "anthropic")
        self.assertEqual(result["usage"], {"input_tokens": 13, "output_tokens": 129})
        self.assertEqual(result["content"][0], {"type": "thinking", "thinking": "Reasoning", "signature": "signed"})
        self.assertEqual(result["content"][1]["text"], "by trivial")
        self.assertEqual(s.providers.parse_anthropic_body(result, json.dumps(result).encode(), "test-model")["generation_status"], "completed")

    def test_openai_authoritative_terminal_response(self):
        final = {"id": "resp_x", "status": "completed", "output": [], "usage": {"output_tokens": 500}}
        result = self.parse([{"type": "response.output_text.delta", "delta": "earlier text"},
                             {"type": "response.completed", "response": final}], "openai")
        self.assertEqual(result, final)

    def test_openai_native_limit_is_preserved(self):
        final = {"status": "incomplete", "output": [], "incomplete_details": {"reason": "max_output_tokens"}}
        self.assertEqual(self.parse([{"type": "response.incomplete", "response": final}], "openai"), final)

    def test_partial_stream_never_becomes_completed(self):
        with tempfile.TemporaryFile() as saved:
            with self.assertRaises(Exception):
                s.read_sse(io.BytesIO(wire(anthropic_events()[:-1])), saved, "anthropic")
            self.assertGreater(saved.tell(), 0)

    def test_unknown_delta_retains_uncertainty(self):
        events = anthropic_events()
        events[2]["delta"]["type"] = "new_delta_type"
        with self.assertRaises(ValueError):
            self.parse(events, "anthropic")

    def test_message_stop_requires_all_blocks_closed(self):
        with self.assertRaises(ValueError):
            self.parse(anthropic_events()[:3] + [{"type": "message_stop"}], "anthropic")

    def test_stream_error_is_not_a_model_answer(self):
        for provider in ("openai", "anthropic"):
            with self.assertRaises(ValueError):
                self.parse([{"type": "error", "error": {"message": "overloaded"}}], provider)

    def test_payload_cap_effort_and_stream_hash(self):
        slot = {"model_snapshot": "test-model", "messages": [{"role": "system", "content": "system"}, {"role": "user", "content": "proof"}]}
        for provider in ("openai", "anthropic"):
            settings = {"provider": provider, "max_output_tokens": 128000, "reasoning_setting": "high", "thinking": {"type": "adaptive"}}
            result = s.streaming_payload(slot, settings)
            self.assertTrue(result["stream"])
            self.assertEqual(result["max_tokens" if provider == "anthropic" else "max_output_tokens"], 128000)
            self.assertEqual(result["output_config" if provider == "anthropic" else "reasoning"]["effort"], "high")


class SafetyTests(unittest.TestCase):
    def test_parent_resume_requires_fresh_hash_bound_explicit_receipt(self):
        with tempfile.TemporaryDirectory() as root:
            plan = Path(root) / "launch.json"
            plan.write_text(json.dumps({"experiment": "new"}))
            control = {"ready": True, "nonce": "0123456789abcdef", "issued_at": 11,
                       "launch_sha256": p.sha(plan), "experiment": "new"}
            self.assertTrue(p.valid_resume(control, plan, 10))
            self.assertFalse(p.valid_resume(control, plan, 12))
            control["launch_sha256"] = "wrong"
            self.assertFalse(p.valid_resume(control, plan, 10))

    def test_parent_refuses_unpinned_or_modified_child(self):
        with self.assertRaises(ValueError):
            p.validate_sources({})
        with self.assertRaises(ValueError):
            p.validate_sources({"operational_source_hashes": {"orchestration/generate_parallel.py": "wrong"}})

    def test_workspace_selection_rejects_conflicting_inherited_value(self):
        with patch.dict(c.os.environ, {"ANTHROPIC_WORKSPACE_ID": "wrkspc_other"}):
            with self.assertRaises(ValueError):
                c.configure_environment({"anthropic_workspace_id": "wrkspc_expected"})

    def test_permanent_quota_response_never_retries(self):
        with tempfile.TemporaryDirectory() as root:
            run = Path(root)
            (run / "transport.jsonl").write_text(json.dumps({"attempt_id": "a", "event": "no_model_outcome", "last_error": {
                "http_status": 429, "body": '{"error":{"type":"insufficient_quota"}}'}}) + "\n")
            self.assertIsNone(c.retry_status(run, "a"))

    def test_quota_reservation_blocks_oversubscription(self):
        gate = r.RateBudget()
        self.assertEqual(gate.reserve("model", "openai", 128000), 0)
        gate.headers("model", "openai", {"x-ratelimit-limit-tokens": "256000", "x-ratelimit-remaining-tokens": "128000",
                     "x-ratelimit-reset-tokens": "60s"}, 0, 128000)
        self.assertEqual(gate.reserve("model", "openai", 128000), 1)
        self.assertIsNone(gate.reserve("model", "openai", 128000))

    def test_older_headers_cannot_refill_newer_quota(self):
        gate = r.RateBudget()
        gate.headers("m", "openai", {"x-ratelimit-limit-tokens": "256000", "x-ratelimit-remaining-tokens": "0",
                     "x-ratelimit-reset-tokens": "60s"}, 2, 128000)
        gate.headers("m", "openai", {"x-ratelimit-limit-tokens": "256000", "x-ratelimit-remaining-tokens": "256000",
                     "x-ratelimit-reset-tokens": "60s"}, 1, 128000)
        self.assertIsNone(gate.reserve("m", "openai", 128000))

    def test_anthropic_token_limit_does_not_assume_output_reservation(self):
        gate = r.RateBudget()
        gate.headers("m", "anthropic", {"anthropic-ratelimit-requests-limit": "100", "anthropic-ratelimit-requests-remaining": "1",
                     "anthropic-ratelimit-requests-reset": "2099-01-01T00:00:00Z",
                     "anthropic-ratelimit-output-tokens-limit": "10000", "anthropic-ratelimit-output-tokens-remaining": "100"}, 0, 128000)
        self.assertIsNotNone(gate.reserve("m", "anthropic", 128000))
        self.assertIsNone(gate.reserve("m", "anthropic", 128000))

    def test_unknown_inflight_cannot_retry(self):
        with tempfile.TemporaryDirectory() as root:
            run = Path(root)
            (run / "inflight").mkdir()
            (run / "transport.jsonl").write_text(json.dumps({"attempt_id": "a", "event": "no_model_outcome", "last_error": {"http_status": 429}}) + "\n")
            self.assertEqual(c.retry_status(run, "a"), 429)
            (run / "inflight/a.json").write_text("{}")
            self.assertIsNone(c.retry_status(run, "a"))

    def test_other_attempt_event_cannot_authorize_retry(self):
        with tempfile.TemporaryDirectory() as root:
            run = Path(root)
            (run / "transport.jsonl").write_text(json.dumps({"attempt_id": "b", "event": "no_model_outcome", "last_error": {"http_status": 429}}) + "\n")
            self.assertIsNone(c.retry_status(run, "a"))

    def test_cache_refuses_changed_manifest(self):
        with tempfile.TemporaryDirectory() as root:
            run = Path(root)
            m = {"small": True}
            m["run_hash"] = c.digest(m)
            path = run / "manifest.json"
            path.write_text(json.dumps(m))
            cache = c.ManifestCache()
            cached = cache.add(run, c.file_hash(path))
            self.assertIs(cache(run), cached)
            path.write_text(json.dumps({"changed": True}))
            with self.assertRaises(ValueError):
                cache(run)

    def test_full_generation_captures_stream_and_never_repeats_saved(self):
        class Response(io.BytesIO):
            headers = {"content-type": "text/event-stream", "request-id": "req_test", "authorization": "must-not-copy"}
        with tempfile.TemporaryDirectory() as root:
            run = Path(root)
            slot = {"attempt_id": "a" * 64, "prompt_hash": "b" * 64, "model_snapshot": "test-model",
                    "messages": [{"role": "system", "content": "system"}, {"role": "user", "content": "proof"}]}
            settings = {"provider": "anthropic", "max_output_tokens": 128000, "reasoning_setting": "high", "thinking": {"type": "adaptive"},
                        "snapshot_pinned": True, "settings_resolved": True, "timeout_seconds": 600}
            manifest = {"slots": [slot], "model_snapshot": "test-model", "scheduled_attempts": 1,
                        "config": {"generation": settings, "validation": {"isolation": "docker", "docker_image": "sha256:fixture"}}}
            with patch.object(s.providers, "load_run", return_value=manifest), patch.object(c.store, "load_run", return_value=manifest), \
                 patch.object(s, "_original_urlopen", return_value=Response(wire(anthropic_events()))) as request, \
                 patch.object(s, "_original_subprocess_run", return_value=subprocess.CompletedProcess([], 0)), \
                 patch.dict(s.os.environ, {"ANTHROPIC_API_KEY": "offline-test-placeholder"}):
                first = s.generate_run(run, limit=1, shard_index=0, shard_count=1)
                second = s.generate_run(run, limit=1, shard_index=0, shard_count=1)
            self.assertEqual(request.call_count, 1)
            self.assertEqual(first["new_generation_records"], 1)
            self.assertEqual(second["new_generation_records"], 0)
            record = json.loads((run / "generations" / ("a" * 64 + ".json")).read_text())
            self.assertEqual(record["raw_response"], "by trivial")
            self.assertEqual(record["usage"]["output_tokens"], 129)
            self.assertTrue((run / record["response_capture"]["path"]).exists())
            event_text = (run / "stream-transport.jsonl").read_text()
            self.assertNotIn("must-not-copy", event_text)
            self.assertTrue(list((run / "stream-captures").rglob("*.sse")))
            self.assertFalse(list((run / "inflight").glob("*.json")))


if __name__ == "__main__":
    unittest.main()
