"""Recover a saved text-only tool_use response without another provider call.

This narrow operational amendment handles the observed Messages representation:
thinking/text blocks, with a serialized <invoke> request in the returned text.
All text is preserved unchanged and sent to the ordinary frozen Lean validator.
No tool is executed, no proof is selected from the prose, and no verdict is
invented here. Other unknown terminations or structured tool blocks stay held.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import sys

WORK = Path(__file__).resolve().parent
REPO = WORK.parents[1] / "outputs/lean-compression-eval"
sys.path.insert(0, str(REPO / "src"))
from lean_compression.io import _abandoned, _release, append_event, digest, file_hash, read_json, read_jsonl
from lean_compression.providers import response_payload, unstorable
from lean_compression.store import load_run, record_generation, generation_path


def recoverable_text(response, model):
    if (response.get("type") != "message" or response.get("role") != "assistant" or
            response.get("model") != model or response.get("stop_reason") != "tool_use"):
        raise ValueError("Not the recognized text-only tool termination")
    content = response.get("content")
    if not isinstance(content, list) or any(not isinstance(x, dict) or x.get("type") not in
            {"text", "thinking", "redacted_thinking"} for x in content):
        raise ValueError("Structured/unknown content must remain held")
    parts = [x.get("text") for x in content if x.get("type") == "text"]
    if not parts or not all(isinstance(x, str) for x in parts):
        raise ValueError("No complete returned text")
    raw = "\n".join(parts)
    if "<invoke" not in raw or "</invoke>" not in raw or unstorable(response) is not None:
        raise ValueError("Not the observed serialized tool-request form")
    if not isinstance(response.get("usage"), dict):
        raise ValueError("Missing captured token usage")
    return raw


def recover(run, attempt, apply):
    manifest = load_run(run)
    slot = next(s for s in manifest["slots"] if s["attempt_id"] == attempt)
    settings = manifest["config"]["generation"]
    if settings["provider"] != "anthropic" or settings["mode"] != "single_shot":
        raise ValueError("Wrong frozen provider/mode")
    marker = run / "inflight" / (attempt + ".json")
    if generation_path(run, attempt).exists():
        return {"attempt": attempt, "state": "already_recorded"}
    lock = _abandoned(marker, protocol=False)
    if lock is None:
        raise ValueError("Marker is absent or still held by a live worker")
    try:
        captures = read_jsonl(run / "captures" / (attempt + ".jsonl"))
        if len(captures) != 1:
            raise ValueError("Multiple captured responses need separate review")
        captured = captures[0]
        body = base64.b64decode(captured["body_base64"], validate=True)
        if hashlib.sha256(body).hexdigest() != captured["body_sha256"]:
            raise ValueError("Capture bytes do not match their hash")
        response = json.loads(body)
        raw = recoverable_text(response, slot["model_snapshot"])
        events = [e for e in read_jsonl(run / "transport.jsonl") if e.get("attempt_id") == attempt]
        request_hash = digest(response_payload(slot, settings))
        requests = [e for e in events if e["event"] == "request_started"]
        received = [e for e in events if e["event"] == "response_received" and
                    e.get("capture", {}).get("body_sha256") == captured["body_sha256"]]
        if len(requests) != 1 or requests[0].get("request_hash") != request_hash or len(received) != 1:
            raise ValueError("Request/response transport binding is ambiguous")
        capture = {"path": f"captures/{attempt}.jsonl", "body_sha256": captured["body_sha256"]}
        amendment = {"kind": "captured_text_termination_reconciliation_v1", "attempt_id": attempt,
                     "resolution": "preserved_full_text_for_frozen_single_shot_verification",
                     "provider_stop_reason": "tool_use", "capture": capture,
                     "marker": read_json(marker), "request_hash": request_hash,
                     "raw_response_hash": digest(raw), "script_sha256": file_hash(Path(__file__)),
                     "new_model_calls": 0, "tools_executed": 0,
                     "text_changed": False, "proof_selection_or_repair": False,
                     "note": "A captured single-shot API response exists. Preserve all returned text, including prose and serialized tool request, and let the unchanged validator decide its outcome."}
        if apply:
            append_event(run / "amendments.jsonl", amendment, once=True)
            record_generation(run, slot, raw, generation_status="completed",
                provider_response_id=response.get("id"), returned_model=response["model"],
                usage=response["usage"], provider_status="tool_use", stop_reason="tool_use",
                stop_sequence=response.get("stop_sequence"), incomplete_details=None, refusals=[],
                provider_response=response, response_capture=capture, generation_settings=settings,
                generation_elapsed_seconds=received[0]["elapsed_seconds"],
                capture_reconciliation=amendment, model_tool_calls=0)
            _release(lock, marker)
            lock = None
        return {"attempt": attempt, "state": "recorded" if apply else "reviewed",
                "returned_text_chars": len(raw), "new_model_calls": 0, "tools_executed": 0}
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
