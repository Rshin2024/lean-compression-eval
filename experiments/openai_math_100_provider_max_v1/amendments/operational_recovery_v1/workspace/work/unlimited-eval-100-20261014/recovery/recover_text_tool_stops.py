"""Recover a saved text-only tool_use response without another provider call.

This narrow operational amendment handles the observed Messages representation:
thinking/text blocks, with a serialized <invoke> request in the returned text.
All text is preserved unchanged and sent to the ordinary frozen Lean validator.
No tool is executed, no proof is selected from the prose, and no verdict is
invented here. Other unknown terminations or structured tool blocks stay held.
"""
import argparse
import base64
import fcntl
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import time

WORK = Path(__file__).resolve().parents[1]
REPO = WORK.parents[1] / "outputs/lean-compression-eval"
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(WORK / "orchestration"))
sys.path.insert(0, str(WORK / "preparation"))
from lean_compression.io import _abandoned, _release, append_event, digest, file_hash, read_json, read_jsonl
from lean_compression.providers import unstorable
from streaming_adapter import streaming_payload as response_payload, AnthropicMessage
from lean_compression.store import load_run, record_generation, generation_path
from lean_compression.integrity import verified_generation
from lean_compression.io import evaluator_hash
import lean_compression.store as store

KIND = "captured_stream_text_termination_reconciliation_v1"
KNOWN_DIAGNOSTIC = "TypeError: the Message has an unsupported or absent stop_reason"
_PLAN_SHA = None
_RUNS = {}
_CACHE = None


def bind_plan(plan_sha256, helper_sha256):
    global _PLAN_SHA, _RUNS, _CACHE, load_run
    if file_hash(Path(__file__)) != helper_sha256 or file_hash(WORK / "launch_ready.json") != plan_sha256:
        raise ValueError("Reviewed helper or launch hash differs")
    plan = read_json(WORK / "launch_ready.json")
    if plan.get("ready") is not True or plan.get("experiment") != "openai_math_100_provider_max_v2_20261014":
        raise ValueError("Wrong or incomplete independent-run launch")
    if evaluator_hash() != plan["source_hash"] or file_hash(REPO / "lean/Evaluator.lean") != plan["engine_hash"]:
        raise ValueError("Frozen evaluator changed")
    for relative, expected in plan["operational_source_hashes"].items():
        path = (WORK / relative).resolve()
        if not path.is_relative_to(WORK) or file_hash(path) != expected:
            raise ValueError("Pinned operational source changed")
    from generate_parallel import ManifestCache
    cache, runs = ManifestCache(), {}
    for entry in plan["runs"]:
        run = (REPO / "runs" / entry["directory"]).resolve()
        if run.parent != (REPO / "runs").resolve():
            raise ValueError("Run path escaped the output directory")
        manifest = cache.add(run, entry["manifest_sha256"])
        if manifest["run_hash"] != entry["run_hash"] or manifest["model_snapshot"] != entry["model"]:
            raise ValueError("New run identity differs from launch")
        runs[entry["model"]] = (run, manifest)
    from priority_costs import install
    install()
    _PLAN_SHA, _RUNS, _CACHE = plan_sha256, runs, cache
    load_run = store.load_run = cache
    return plan


def verified_stream(run, attempt, captured):
    events = [e for e in read_events(run / "stream-transport.jsonl")[0] if e.get("attempt_id") == attempt]
    if [e.get("event") for e in events] != ["stream_opened", "stream_closed"]:
        raise ValueError("Exactly one completed stream is required")
    opened, closed = events
    relative = closed.get("path")
    if not isinstance(relative, str) or re.fullmatch(rf"stream-captures/{attempt}/[0-9]+\.sse", relative) is None:
        raise ValueError("Stream capture path differs from attempt")
    path = (run / relative).resolve()
    if not path.is_relative_to(run.resolve()) or opened.get("path") != relative:
        raise ValueError("Stream path is ambiguous")
    raw = path.read_bytes()
    raw_sha = hashlib.sha256(raw).hexdigest()
    if (closed.get("complete") is not True or closed.get("sha256") != raw_sha or closed.get("bytes") != len(raw)
            or closed.get("response_sha256") != captured["body_sha256"]
            or opened.get("provider") != "anthropic" or closed.get("provider") != "anthropic"):
        raise ValueError("Complete stream/hash binding differs")
    for event in events:
        headers = {k.lower(): v for k, v in event.get("headers", {}).items()}
        if headers.get("request-id") != captured["request_id"]:
            raise ValueError("Stream and response request IDs differ")
    builder, data, event_name, terminal = AnthropicMessage(), [], None, None
    for line in raw.splitlines():
        if line.startswith(b"event:"):
            event_name = line[6:].strip().decode()
        elif line.startswith(b"data:"):
            data.append(line[5:].lstrip(b" "))
        elif not line.strip() and data:
            value = json.loads(b"\n".join(data), object_pairs_hook=unique_object)
            if event_name is not None and event_name != value.get("type"):
                raise ValueError("SSE event label differs from payload")
            if terminal is not None:
                raise ValueError("Unexpected events after terminal message_stop")
            terminal = builder.event(value)
            data, event_name = [], None
    if data or terminal is None or builder.complete is not True:
        raise ValueError("Stream lacks complete final message_stop")
    if digest(terminal) != captured["body_sha256"]:
        raise ValueError("Reconstructed SSE response differs from capture")
    return {"stream_evidence_hash": digest(events), "stream_file_sha256": raw_sha,
            "stream_file_path": relative, "stream_response_sha256": captured["body_sha256"],
            "stream_terminal_event": "message_stop"}


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError("Missing timestamp")
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError("Timestamp has no time zone")
    return result


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def read_events(path):
    data = Path(path).read_bytes()
    rows = [json.loads(line, object_pairs_hook=unique_object) for line in data.splitlines() if line.strip()]
    if any(not isinstance(row, dict) or unstorable(row) is not None for row in rows):
        raise ValueError("Invalid evidence log row")
    return rows, hashlib.sha256(data).hexdigest()


def recoverable_text(response, model):
    if (not isinstance(response, dict) or response.get("type") != "message" or response.get("role") != "assistant" or
            response.get("model") != model or response.get("stop_reason") != "tool_use" or
            not isinstance(response.get("id"), str) or not response["id"]):
        raise ValueError("Not the recognized text-only tool termination")
    content = response.get("content")
    if not isinstance(content, list) or any(not isinstance(x, dict) or x.get("type") not in
            {"text", "thinking", "redacted_thinking"} for x in content):
        raise ValueError("Structured/unknown content must remain held")
    parts = [x.get("text") for x in content if x.get("type") == "text"]
    if not parts or not all(isinstance(x, str) for x in parts):
        raise ValueError("No complete returned text")
    raw = "\n".join(parts)
    if re.search(r"<invoke(?:\s|>)", raw) is None or "</invoke>" not in raw or unstorable(response) is not None:
        raise ValueError("Not the observed serialized tool-request form")
    if not isinstance(response.get("usage"), dict):
        raise ValueError("Missing captured token usage")
    return raw


def bound_evidence(run, slot, settings, marker):
    attempt = slot["attempt_id"]
    if not isinstance(marker, dict) or marker.get("attempt_id") != attempt:
        raise ValueError("Marker identifies another attempt")
    started = timestamp(marker.get("started_at"))
    captures, capture_file_sha = read_events(run / "captures" / (attempt + ".jsonl"))
    if len(captures) != 1:
        raise ValueError("Exactly one capture is required")
    captured = captures[0]
    if (captured.get("attempt_id") != attempt or type(captured.get("transport_index")) is not int
            or captured["transport_index"] != 0):
        raise ValueError("Capture attempt/index differs")
    body = base64.b64decode(captured.get("body_base64"), validate=True)
    if hashlib.sha256(body).hexdigest() != captured.get("body_sha256"):
        raise ValueError("Capture bytes do not match their hash")
    response = json.loads(body, object_pairs_hook=unique_object)
    raw = recoverable_text(response, slot["model_snapshot"])
    events = [e for e in read_events(run / "transport.jsonl")[0] if e.get("attempt_id") == attempt]
    if [e.get("event") for e in events] != ["request_started", "response_received", "outcome_unknown"]:
        raise ValueError("Ambiguous transport event sequence")
    request, received, unknown = events
    request_hash = digest(response_payload(slot, settings))
    capture = {"path": f"captures/{attempt}.jsonl", "body_sha256": captured["body_sha256"]}
    if (request.get("request_hash") != request_hash or
            any(type(e.get("transport_index")) is not int or e["transport_index"] != 0 for e in (request, received)) or
            not isinstance(captured.get("request_id"), str) or not captured["request_id"] or
            received.get("request_id") != captured["request_id"] or received.get("capture") != capture or
            unknown.get("capture") != capture or unknown.get("reason") != KNOWN_DIAGNOSTIC):
        raise ValueError("Request/response transport binding differs")
    moments = [started, timestamp(request.get("recorded_at")), timestamp(captured.get("recorded_at")),
               timestamp(received.get("recorded_at")), timestamp(unknown.get("recorded_at"))]
    if moments != sorted(moments):
        raise ValueError("Marker/request/capture/response timing differs")
    elapsed = received.get("elapsed_seconds")
    if type(elapsed) not in (int, float) or not math.isfinite(elapsed) or elapsed < 0:
        raise ValueError("Invalid captured request duration")
    stream = verified_stream(run, attempt, captured)
    return {"response": response, "raw": raw, "capture": capture, "request_hash": request_hash, **stream,
            "capture_file_sha256": capture_file_sha, "transport_evidence_hash": digest(events),
            "transport_index": 0, "request_id": captured["request_id"], "captured_at": captured["recorded_at"],
            "unknown_recorded_at": unknown["recorded_at"], "elapsed_seconds": elapsed}


def check_existing(generation, slot, settings, evidence, marker):
    verified_generation(generation, slot)
    response = evidence["response"]
    expected = {"raw_response": evidence["raw"], "generation_status": "completed", "returned_model": response["model"],
        "provider_response_id": response["id"], "usage": response["usage"], "provider_response": response,
        "response_capture": evidence["capture"], "generation_settings": settings,
        "generation_elapsed_seconds": evidence["elapsed_seconds"], "stop_reason": "tool_use",
        "provider_status": "tool_use", "model_tool_calls": 0}
    if any(digest(generation.get(k)) != digest(v) for k, v in expected.items()):
        raise ValueError("Existing immutable generation differs from captured evidence")
    amendment = generation.get("capture_reconciliation")
    if not isinstance(amendment, dict) or amendment.get("kind") != KIND:
        raise ValueError("Existing generation is not this narrow reconciliation")
    expected = {"attempt_id": slot["attempt_id"], "marker": marker, "request_hash": evidence["request_hash"],
        "launch_plan_sha256": _PLAN_SHA,
        "raw_response_hash": digest(evidence["raw"]), "capture": evidence["capture"],
        "script_sha256": file_hash(Path(__file__)),
        "resolution": "preserved_full_text_for_frozen_single_shot_verification", "provider_stop_reason": "tool_use",
        "new_model_calls": 0, "tools_executed": 0, "text_changed": False, "proof_selection_or_repair": False}
    if any(digest(amendment.get(k)) != digest(v) for k, v in expected.items()):
        raise ValueError("Existing reconciliation provenance differs")
    if any(amendment.get(k) != evidence[k] for k in
            ("capture_file_sha256", "transport_evidence_hash", "transport_index", "request_id", "captured_at",
             "stream_evidence_hash", "stream_file_sha256", "stream_file_path", "stream_response_sha256", "stream_terminal_event")):
        raise ValueError("Existing reconciliation evidence hashes differ")


def recover(run, attempt, apply):
    run = Path(run)
    if apply and (_PLAN_SHA is None or run.resolve() not in {r for r, m in _RUNS.values()}):
        raise ValueError("Apply requires the reviewed new-run plan binding")
    if not isinstance(attempt, str) or re.fullmatch(r"[0-9a-f]{64}", attempt) is None:
        raise ValueError("Invalid scheduled attempt ID")
    manifest = load_run(run)
    slots = [s for s in manifest["slots"] if s["attempt_id"] == attempt]
    if len(slots) != 1:
        raise ValueError("Attempt is not uniquely scheduled")
    slot = slots[0]
    settings = manifest["config"]["generation"]
    if settings["provider"] != "anthropic" or settings["mode"] != "single_shot":
        raise ValueError("Wrong frozen provider/mode")
    marker = run / "inflight" / (attempt + ".json")
    target = generation_path(run, attempt)
    existing = read_json(target) if target.exists() else None
    if existing is not None:
        verified_generation(existing, slot)
    lock = _abandoned(marker, protocol=False) if marker.exists() else None
    if marker.exists() and lock is None:
        raise ValueError("Marker is still held by a live worker")
    if lock is None and existing is None:
        raise ValueError("Marker is absent")
    try:
        owner = read_json(marker) if lock is not None else (existing.get("capture_reconciliation") or {}).get("marker")
        evidence = bound_evidence(run, slot, settings, owner)
        raw, response = evidence["raw"], evidence["response"]
        capture, request_hash = evidence["capture"], evidence["request_hash"]
        # A competing recorder may have completed before this lock acquisition.
        existing = read_json(target) if target.exists() else None
        state = "reviewed"
        if existing is not None:
            check_existing(existing, slot, settings, evidence, owner)
            state = "already_recorded"
            if apply and lock is not None:
                _release(lock, marker)
                lock = None
                state = "recorded_marker_released"
        amendment = {"kind": KIND, "attempt_id": attempt,
                     "launch_plan_sha256": _PLAN_SHA,
                     "resolution": "preserved_full_text_for_frozen_single_shot_verification",
                     "provider_stop_reason": "tool_use", "capture": capture,
                     "marker": owner, "request_hash": request_hash,
                     "raw_response_hash": digest(raw), "script_sha256": file_hash(Path(__file__)),
                     "new_model_calls": 0, "tools_executed": 0,
                     "text_changed": False, "proof_selection_or_repair": False,
                     **{k: evidence[k] for k in ("capture_file_sha256", "transport_evidence_hash",
                                                "transport_index", "request_id", "captured_at", "stream_evidence_hash",
                                                "stream_file_sha256", "stream_file_path", "stream_response_sha256", "stream_terminal_event")},
                     "note": "A captured single-shot API response exists. Preserve all returned text, including prose and serialized tool request, and let the unchanged validator decide its outcome."}
        if apply and existing is None:
            append_event(run / "amendments.jsonl", amendment, once=True)
            record_generation(run, slot, raw, generation_status="completed",
                provider_response_id=response.get("id"), returned_model=response["model"],
                usage=response["usage"], provider_status="tool_use", stop_reason="tool_use",
                stop_sequence=response.get("stop_sequence"), incomplete_details=None, refusals=[],
                provider_response=response, response_capture=capture, generation_settings=settings,
                generation_elapsed_seconds=evidence["elapsed_seconds"],
                capture_reconciliation=amendment, model_tool_calls=0)
            _release(lock, marker)
            lock = None
            state = "recorded"
        return {"attempt": attempt, "state": state, "evidence_verified": True,
                "returned_text_chars": len(raw), "raw_response_hash": digest(raw), "capture": capture,
                "request_hash": request_hash, "transport_evidence_hash": evidence["transport_evidence_hash"],
                "stream_file_sha256": evidence["stream_file_sha256"], "stream_file_path": evidence["stream_file_path"],
                "unknown_recorded_at": evidence["unknown_recorded_at"],
                "generation_sha256": file_hash(target) if target.exists() else None,
                "new_model_calls": 0, "tools_executed": 0}
    finally:
        if lock is not None:
            os.close(lock)


def scan(apply=False):
    status = read_json(WORK / "generation-status.json")
    if not -5 <= time.time() - status.get("updated_at", 0) <= 90:
        raise ValueError("Generation status is stale")
    actions, held = [], []
    for key, block in status.get("blocked_attempts", {}).items():
        try:
            model, raw_index = key.rsplit("/", 1)
            index = int(raw_index)
            run, manifest = _RUNS[model]
            if index < 0 or index >= len(manifest["slots"]):
                raise ValueError("Unknown frozen attempt index")
            attempt = manifest["slots"][index]["attempt_id"]
            if block.get("attempt_id") != attempt or KNOWN_DIAGNOSTIC not in str(block.get("error")):
                raise ValueError("Blocked attempt has another unknown outcome")
            actions.append({"slot": key, **recover(run, attempt, apply)})
        except (ValueError, KeyError, TypeError, OSError) as error:
            held.append({"slot": key, "reason": str(error)})
    return {"kind": "captured_stream_text_recovery_scan_v1", "apply": apply,
            "launch_plan_sha256": _PLAN_SHA, "helper_sha256": file_hash(Path(__file__)),
            "actions": actions, "held": held, "new_model_calls": 0, "tools_executed": 0,
            "generation_complete": status.get("state") == "complete" and status.get("generation_total") == 9600}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--plan-sha256", required=True)
    p.add_argument("--helper-sha256", required=True)
    p.add_argument("--apply", action="store_true")
    p.add_argument("--watch", action="store_true")
    p.add_argument("--interval-seconds", type=int, default=15)
    args = p.parse_args()
    if not 5 <= args.interval_seconds <= 60:
        raise ValueError("Interval must be between 5 and 60 seconds")
    # This isolated recovery process has no use for provider credentials.
    for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        os.environ.pop(name, None)
    bind_plan(args.plan_sha256, args.helper_sha256)
    with (WORK / "recovery/text-stop-watch.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        while True:
            result = scan(args.apply)
            print(json.dumps(result, sort_keys=True), flush=True)
            if args.apply:
                append_event(WORK / "recovery/text-stop-watch-events.jsonl", result)
            if not args.watch or result["generation_complete"]:
                return
            time.sleep(args.interval_seconds)


if __name__ == "__main__":
    main()
