"""Recover a saved text-only tool_use response without another provider call.

This narrow operational amendment handles the observed Messages representation:
thinking/text blocks, with a serialized <invoke> request in the returned text.
All text is preserved unchanged and sent to the ordinary frozen Lean validator.
No tool is executed, no proof is selected from the prose, and no verdict is
invented here. Other unknown terminations or structured tool blocks stay held.
"""
import argparse
import base64
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys

WORK = Path(__file__).resolve().parent
REPO = WORK.parents[1] / "outputs/lean-compression-eval"
sys.path.insert(0, str(REPO / "src"))
from lean_compression.io import _abandoned, _release, append_event, digest, file_hash, read_json, read_jsonl
from lean_compression.providers import response_payload, unstorable
from lean_compression.store import load_run, record_generation, generation_path
from lean_compression.integrity import verified_generation

KIND = "captured_text_termination_reconciliation_v2"
LEGACY_KIND = "captured_text_termination_reconciliation_v1"
LEGACY_SHA = "c3fe7b1593ecfe659fbe20a12a1925561289c17cec6755513ba087b0dce9e707"
KNOWN_DIAGNOSTIC = "TypeError: the Message has an unsupported or absent stop_reason"


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
    return {"response": response, "raw": raw, "capture": capture, "request_hash": request_hash,
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
    if not isinstance(amendment, dict) or amendment.get("kind") not in {KIND, LEGACY_KIND}:
        raise ValueError("Existing generation is not this narrow reconciliation")
    expected = {"attempt_id": slot["attempt_id"], "marker": marker, "request_hash": evidence["request_hash"],
        "raw_response_hash": digest(evidence["raw"]), "capture": evidence["capture"],
        "script_sha256": LEGACY_SHA if amendment["kind"] == LEGACY_KIND else file_hash(Path(__file__)),
        "resolution": "preserved_full_text_for_frozen_single_shot_verification", "provider_stop_reason": "tool_use",
        "new_model_calls": 0, "tools_executed": 0, "text_changed": False, "proof_selection_or_repair": False}
    if any(digest(amendment.get(k)) != digest(v) for k, v in expected.items()):
        raise ValueError("Existing reconciliation provenance differs")
    if amendment["kind"] == KIND and any(amendment.get(k) != evidence[k] for k in
            ("capture_file_sha256", "transport_evidence_hash", "transport_index", "request_id", "captured_at")):
        raise ValueError("Existing reconciliation evidence hashes differ")


def recover(run, attempt, apply):
    run = Path(run)
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
                     "resolution": "preserved_full_text_for_frozen_single_shot_verification",
                     "provider_stop_reason": "tool_use", "capture": capture,
                     "marker": owner, "request_hash": request_hash,
                     "raw_response_hash": digest(raw), "script_sha256": file_hash(Path(__file__)),
                     "new_model_calls": 0, "tools_executed": 0,
                     "text_changed": False, "proof_selection_or_repair": False,
                     **{k: evidence[k] for k in ("capture_file_sha256", "transport_evidence_hash",
                                                "transport_index", "request_id", "captured_at")},
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
                "unknown_recorded_at": evidence["unknown_recorded_at"],
                "generation_sha256": file_hash(target) if target.exists() else None,
                "new_model_calls": 0, "tools_executed": 0}
    finally:
        if lock is not None:
            os.close(lock)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--run", type=Path, required=True)
    p.add_argument("--attempt", action="append", required=True)
    p.add_argument("--apply", action="store_true")
    args = p.parse_args()
    print(json.dumps([recover(args.run, ident, args.apply) for ident in args.attempt], indent=2))
