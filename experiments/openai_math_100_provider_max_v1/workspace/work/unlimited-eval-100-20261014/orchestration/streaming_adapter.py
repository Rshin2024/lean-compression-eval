"""Versioned SSE transport adapter; does not edit the frozen core provider module.

The outbound request includes stream=true in its recorded request hash. All raw
SSE lines are saved locally before parsing; a final response is reconstructed for
the unchanged parser, capture writer, usage accounting and generation recorder.
There is no whole-request deadline. The socket timeout is an idle-read timeout.
Disconnects and incomplete streams retain the stock in-flight marker, never retry.
"""
from __future__ import annotations
import copy
import hashlib
import http.client
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import threading
import time
from types import SimpleNamespace
import urllib.error
import urllib.request
from rate_budget import RateBudget

from lean_compression import providers
from lean_compression.io import append_event, canonical, file_hash

_original_payload = providers.response_payload
_original_urlopen = urllib.request.urlopen
_original_subprocess_run = subprocess.run
_context = threading.local()
_setup_lock = threading.Lock()
_runtime_lock = threading.Lock()
_runtime_cache = {}
_installed = False
_preflight = None
_session = None
_rates = RateBudget()
_reservations = {}
_reservation_lock = threading.Lock()


def reserve_dispatch(run, slot):
    manifest = providers.load_run(run)
    settings = manifest["config"]["generation"]
    tokens = settings["max_output_tokens"] + sum(len(m["content"]) for m in slot["messages"]) // 2 + 1
    sequence = _rates.reserve(slot["model_snapshot"], settings["provider"], tokens)
    if sequence is None:
        return False
    with _reservation_lock:
        _reservations[slot["attempt_id"]] = (sequence, tokens)
    return True


def bound_json(session, descriptor):
    path = (session / descriptor["path"]).resolve()
    if not path.is_relative_to(session) or file_hash(path) != descriptor["sha256"]:
        raise ValueError("Runtime preflight artifact differs from frozen pin")
    return json.loads(path.read_text())


def configure(plan, session):
    """Bind explicit verification-runtime readiness and delivered-tier prices."""
    global _preflight, _session
    session = Path(session).resolve()
    _session = session
    if descriptor := plan.get("cost_adapter"):
        path = (session / descriptor["path"]).resolve()
        if not path.is_relative_to(session) or file_hash(path) != descriptor["sha256"]:
            raise ValueError("Cost adapter differs from launch pin")
        spec = importlib.util.spec_from_file_location("provider_max_cost_adapter", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.install()
    if preflight := plan.get("runtime_preflight"):
        amendment = bound_json(session, preflight["rebound_amendment"])
        environment = bound_json(session, amendment["verification_environment"])
        if preflight["source_amendment_sha256"] != "a9b8e3254e7f61138837e06a87ee68ea21d89802a9693671c49717ccb5e367b0":
            raise ValueError("Unexpected source runtime amendment")
        if amendment["source_core_hash"] != plan["source_hash"] or amendment["engine_hash"] != plan["engine_hash"]:
            raise ValueError("Runtime amendment changes core evaluator identity")
        from lean_compression.environment import verify_environment
        verify_environment(environment)
        fields = ("compiled_environment_hash", "lean_toolchain", "mathlib_commit", "evaluator_python_hash", "lockfile_hash")
        logical = []
        for entry in plan["runs"]:
            manifest = providers.load_run(session.parents[1] / "outputs/lean-compression-eval/runs" / entry["directory"])
            source = manifest["environment"]
            if any(source.get(k) is None or source.get(k) != environment.get(k) for k in fields):
                raise ValueError("Verification runtime changed the compiled mathematical environment")
            if not any(binding["run_hash"] == manifest["run_hash"] for binding in amendment["generation_runs"]):
                raise ValueError("New run absent from rebound verification amendment")
            logical.append({"model": entry["model"], "generation_environment_hash": source["environment_hash"],
                            "generation_image": source["docker_image"]})
        images = {entry["generation_image"] for entry in logical}
        if len(images) != 1:
            raise ValueError("Generation manifests do not share one logical mathematical environment")
        _preflight = {"generation_image": images.pop(), "verification_image": environment["docker_image"],
                      "verification_environment_hash": environment["environment_hash"], "logical_runs": logical,
                      "compared_environment_fields": fields, **preflight}


def streaming_payload(slot, settings):
    return {**_original_payload(slot, settings), "stream": True}


def cached_runtime_inspect(args, **kwargs):
    if args[:3] != ["docker", "image", "inspect"]:
        raise ValueError("Unexpected command in generation runtime preflight")
    environment = kwargs.get("env", {})
    actual_args = list(args)
    if _preflight:
        if args[-1] != _preflight["generation_image"]:
            raise ValueError("Generation runtime readiness requested an unbound image")
        actual_args[-1] = _preflight["verification_image"]
    key = (tuple(actual_args), environment.get("DOCKER_HOST"), environment.get("DOCKER_CONTEXT"))
    with _runtime_lock:
        if key not in _runtime_cache:
            result = _original_subprocess_run(actual_args, **kwargs)
            if result.returncode == 0:
                if _preflight:
                    inspected = json.loads(result.stdout)
                    if len(inspected) != 1 or inspected[0].get("Id") != _preflight["verification_image"]:
                        raise ValueError("Docker returned a different immutable verification image")
                    append_event(_session / "runtime-preflight-receipts.jsonl", {
                        "event": "actual_verification_image_inspected", **_preflight,
                        "requested_logical_generation_image": args[-1],
                        "inspected_image": inspected[0]["Id"], "docker_endpoint": environment.get("DOCKER_HOST"),
                        "generation_executes_lean": False, "claims_generation_image_present": False})
                _runtime_cache[key] = result
            return result
        return _runtime_cache[key]


def preflight():
    if not _preflight:
        raise ValueError("The new run must explicitly bind its actual verification runtime")
    result = cached_runtime_inspect(["docker", "image", "inspect", _preflight["generation_image"]],
        env=providers.docker_cli_env(), capture_output=True, timeout=30)
    if result.returncode:
        raise ValueError("The task's pinned verification image is not available; no model call made")


class AnthropicMessage:
    def __init__(self):
        self.message = None
        self.blocks = {}
        self.open_blocks = set()
        self.partial_json = {}
        self.complete = False

    def event(self, value):
        kind = value.get("type")
        if kind == "ping":
            return None
        if kind == "error":
            raise ValueError("Provider emitted an SSE error; possibly billed outcome retained")
        if kind == "message_start":
            if self.message is not None:
                raise ValueError("Duplicate SSE message_start")
            self.message = copy.deepcopy(value["message"])
            if self.message.get("content"):
                raise ValueError("Unexpected initial streamed content")
            return None
        if self.message is None:
            raise ValueError("SSE content preceded message_start")
        if kind == "content_block_start":
            index = value["index"]
            if type(index) is not int or index != len(self.blocks):
                raise ValueError("Nonsequential or duplicate content block")
            self.blocks[index] = copy.deepcopy(value["content_block"])
            self.open_blocks.add(index)
        elif kind == "content_block_delta":
            index, delta = value["index"], value["delta"]
            if index not in self.open_blocks:
                raise ValueError("Delta for a closed or missing block")
            block = self.blocks[index]
            mapping = {"text_delta": "text", "thinking_delta": "thinking", "signature_delta": "signature"}
            if delta["type"] in mapping:
                key = mapping[delta["type"]]
                block[key] = block.get(key, "") + delta[key]
            elif delta["type"] == "input_json_delta":
                self.partial_json[index] = self.partial_json.get(index, "") + delta["partial_json"]
            elif delta["type"] == "citations_delta":
                block.setdefault("citations", []).append(delta["citation"])
            else:
                raise ValueError("Unsupported SSE delta; raw evidence retained")
        elif kind == "content_block_stop":
            index = value["index"]
            if index not in self.open_blocks:
                raise ValueError("Missing or duplicate content block stop")
            if index in self.partial_json:
                self.blocks[index]["input"] = json.loads(self.partial_json[index])
            self.open_blocks.remove(index)
        elif kind == "message_delta":
            self.message.update(value.get("delta", {}))
            self.message.setdefault("usage", {}).update(value.get("usage", {}))
        elif kind == "message_stop":
            if self.open_blocks or not self.message.get("stop_reason"):
                raise ValueError("Incomplete streamed message at message_stop")
            self.message["content"] = [self.blocks[i] for i in sorted(self.blocks)]
            self.complete = True
            return self.message
        else:
            raise ValueError("Unsupported Anthropic SSE event; raw evidence retained")
        return None


def read_sse(response, raw_file, provider):
    """Read through a terminal event; every consumed byte is kept before parsing."""
    data = []
    builder = AnthropicMessage() if provider == "anthropic" else None
    last_flush = time.monotonic()
    while True:
        line = response.readline()
        if not line:
            raise http.client.IncompleteRead(b"", 1)
        raw_file.write(line)
        if time.monotonic() - last_flush >= 2:
            raw_file.flush()
            os.fsync(raw_file.fileno())
            last_flush = time.monotonic()
        if line.strip():
            if line.startswith(b"data:"):
                data.append(line[5:].lstrip(b" ").rstrip(b"\r\n"))
            continue
        if not data:
            continue
        payload = b"\n".join(data)
        data.clear()
        if payload == b"[DONE]":
            raise ValueError("SSE ended without an authoritative terminal response")
        event = json.loads(payload)
        if provider == "openai":
            if event.get("type") in {"response.completed", "response.incomplete", "response.failed"}:
                result = event.get("response")
                if not isinstance(result, dict):
                    raise ValueError("Terminal SSE event has no response")
                return result
            if event.get("type") == "error":
                raise ValueError("Provider emitted SSE error; outcome remains uncertain")
        elif result := builder.event(event):
            return result


class StreamResponse:
    def __init__(self, response, context):
        self.response, self.context = response, context
        self.headers = response.headers

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.response.close()

    def read(self):
        run, slot = self.context["run"], self.context["slot"]
        attempt = slot["attempt_id"]
        directory = run / "stream-captures" / attempt
        directory.mkdir(parents=True, exist_ok=True)
        # Each transport call gets a distinct exclusive file. Unknown evidence
        # never gets overwritten, even after separately approved reconciliation.
        raw_path = directory / f"{time.time_ns()}.sse"
        descriptor = {"attempt_id": attempt, "path": str(raw_path.relative_to(run)),
                      "provider": self.context["provider"], "streaming": True,
                      "headers": {k: v for k, v in self.headers.items() if k.lower() in {"request-id", "x-request-id", "content-type", "retry-after"}
                                  or "ratelimit" in k.lower()}, "complete": False,
                      "started_at": time.time()}
        append_event(run / "stream-transport.jsonl", {**descriptor, "event": "stream_opened"})
        result = None
        with raw_path.open("xb") as raw_file:
            try:
                if "text/event-stream" not in self.headers.get("content-type", "").lower():
                    raise ValueError("Streaming request did not receive an SSE response")
                result = read_sse(self.response, raw_file, self.context["provider"])
                output = canonical(result)
                descriptor.update(complete=True, response_sha256=hashlib.sha256(output).hexdigest())
                return output
            finally:
                raw_file.flush()
                os.fsync(raw_file.fileno())
                descriptor.update(finished_at=time.time(), bytes=raw_file.tell())
                descriptor["sha256"] = file_hash(raw_path)
                append_event(run / "stream-transport.jsonl", {**descriptor, "event": "stream_closed"})


def streaming_urlopen(request, timeout):
    try:
        response = _original_urlopen(request, timeout=timeout)
    except urllib.error.HTTPError as error:
        context = _context.value
        _rates.headers(context["slot"]["model_snapshot"], context["provider"], error.headers or {}, context["sequence"], context["reservation_tokens"])
        raise  # Explicit HTTP rejections retain stock response capture/retry policy.
    except urllib.error.URLError as error:
        # A connection exception alone does not prove the paid request was unsent.
        raise OSError("Streaming connection failed; request outcome is uncertain") from error
    context = _context.value
    _rates.headers(context["slot"]["model_snapshot"], context["provider"], response.headers, context["sequence"], context["reservation_tokens"])
    return StreamResponse(response, context)


def install():
    global _installed
    with _setup_lock:
        if _installed:
            return
        providers.response_payload = streaming_payload
        providers.urllib = SimpleNamespace(request=SimpleNamespace(Request=urllib.request.Request, urlopen=streaming_urlopen),
                                           error=urllib.error)
        providers.subprocess = SimpleNamespace(run=cached_runtime_inspect, TimeoutExpired=subprocess.TimeoutExpired)
        _installed = True


def generate_run(run, *, limit=None, retry_rejected=False, shard_index=0, shard_count=1):
    install()
    manifest = providers.load_run(run)
    selected = manifest["slots"][shard_index::shard_count]
    if len(selected) != 1 or limit != 1 or retry_rejected:
        raise ValueError("Streaming controller requires exactly one frozen attempt per worker")
    with _reservation_lock:
        sequence, tokens = _reservations.pop(selected[0]["attempt_id"], (0, manifest["config"]["generation"]["max_output_tokens"]))
    _context.value = {"run": Path(run), "slot": selected[0], "provider": manifest["config"]["generation"]["provider"],
                      "sequence": sequence, "reservation_tokens": tokens}
    try:
        return providers.generate_run(run, limit=1, shard_index=shard_index, shard_count=shard_count)
    finally:
        del _context.value
