"""Single-shot provider access. No tools, history, repair, or candidate selection."""
from __future__ import annotations

import base64
import email.utils
import hashlib
import http.client
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from datetime import timezone
from pathlib import Path

from .io import append_event, canonical, digest, json_text, now, read_json
from .outcomes import GENERATION_OUTCOMES
from .runner import docker_cli_env
from .store import generation_path, load_run, record_generation, rejected_prompts, stored_generation

IMPORT_FIELDS = ("attempt_id", "prompt_hash", "model_snapshot", "raw_response")
# Far below any interpreter's recursion limit, so imported evidence is written and read alike on Python 3.11+.
MAX_IMPORT_NESTING = 64
# Documented provider error codes for refusing one prompt itself (a safety flag), not the run's settings or access.
PROMPT_REJECTIONS = ("invalid_prompt",)


def nesting_depth(value, limit: int | None = None) -> int:
    """How many objects and arrays deep a JSON value nests, counted without recursion.

    Tuples count as the arrays the JSON writer makes of them. With a limit, the walk returns at the first container
    deeper than that, so its work stays bounded. A container nested in itself raises ValueError, as the writer would.
    """
    deepest, ancestors, stack = 0, set(), [(value, 1)]
    while stack:
        item, depth = stack.pop()
        if depth is None:
            ancestors.discard(item)  # every child of this container has been walked
            continue
        if not isinstance(item, (dict, list, tuple)):
            continue
        if id(item) in ancestors:
            raise ValueError("Circular reference detected")
        if limit is not None and depth > limit:
            return depth
        deepest = max(deepest, depth)
        ancestors.add(id(item))
        stack.append((id(item), None))
        stack.extend((child, depth + 1) for child in (item.values() if isinstance(item, dict) else item))
    return deepest


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


def listed(value, name: str) -> list:
    """A response's output or a message's content; anything but a list is not a response of the expected shape."""
    if not isinstance(value, list):
        raise TypeError(f"{name} is not a list")
    return value


def parse_response(response: dict) -> dict:
    """Read a response object; one of another shape raises TypeError or AttributeError."""
    parts = []
    refusals = []
    status = response.get("status")
    # A provider failure is never a model outcome, so its output is not read, whatever it holds.
    for output in [] if status == "failed" else listed(response.get("output", []), "output"):
        if output.get("type") == "message":
            for content in listed(output.get("content", []), "content"):
                if content.get("type") == "output_text":
                    parts.append(content.get("text", ""))
                elif content.get("type") == "refusal":
                    refusals.append(content.get("refusal", ""))
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


def unstorable(value) -> str | None:
    """Why a stored record cannot hold this parsed JSON, which can hold NaN or a lone surrogate; None if it can."""
    try:
        canonical(value)
    except (ValueError, RecursionError) as error:
        return f"{type(error).__name__}: {error}"
    return None


def without_model_text(response: dict) -> dict:
    """A read response with the model's own text, the output_text and refusal strings parse_response reads, emptied."""
    output = []
    for item in response.get("output", []):
        if item.get("type") == "message":
            item = {**item, "content": [{**content, "text": ""} if content.get("type") == "output_text" else
                                        {**content, "refusal": ""} if content.get("type") == "refusal" else content
                                        for content in item.get("content", [])]}
        output.append(item)
    return {**response, "output": output}


def parse_body(body: bytes, model: str) -> dict:
    """Parse a received body that is already captured; never raise on its content.

    A response that parse_response reads and a stored record can hold is returned as parsed. Of the
    rest, one whose status is neither completed nor incomplete is a provider failure, whatever its
    output holds. Only a completed or incomplete response from `model` whose own text alone holds
    what JSON admits but a stored record cannot (a lone surrogate such as half of 𝕜) is a malformed
    response: a model-attempt failure whose raw_response is the body text with undecodable bytes
    escaped, kept with the response's ID, model, status, usage and incomplete_details. Any other
    body (empty, not a JSON object, of an unexpected shape, with NaN or a lone surrogate outside the
    model's text, or with such text from another model) has no generation_status: nothing shows what
    the model returned, so its outcome is unknown.
    """
    try:
        response = json.loads(body)
    except (ValueError, RecursionError) as error:
        return {"generation_status": None, "unreadable": f"{type(error).__name__}: {error}"}
    if not isinstance(response, dict):
        return {"generation_status": None, "unreadable": "the body is not a JSON object"}
    status = response.get("status")
    try:
        parsed = parse_response(response)
    except (TypeError, AttributeError) as error:
        if status in ("completed", "incomplete"):
            return {"generation_status": None, "unreadable": f"{type(error).__name__}: {error}"}
        parsed = {"generation_status": "provider_failure", "provider_response": response}
    problem = unstorable(parsed)
    if problem is None:
        return parsed
    if status not in ("completed", "incomplete"):
        # Not a model outcome; the transport log cannot hold this response, and its capture keeps it.
        return {"generation_status": "provider_failure", "provider_response": None}
    outside = unstorable(without_model_text(response))
    if outside is not None or response.get("model") != model:
        return {"generation_status": None, "unreadable": outside or f"the returned model is not the pinned snapshot; {problem}"}
    return {"raw_response": body.decode("utf-8", "backslashreplace"), "generation_status": "malformed_response",
            "malformed_reason": problem, "provider_response_id": response.get("id"), "returned_model": response.get("model"),
            "usage": response.get("usage"), "provider_status": status, "incomplete_details": response.get("incomplete_details")}


def retry_after_seconds(value) -> float:
    """Seconds a Retry-After header asks for, as delay-seconds or an HTTP-date; 0 if absent or unreadable."""
    try:
        return float(value)
    except (TypeError, ValueError):
        pass
    try:
        when = email.utils.parsedate_to_datetime(str(value))
    except (TypeError, ValueError, OverflowError):
        return 0
    # HTTP-dates are UTC; the zoneless asctime and -0000 forms parse as naive datetimes.
    return max(0, (when if when.tzinfo else when.replace(tzinfo=timezone.utc)).timestamp() - time.time())


def prompt_rejected(response: dict | None, last_error: dict | None) -> bool:
    """Whether the provider refused this prompt itself, in an HTTP 400 body or a failed response."""
    if response is not None:
        detail = response.get("error") if response.get("status") == "failed" else None
    elif last_error and last_error.get("http_status") == 400:
        try:
            detail = json.loads(last_error["body"]).get("error")
        except (TypeError, ValueError, AttributeError, RecursionError):
            return False
    else:
        return False
    return isinstance(detail, dict) and detail.get("code") in PROMPT_REJECTIONS


def generate_run(run: Path, *, limit: int | None = None, retry_rejected: bool = False) -> dict:
    """Request the frozen schedule's pending slots, one fresh single-shot call each.

    A prompt the provider rejected as invalid_prompt, in this command or as logged in transport.jsonl, is not sent
    again: the samples of a task and condition share it, so its other slots stay pending without a request and are
    counted in skipped_rejected_prompt_slots. retry_rejected sends the logged ones once more.
    """
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
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        raise ValueError("OPENAI_API_KEY is not configured. Set it outside the repository; never put it in config files.")
    if not (key.isascii() and key.isprintable()) or " " in key:
        # Header construction would otherwise fail with the key in its message. Never echo it.
        raise ValueError("OPENAI_API_KEY contains whitespace, control or non-ASCII characters; fix the stored secret (value not shown)")
    image = manifest["config"]["validation"].get("docker_image", "")
    try:
        ready = subprocess.run(["docker", "image", "inspect", image], env=docker_cli_env(), capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError("The pinned container runtime is not available; no model call was made") from error
    if ready.returncode:
        raise ValueError("The pinned container runtime is not available; no model call was made")
    retry = settings.get("retry", {})
    # A rejection is the provider's verdict on the prompt text, which every sample of a task and condition shares.
    earlier = rejected_prompts(run, manifest["slots"])
    unsent = set() if retry_rejected else set(earlier)
    completed = rejected = skipped = consecutive = 0
    transport = run / "transport.jsonl"
    for slot in manifest["slots"]:
        # Rejected prompts were requests too, so they count toward the limit. Skipped slots were not.
        if limit is not None and completed + rejected >= limit:
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
        if slot["prompt_hash"] in unsent:
            # Its prompt was rejected: the slot stays pending, and the run incomplete, without another request.
            marker.unlink()
            skipped += 1
            continue
        payload = response_payload(slot, settings)
        maximum = retry.get("max_transport_attempts", 3)
        received = capture = last_error = None
        generation_started = time.monotonic()
        for transport_index in range(maximum):
            event = {"attempt_id": slot["attempt_id"], "transport_index": transport_index}
            append_event(transport, {**event, "event": "request_started", "request_hash": digest(payload)})
            request = urllib.request.Request("https://api.openai.com/v1/responses",
                       data=json.dumps(payload).encode(), method="POST",
                       headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
            started = time.monotonic()
            retry_after = 0
            try:
                with urllib.request.urlopen(request, timeout=settings.get("timeout_seconds", 180)) as reply:
                    received = reply.read()
                    request_id = reply.headers.get("x-request-id")
                # Append the received, possibly billed, bytes to the attempt's capture before anything reads them.
                capture = {"path": f"captures/{slot['attempt_id']}.jsonl", "body_sha256": hashlib.sha256(received).hexdigest()}
                append_event(run / capture["path"], {**event, "request_id": request_id, "body_sha256": capture["body_sha256"],
                             "body_base64": base64.b64encode(received).decode("ascii")})
                append_event(transport, {**event, "event": "response_received", "request_id": request_id,
                             "capture": capture, "elapsed_seconds": time.monotonic() - started})
                break
            except urllib.error.HTTPError as error:
                # The provider answered without a model outcome. Retain diagnostics, not authorization headers.
                # A 400 never changes settings.
                try:
                    body = error.read(65536).decode(errors="replace").replace(key, "[REDACTED_API_KEY]")
                except (OSError, ValueError, http.client.HTTPException):
                    body = None
                finally:
                    error.close()
                last_error = {"http_status": error.code, "body": body, "retry_after": (error.headers or {}).get("Retry-After")}
                append_event(transport, {**event, "event": "http_error", **last_error})
                if error.code not in (408, 429, 500, 502, 503, 504):
                    break
                retry_after = retry_after_seconds(last_error["retry_after"])
                if retry_after > retry.get("max_retry_after_seconds", 60):
                    break
            except urllib.error.URLError as error:
                # urllib raises this only while connecting or writing the request, before the provider has it.
                last_error = {"type": type(error).__name__, "message": str(error).replace(key, "[REDACTED_API_KEY]")}
                append_event(transport, {**event, "event": "request_not_sent", **last_error})
            except (OSError, http.client.HTTPException, ValueError) as error:
                # A lost or unreadable response may have been billed. Do not silently create a second completion.
                append_event(transport, {**event, "event": "outcome_unknown", "type": type(error).__name__,
                             "message": str(error).replace(key, "[REDACTED_API_KEY]")})
                raise RuntimeError("Provider outcome is unknown; the in-flight marker and transport evidence were retained")
            if transport_index + 1 < maximum:
                backoffs = retry.get("backoff_seconds", [1, 4])
                time.sleep(max(backoffs[min(transport_index, len(backoffs) - 1)], retry_after))
        parsed = None if received is None else parse_body(received, slot["model_snapshot"])
        if parsed is not None and parsed["generation_status"] is None:
            # Possibly billed but unreadable: never resend it automatically. Keep the marker, as for a lost response.
            append_event(transport, {"attempt_id": slot["attempt_id"], "event": "outcome_unknown", "capture": capture,
                         "reason": parsed["unreadable"]})
            raise RuntimeError(f"Provider outcome is unknown ({parsed['unreadable']}); the in-flight marker and transport "
                               f"evidence were retained, with the response in {capture['path']}")
        if parsed is None or parsed["generation_status"] == "provider_failure":
            # Not a model outcome, so no generation is recorded. A later generate resends this attempt ID, unless the
            # provider rejected its prompt.
            provider_response = None if parsed is None else parsed["provider_response"]
            refused = prompt_rejected(provider_response, last_error)
            append_event(transport, {"attempt_id": slot["attempt_id"], "event": "no_model_outcome",
                         "provider_response": provider_response, "last_error": last_error, "prompt_rejected": refused})
            marker.unlink()
            if refused:
                # Only this prompt was refused, not the run: its slots stay pending and the run incomplete.
                rejected += 1
                unsent.add(slot["prompt_hash"])
                if slot["prompt_hash"] not in earlier:
                    # Different prompts rejected one after another are systemic, so they never sweep the schedule.
                    # A prompt rejected before this command is no news, so it never counts here.
                    consecutive += 1
                    if consecutive >= retry.get("max_consecutive_prompt_rejections", 3):
                        count = "a prompt" if consecutive == 1 else f"{consecutive} different prompts in a row"
                        raise RuntimeError(
                            f"Provider rejected {count} as invalid_prompt (generation.retry."
                            "max_consecutive_prompt_rejections), so further model calls stopped. Their slots stay pending "
                            "with nothing recorded; the transport log has the details. Check the shared prompt text and "
                            "the provider account. generate skips rejected prompts from now on; pass --retry-rejected "
                            "to send them again")
                continue
            if last_error and last_error.get("http_status") in (400, 401, 403, 404, 422):
                raise RuntimeError("Provider rejected the configuration or access; the error is logged in transport.jsonl and further model calls stopped")
            raise RuntimeError(f"No model outcome for attempt {slot['attempt_id']}; nothing was recorded, the transport log "
                               "has the details, and further model calls stopped. Run generate again to retry the same attempt ID")
        raw = parsed.pop("raw_response")
        if parsed.get("returned_model") != slot["model_snapshot"]:
            parsed["model_version_mismatch"] = True
            # Stored for audit, but cannot silently pool an unpinned return with the requested cohort.
            parsed["generation_status"] = "model_version_mismatch"
        record_generation(run, slot, raw, **parsed, response_capture=capture, generation_settings=settings,
                          generation_elapsed_seconds=time.monotonic() - generation_started)
        marker.unlink()
        completed += 1
        consecutive = 0
        if parsed.get("model_version_mismatch"):
            raise RuntimeError("Provider returned a different model snapshot; the response is preserved and further model calls stopped")
        if parsed["generation_status"] == "malformed_response":
            # A provider fault could repeat on every call and each would be a final model failure; let a person look first.
            raise RuntimeError(f"Provider returned a malformed response for {slot['attempt_id']}; it is recorded as a model-attempt "
                               f"failure with its bytes in {capture['path']}, and further model calls stopped")
    return {"new_generation_records": completed, "prompt_rejections": rejected,
            "skipped_rejected_prompt_slots": skipped, "scheduled_attempts": manifest["scheduled_attempts"]}


def import_responses(run: Path, rows: list[dict]) -> dict:
    manifest = load_run(run)
    slots = {slot["attempt_id"]: slot for slot in manifest["slots"]}
    seen, pending = set(), []
    # Check every row before writing any, so a rejected file imports nothing.
    for number, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            raise ValueError(f"Import row {number} must be a JSON object")
        missing = [key for key in IMPORT_FIELDS if key not in row]
        if missing:
            raise ValueError(f"Import row {number} lacks {', '.join(missing)}")
        attempt_id = row["attempt_id"]
        if not isinstance(attempt_id, str) or attempt_id in seen or attempt_id not in slots:
            raise ValueError(f"Duplicate or unscheduled attempt ID: {attempt_id}")
        seen.add(attempt_id)
        slot = slots[attempt_id]
        if row["prompt_hash"] != slot["prompt_hash"]:
            raise ValueError(f"Imported response must attest the exact frozen prompt hash: {attempt_id}")
        if row["model_snapshot"] != slot["model_snapshot"]:
            raise ValueError(f"Imported response model does not match frozen cohort: {attempt_id}")
        if not isinstance(row["raw_response"], str):
            raise ValueError(f"raw_response must be the complete response text as a string: {attempt_id}")
        status = row.get("generation_status", "completed")
        if status not in GENERATION_OUTCOMES:
            # Only Lean decides success; transport, provider and stop-reason labels are not model outcomes.
            raise ValueError(f"generation_status must be one of {', '.join(GENERATION_OUTCOMES)}, not {status!r}: {attempt_id}")
        if row.get("usage") is not None and not isinstance(row["usage"], dict):
            raise ValueError(f"usage must be an object or null: {attempt_id}")
        metadata = {"generation_status": status, "provider": "import", "usage": row.get("usage"),
                    "provenance": row.get("provenance"), "generation_settings": row.get("generation_settings")}
        try:
            # Evidence is strict UTF-8 JSON, so a NaN, an Infinity or a lone surrogate would fail mid-write. Deep
            # nesting would too: before Python 3.14 the indented writer recurses once per level. The depth walk
            # stops past the cap and refuses a cyclic value from the Python API, so it always ends.
            if any(nesting_depth(metadata[key], MAX_IMPORT_NESTING) > MAX_IMPORT_NESTING
                   for key in ("usage", "provenance", "generation_settings")):
                raise ValueError(f"nested more than {MAX_IMPORT_NESTING} levels deep")
            json_text([row["raw_response"], metadata]).encode("utf-8")
        except (ValueError, TypeError, RecursionError) as error:
            raise ValueError(f"Import row {number} cannot be stored as JSON evidence ({error}): {attempt_id}") from error
        if stored_generation(run, slot, row["raw_response"], **metadata) is None:
            pending.append((slot, row["raw_response"], metadata))
    for slot, raw, metadata in pending:
        record_generation(run, slot, raw, **metadata)
    return {"imported": len(pending), "already_imported": len(seen) - len(pending)}
