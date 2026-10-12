"""Single-shot provider access. No tools, history, repair, or candidate selection."""
from __future__ import annotations

import json
import os
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from .io import append_event, digest, now, read_json
from .store import generation_path, load_run, record_generation


def response_payload(slot: dict, settings: dict) -> dict:
    payload = {
        "model": slot["model_snapshot"], "input": slot["messages"],
        "max_output_tokens": settings["max_output_tokens"],
        "store": False, "tools": [], "truncation": "disabled",
    }
    if settings.get("temperature") is not None:
        payload["temperature"] = settings["temperature"]
    if settings.get("reasoning_setting") is not None:
        payload["reasoning"] = {"effort": settings["reasoning_setting"]}
    return payload


def parse_response(response: dict) -> dict:
    parts = []
    refusals = []
    for output in response.get("output", []):
        if output.get("type") == "message":
            for content in output.get("content", []):
                if content.get("type") == "output_text":
                    parts.append(content.get("text", ""))
                elif content.get("type") == "refusal":
                    refusals.append(content.get("refusal", ""))
    status = response.get("status")
    if refusals:
        generation_status = "refusal"
    elif status == "incomplete":
        generation_status = "truncated_output"
    elif status != "completed":
        generation_status = "provider_failure"
    else:
        generation_status = "completed"
    # Preserve every returned text part. Multiple proofs will fail the whole-term parser.
    return {"raw_response": "\n".join(parts), "generation_status": generation_status,
            "provider_response_id": response.get("id"), "returned_model": response.get("model"),
            "usage": response.get("usage"), "provider_status": status,
            "incomplete_details": response.get("incomplete_details"), "refusals": refusals,
            "provider_response": response}


def generate_run(run: Path, *, limit: int | None = None) -> dict:
    manifest = load_run(run)
    settings = manifest["config"]["generation"]
    if manifest["config"]["validation"].get("isolation") != "docker":
        raise ValueError("Live model generations require the container runtime profile; native development is for trusted references only")
    if settings.get("provider") != "openai":
        raise ValueError("Built-in live provider is OpenAI; use import-responses for other providers")
    if manifest["model_snapshot"] in ("reference_fixture", "RESOLVE_BEFORE_RUN", "unconfigured"):
        raise ValueError("Set a real model snapshot before requesting live generations")
    if not settings.get("snapshot_pinned"):
        raise ValueError("A pinned model snapshot is required, including for live development runs")
    if not settings.get("settings_resolved"):
        raise ValueError("Mark reasoning and sampling settings resolved before live generation")
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise ValueError("OPENAI_API_KEY is not configured. Set it outside the repository; never put it in config files.")
    image = manifest["config"]["validation"].get("docker_image", "")
    try:
        ready = subprocess.run(["docker", "image", "inspect", image], capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError("The pinned container runtime is not available; no model call was made") from error
    if ready.returncode:
        raise ValueError("The pinned container runtime is not available; no model call was made")
    completed = 0
    for slot in manifest["slots"]:
        if limit is not None and completed >= limit:
            break
        if generation_path(run, slot["attempt_id"]).exists():
            continue
        marker = run / "inflight" / f"{slot['attempt_id']}.json"
        marker.parent.mkdir(parents=True, exist_ok=True)
        try:
            with marker.open("x") as out:
                json.dump({"started_at": now(), "attempt_id": slot["attempt_id"]}, out)
        except FileExistsError:
            raise RuntimeError(f"Unresolved in-flight call for {slot['attempt_id']}; reconcile its transport log before resending")
        # Another worker can finish between the first existence check and this
        # marker acquisition. Recheck under our exclusive marker before billing.
        if generation_path(run, slot["attempt_id"]).exists():
            marker.unlink()
            continue
        payload = response_payload(slot, settings)
        retry = settings.get("retry", {})
        maximum = retry.get("max_transport_attempts", 3)
        response_data = None
        last_error = None
        uncertain = False
        generation_started = time.monotonic()
        for transport_index in range(maximum):
            append_event(run / "transport.jsonl", {"attempt_id": slot["attempt_id"],
                         "transport_index": transport_index, "event": "request_started",
                         "request_hash": digest(payload)})
            request = urllib.request.Request("https://api.openai.com/v1/responses",
                       data=json.dumps(payload).encode(), method="POST",
                       headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
            started = time.monotonic()
            try:
                with urllib.request.urlopen(request, timeout=settings.get("timeout_seconds", 180)) as reply:
                    response_data = json.load(reply)
                    request_id = reply.headers.get("x-request-id")
                append_event(run / "transport.jsonl", {"attempt_id": slot["attempt_id"],
                             "transport_index": transport_index, "event": "response_received",
                             "request_id": request_id, "elapsed_seconds": time.monotonic() - started})
                break
            except urllib.error.HTTPError as error:
                # Retain diagnostics, not authorization headers. A 400 never changes settings.
                last_error = {"http_status": error.code, "body": error.read(65536).decode(errors="replace").replace(key, "[REDACTED_API_KEY]")}
                append_event(run / "transport.jsonl", {"attempt_id": slot["attempt_id"],
                             "transport_index": transport_index, "event": "http_error", **last_error})
                if error.code not in (408, 429, 500, 502, 503, 504):
                    break
            except (urllib.error.URLError, TimeoutError, socket.timeout, json.JSONDecodeError) as error:
                # A lost response may have been billed. Do not silently create a second completion.
                last_error = {"type": type(error).__name__, "message": str(error)}
                uncertain = True
                append_event(run / "transport.jsonl", {"attempt_id": slot["attempt_id"],
                             "transport_index": transport_index, "event": "outcome_unknown", **last_error})
                break
            if transport_index + 1 < maximum:
                backoffs = retry.get("backoff_seconds", [1, 4])
                time.sleep(backoffs[min(transport_index, len(backoffs) - 1)])
        if uncertain:
            raise RuntimeError("Provider outcome is unknown; the in-flight marker and transport evidence were retained")
        if response_data is None:
            record_generation(run, slot, "", generation_status="transport_error", transport_error=last_error,
                              generation_elapsed_seconds=time.monotonic() - generation_started)
        else:
            parsed = parse_response(response_data)
            raw = parsed.pop("raw_response")
            if parsed.get("returned_model") != slot["model_snapshot"]:
                parsed["model_version_mismatch"] = True
                # Stored for audit, but cannot silently pool an unpinned return with the requested cohort.
                parsed["generation_status"] = "model_version_mismatch"
            record_generation(run, slot, raw, **parsed, generation_settings=settings,
                              generation_elapsed_seconds=time.monotonic() - generation_started)
        marker.unlink()
        completed += 1
        if response_data is not None and parsed.get("model_version_mismatch"):
            raise RuntimeError("Provider returned a different model snapshot; the response is preserved and further model calls stopped")
        if response_data is None and last_error and last_error.get("http_status") in (400, 401, 403, 404, 422):
            raise RuntimeError("Provider rejected the configuration or access; the error is recorded and further model calls stopped")
    return {"new_generation_records": completed, "scheduled_attempts": manifest["scheduled_attempts"]}


def import_responses(run: Path, rows: list[dict]) -> dict:
    manifest = load_run(run)
    slots = {slot["attempt_id"]: slot for slot in manifest["slots"]}
    seen = set()
    for row in rows:
        attempt_id = row["attempt_id"]
        if attempt_id in seen or attempt_id not in slots:
            raise ValueError(f"Duplicate or unscheduled attempt ID: {attempt_id}")
        seen.add(attempt_id)
        slot = slots[attempt_id]
        if row.get("prompt_hash") != slot["prompt_hash"]:
            raise ValueError(f"Imported response must attest the exact frozen prompt hash: {attempt_id}")
        if row.get("model_snapshot") != slot["model_snapshot"]:
            raise ValueError(f"Imported response model does not match frozen cohort: {attempt_id}")
        record_generation(run, slot, row["raw_response"], generation_status=row.get("generation_status", "completed"),
                          provider="import", usage=row.get("usage"), provenance=row.get("provenance"),
                          generation_settings=row.get("generation_settings"))
    return {"imported": len(seen)}
