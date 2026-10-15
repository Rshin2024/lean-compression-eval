"""One-use releases restricted to six terminal Sonnet errors after partial text.

The original outcome/charge stay unknown. This helper makes no model calls and
does not resume the controller. Every original byte and claim is hash-bound.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
WORK = HERE.parents[2]
REPO = WORK.parents[1] / "outputs/lean-compression-eval"
sys.path[:0] = [str(REPO / "src"), str(WORK / "recovery"), str(WORK / "orchestration")]
from lean_compression.io import _abandoned, _release, append_event, digest, file_hash, read_json, write_json
from lean_compression.store import load_run
from streaming_adapter import streaming_payload
from recover_text_tool_stops import read_events, unique_object

ATTEMPTS = ('bca7a27c55aaa42f4be945bc92f4d52f8201d6bf3e4cd00f363da441cfd5de2f', '47fe381fab717179bd251a226b46f49c6ce6c071a2445c99fc053132f2410bc2', '142eef3bf02eee2f8233a914b1e9d629df06559474f19ba476eafd3101882fbd', '88e446649aa37e9d900b48130d580c5c87d9914751e419053c3df96044a62228', '5d3b679dbd40b955566e8698dd1289127bc1c23077a323a3ad54a88bb1539206', '68b64b8ac4296ed58c4c3a281986f8f8a5aa2c2a23d05a1ec585eedc2d961a5f')
MODEL = "claude-sonnet-5-5"
KIND = "one_use_terminal_partial_api_error_release_v1"
REASON = "terminal provider api_error after partial text; no final response; charge unknown"


def inspect(run, attempt):
    run = Path(run)
    if attempt not in ATTEMPTS or run.name != "om100-max-claude-sonnet-5-5-high-20261014":
        raise ValueError("Only the exact reviewed new-run Sonnet attempt is eligible")
    m = load_run(run)
    matches = [s for s in m["slots"] if s["attempt_id"] == attempt]
    if len(matches) != 1 or m["model_snapshot"] != MODEL:
        raise ValueError("Frozen model/attempt differs")
    slot = matches[0]
    if (run / "generations" / f"{attempt}.json").exists() or (run / "captures" / f"{attempt}.jsonl").exists():
        raise ValueError("A saved model response must never be retried by this release")
    marker = run / "inflight" / f"{attempt}.json"
    marker_data = read_json(marker)
    if marker_data.get("attempt_id") != attempt:
        raise ValueError("Claim names another attempt")
    events = [e for e in read_events(run / "transport.jsonl")[0] if e.get("attempt_id") == attempt]
    original = events[:2]
    if [e.get("event") for e in original] != ["request_started", "outcome_unknown"]:
        raise ValueError("Original transport prefix differs")
    if (original[0].get("request_hash") != digest(streaming_payload(slot, m["config"]["generation"]))
            or any(e.get("transport_index") != 0 for e in original)
            or original[1].get("message") != "Provider emitted an SSE error; possibly billed outcome retained"):
        raise ValueError("Original request/error binding differs")
    if any(e.get("event") != "authorized_unknown_release" for e in events[2:]) or len(events) > 3:
        raise ValueError("A replacement request already exists or transport history differs")
    streams = [e for e in read_events(run / "stream-transport.jsonl")[0] if e.get("attempt_id") == attempt]
    if [e.get("event") for e in streams] != ["stream_opened", "stream_closed"]:
        raise ValueError("Expected exactly one original stream")
    opened, closed = streams
    path = (run / closed["path"]).resolve()
    if not path.is_relative_to((run / "stream-captures" / attempt).resolve()) or opened["path"] != closed["path"]:
        raise ValueError("Stream path escaped the exact attempt")
    raw = path.read_bytes()
    if closed.get("complete") is not False or len(raw) != closed.get("bytes") or hashlib.sha256(raw).hexdigest() != closed.get("sha256"):
        raise ValueError("Original failed stream changed")
    parsed = []
    for line in raw.splitlines():
        if line.startswith(b"data:"):
            parsed.append(json.loads(line[5:].strip(), object_pairs_hook=unique_object))
    if not parsed or parsed[0].get("type") != "message_start" or parsed[-1].get("type") != "error":
        raise ValueError("Stream does not end with explicit provider error")
    if parsed[-1].get("error", {}).get("type") != "api_error":
        raise ValueError("Only the reviewed terminal provider api_error is authorized")
    if any(e.get("type") == "message_stop" for e in parsed):
        raise ValueError("A final response exists")
    message = parsed[0]["message"]
    if message.get("model") != MODEL or message.get("content"):
        raise ValueError("Original streamed model/content differs")
    text_blocks = {}
    for event in parsed:
        if event.get("type") not in {"message_start", "content_block_start", "content_block_delta", "content_block_stop", "ping", "error"}:
            raise ValueError("Unexpected response content or deltas; leave held")
        if event.get("type") == "content_block_start":
            block = event.get("content_block", {})
            if block.get("type") not in {"thinking", "text", "redacted_thinking"}:
                raise ValueError("Structured or unknown content must remain held")
            if block.get("type") == "text":
                text_blocks[event["index"]] = block.get("text", "")
        if event.get("type") == "content_block_delta":
            delta = event["delta"]
            if delta.get("type") == "text_delta" and event["index"] in text_blocks:
                text_blocks[event["index"]] += delta["text"]
            elif delta.get("type") not in {"thinking_delta", "signature_delta"}:
                raise ValueError("Unknown or structured content delta must remain held")
    partial = "\n".join(text_blocks[i] for i in sorted(text_blocks))
    if not partial:
        raise ValueError("This exact-six proposal requires preserved partial answer text")
    return {"attempt_id": attempt, "model": MODEL, "run_directory": run.name,
        "manifest_sha256": file_hash(run / "manifest.json"), "run_hash": m["run_hash"],
        "slot_identity": {k: v for k, v in slot.items() if k != "messages"},
        "marker": marker_data, "marker_sha256": file_hash(marker),
        "transport_prefix": original, "transport_prefix_hash": digest(original),
        "request_hash": original[0]["request_hash"], "stream_events": streams, "stream_events_hash": digest(streams),
        "stream_path": closed["path"], "stream_sha256": closed["sha256"], "stream_bytes": len(raw),
        "provider_response_id": message.get("id"), "terminal_error": parsed[-1],
        "no_answer_text": False, "partial_answer_chars": len(partial),
        "partial_answer_sha256": hashlib.sha256(partial.encode()).hexdigest(),
        "final_message_stop_present": False,
        "original_outcome": "unknown", "original_charge_usd": None}


def execute(proposal_path, proposal_sha256, apply=False):
    if file_hash(proposal_path) != proposal_sha256:
        raise ValueError("Proposal differs from reviewed hash")
    proposal = read_json(proposal_path)
    attempt = proposal.get("attempt_id")
    if (proposal.get("kind") != KIND or proposal.get("attempt_id") not in ATTEMPTS
            or proposal.get("helper_sha256") != file_hash(Path(__file__))
            or proposal.get("maximum_release_count") != 1 or proposal.get("reason") != REASON):
        raise ValueError("One-use scope differs")
    if file_hash(WORK / "launch_ready.json") != proposal["launch_plan_sha256"]:
        raise ValueError("Frozen launch plan changed")
    evidence_path = proposal_path.parent / proposal["evidence_file"]
    if file_hash(evidence_path) != proposal["evidence_sha256"]:
        raise ValueError("Original evidence snapshot changed")
    expected = read_json(evidence_path)
    run = (REPO / "runs" / expected["run_directory"]).resolve()
    if run.parent != (REPO / "runs").resolve():
        raise ValueError("Reviewed run path escaped the repository")
    if preserved := proposal.get("preserved_original_stream_file"):
        path = (proposal_path.parent / preserved).resolve()
        if not path.is_relative_to(proposal_path.parent.resolve()) or file_hash(path) != expected["stream_sha256"]:
            raise ValueError("Preserved original failed SSE differs")
    partial_path = (proposal_path.parent / proposal["preserved_partial_answer_file"]).resolve()
    if not partial_path.is_relative_to(proposal_path.parent.resolve()) or file_hash(partial_path) != expected["partial_answer_sha256"]:
        raise ValueError("Preserved full partial answer text differs")
    receipt_path = proposal_path.parent / "release-receipt.json"
    if receipt_path.exists():
        receipt = read_json(receipt_path)
        if receipt.get("proposal_sha256") != proposal_sha256:
            raise ValueError("A conflicting release receipt exists")
        return {"state": "authorization_already_consumed", "receipt": receipt, "new_model_calls": 0}
    marker = run / "inflight" / f"{attempt}.json"
    lock = _abandoned(marker, protocol=False)
    if lock is None:
        raise ValueError("Original marker is absent or still owned by an active request")
    try:
        actual = inspect(run, attempt)
        if actual != expected:
            raise ValueError("Original evidence differs from the reviewed snapshot")
        intent = {"kind": KIND, "proposal_sha256": proposal_sha256, "attempt_id": attempt,
            "marker": actual["marker"], "marker_sha256": actual["marker_sha256"],
            "reason": REASON, "original_outcome": "unknown", "original_charge_usd": None,
            "original_stream_sha256": actual["stream_sha256"], "maximum_release_count": 1,
            "partial_answer_sha256": actual["partial_answer_sha256"], "partial_answer_chars": actual["partial_answer_chars"],
            "new_model_calls": 0, "tools_executed": 0, "helper_sha256": proposal["helper_sha256"]}
        if not apply:
            return {"state": "reviewed", "intent": intent}
        write_json(proposal_path.parent / "release-intent.json", intent)
        append_event(run / "amendments.jsonl", {**intent, "resolution": "released_for_one_authorized_replacement_request"}, once=True)
        append_event(run / "transport.jsonl", {"attempt_id": attempt, "event": "authorized_unknown_release",
            "reconciliation": intent, "reason": REASON}, once=True)
        _release(lock, marker)
        lock = None
        receipt = {**intent, "state": "released_once", "original_marker_removed": True,
                   "controller_resume_requested": False}
        write_json(receipt_path, receipt)
        return {"state": "released_once", "receipt": receipt, "new_model_calls": 0}
    finally:
        if lock is not None:
            os.close(lock)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proposal", type=Path, default=HERE / "proposal.json")
    parser.add_argument("--proposal-sha256", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        os.environ.pop(key, None)
    with (HERE / "release.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        print(json.dumps(execute(args.proposal, args.proposal_sha256, args.apply), indent=2))


if __name__ == "__main__":
    main()
