"""Single-shot provider access. No tools, history, repair, or candidate selection."""
from __future__ import annotations

import base64
import contextlib
import email.utils
import hashlib
import http.client
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .integrity import verified_generation
from .io import _abandoned, append_event, canonical, digest, json_text, now, read_json
from .outcomes import GENERATION_OUTCOMES
from .runner import docker_cli_env
from .store import _log_lines, generation_path, load_run, record_generation, rejected_prompts, stored_generation

IMPORT_FIELDS = ("attempt_id", "prompt_hash", "model_snapshot", "raw_response")
# Far below any interpreter's recursion limit, so imported evidence is written and read alike on Python 3.11+.
MAX_IMPORT_NESTING = 64
# Documented provider error codes for refusing one prompt itself (a safety flag), not the run's settings or access.
PROMPT_REJECTIONS = ("invalid_prompt",)
# The transport event fields reconcile shows of a call; an http_error body stays in transport.jsonl.
CALL_FIELDS = ("event", "transport_index", "recorded_at", "http_status", "request_id", "type", "message", "reason")
LIVE_PROVIDERS = {
    "openai": ("https://api.openai.com/v1/responses", "OPENAI_API_KEY", "x-request-id"),
    "anthropic": ("https://api.anthropic.com/v1/messages", "ANTHROPIC_API_KEY", "request-id"),
}


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
    if settings.get("provider", "openai") == "anthropic":
        return anthropic_payload(slot, settings)
    payload = {
        "model": slot["model_snapshot"], "input": slot["messages"],
        "max_output_tokens": settings["max_output_tokens"],
        "store": False, "tools": [], "truncation": "disabled",
    }
    if settings.get("temperature") is not None:
        payload["temperature"] = settings["temperature"]
    if settings.get("reasoning_setting") is not None:
        payload["reasoning"] = {"effort": settings["reasoning_setting"]}
    if settings.get("service_tier") is not None:
        payload["service_tier"] = settings["service_tier"]
    return payload


def anthropic_payload(slot: dict, settings: dict) -> dict:
    """Map the frozen system/user prompt into Messages without adding history or sampling overrides."""
    if settings.get("thinking") != {"type": "adaptive"}:
        raise ValueError("Anthropic generation requires explicit thinking: {type: adaptive}")
    if not isinstance(settings.get("reasoning_setting"), str) or not settings["reasoning_setting"]:
        raise ValueError("Anthropic generation requires an explicit reasoning_setting for output_config.effort")
    if any(settings.get(name) is not None for name in ("temperature", "top_p", "top_k")):
        raise ValueError("Anthropic adaptive thinking requires temperature, top_p and top_k to be omitted (null)")
    prompt = slot["messages"]
    if (len(prompt) != 2 or [item.get("role") for item in prompt] != ["system", "user"]
            or any(not isinstance(item.get("content"), str) for item in prompt)):
        raise ValueError("Anthropic generation requires the frozen system/user text prompt")
    return {"model": slot["model_snapshot"], "system": prompt[0]["content"],
            "messages": [{"role": "user", "content": prompt[1]["content"]}],
            "max_tokens": settings["max_output_tokens"], "thinking": settings["thinking"],
            "output_config": {"effort": settings["reasoning_setting"]}}


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


def parse_body(body: bytes, model: str, provider: str = "openai") -> dict:
    """Parse a received body that is already captured; never raise on its content.

    A response that parse_response reads and a stored record can hold is returned as parsed. Of the
    rest, one whose status is neither completed nor incomplete is a provider failure, whatever its
    output holds; whether it rejects the prompt (prompt_rejected) is read before it is set aside, as
    for any failed response. Only a completed or incomplete response from `model` whose own text alone holds
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
    if provider == "anthropic":
        return parse_anthropic_body(response, body, model)
    if provider != "openai":
        return {"generation_status": None, "unreadable": f"unsupported provider: {provider}"}
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
        # Not a model outcome; the transport log cannot hold this response, and its capture keeps it. A value
        # elsewhere in it never hides that the provider refused the prompt itself.
        return {"generation_status": "provider_failure", "provider_response": None,
                "prompt_rejected": prompt_rejected(response, None)}
    outside = unstorable(without_model_text(response))
    if outside is not None or response.get("model") != model:
        return {"generation_status": None, "unreadable": outside or f"the returned model is not the pinned snapshot; {problem}"}
    return {"raw_response": body.decode("utf-8", "backslashreplace"), "generation_status": "malformed_response",
            "malformed_reason": problem, "provider_response_id": response.get("id"), "returned_model": response.get("model"),
            "usage": response.get("usage"), "provider_status": status, "incomplete_details": response.get("incomplete_details")}


def parse_anthropic_body(response: dict, body: bytes, model: str) -> dict:
    """Read only completed Messages responses. Unknown stops and malformed shapes keep the call held.

    Thinking blocks are retained in provider_response and the byte capture but never become a proof candidate.
    Only explicit error objects are provider failures; an unfamiliar possibly billed response is never retried.
    """
    if response.get("type") == "error" and isinstance(response.get("error"), dict):
        return {"generation_status": "provider_failure", "provider_response": response if unstorable(response) is None else None}
    stop = response.get("stop_reason")
    statuses = {"end_turn": "completed", "max_tokens": "truncated_output", "refusal": "refusal"}
    try:
        if response.get("type") != "message" or response.get("role") != "assistant":
            raise TypeError("the body is not an assistant Message")
        if not isinstance(stop, str) or stop not in statuses:
            raise TypeError("the Message has an unsupported or absent stop_reason")
        parts, outside_content = [], []
        for block in listed(response.get("content"), "content"):
            kind = block.get("type")
            if kind == "text":
                if not isinstance(block.get("text"), str):
                    raise TypeError("a text block has no string text")
                parts.append(block["text"])
                outside_content.append({**block, "text": ""})
            elif kind in ("thinking", "redacted_thinking"):
                outside_content.append(block)
            else:
                raise TypeError("the Message has an unsupported content block")
    except (TypeError, AttributeError) as error:
        return {"generation_status": None, "unreadable": f"{type(error).__name__}: {error}"}
    metadata = {"provider_response_id": response.get("id"), "returned_model": response.get("model"),
                "usage": response.get("usage"), "provider_status": stop, "stop_reason": stop,
                "stop_sequence": response.get("stop_sequence"), "incomplete_details": None}
    parsed = {"raw_response": "\n".join(parts), "generation_status": statuses[stop],
              **metadata, "refusals": parts.copy() if stop == "refusal" else [], "provider_response": response}
    problem = unstorable(parsed)
    if problem is None:
        return parsed
    outside = unstorable({**response, "content": outside_content})
    if outside is not None or response.get("model") != model:
        return {"generation_status": None, "unreadable": outside or f"the returned model is not the pinned snapshot; {problem}"}
    return {"raw_response": body.decode("utf-8", "backslashreplace"), "generation_status": "malformed_response",
            "malformed_reason": problem, **metadata}


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


def in_flight(marker: Path, attempt_id: str):
    """Publish a call's in-flight marker already locked; return the open file that holds the lock.

    A marker whose lock is free belongs to a command that has ended, so reconcile_run never takes a call in progress
    for an abandoned one. Markers written before this lock existed are always free. FileExistsError if the attempt
    already has a marker.
    """
    import fcntl
    import tempfile
    marker.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=f".{marker.name}.", dir=marker.parent)
    lock = os.fdopen(fd, "w", encoding="utf-8")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        json.dump({"started_at": now(), "attempt_id": attempt_id}, lock)
        lock.flush()
        os.link(temp, marker)
    except BaseException:
        lock.close()
        raise
    finally:
        os.unlink(temp)
    return lock


def generate_run(run: Path, *, limit: int | None = None, retry_rejected: bool = False,
                 shard_index: int = 0, shard_count: int = 1) -> dict:
    """Request the frozen schedule's pending slots, one fresh single-shot call each.

    A prompt the provider rejected as invalid_prompt, in this command or as logged in transport.jsonl, is not sent
    again until a generation of one of its slots is recorded after that: the samples of a task and condition share it,
    so its other slots stay pending without a request and are counted in skipped_rejected_prompt_slots. retry_rejected
    sends the logged ones once more, after the other pending slots and the one rejected longest ago first. A call's
    in-flight marker stays locked until the call is resolved or the command ends (in_flight).
    """
    if (type(shard_count) is not int or shard_count < 1 or type(shard_index) is not int
            or not 0 <= shard_index < shard_count):
        raise ValueError("Generation shard index must be between zero and shard_count - 1")
    with contextlib.ExitStack() as held:
        return _generate(run, held, limit, retry_rejected, shard_index, shard_count)


def _generate(run: Path, held: contextlib.ExitStack, limit: int | None, retry_rejected: bool,
              shard_index: int = 0, shard_count: int = 1) -> dict:
    manifest = load_run(run)
    settings = manifest["config"]["generation"]
    if manifest["config"]["validation"].get("isolation") != "docker":
        raise ValueError("Live model generations require the container runtime profile; native development is for trusted references only")
    provider = settings.get("provider", "openai")
    if provider not in LIVE_PROVIDERS:
        raise ValueError("Built-in live providers are OpenAI and Anthropic; use import-responses for other providers")
    endpoint, key_name, request_id_header = LIVE_PROVIDERS[provider]
    if manifest["model_snapshot"] in ("reference_fixture", "RESOLVE_BEFORE_RUN", "unconfigured"):
        raise ValueError("Set a real model snapshot before requesting live generations")
    if not settings.get("snapshot_pinned"):
        raise ValueError("A pinned model snapshot is required, including for live development runs")
    if not settings.get("settings_resolved"):
        raise ValueError("Mark reasoning and sampling settings resolved before live generation")
    key = os.environ.get(key_name, "").strip()
    if not key:
        raise ValueError(f"{key_name} is not configured. Set it outside the repository; never put it in config files.")
    if not (key.isascii() and key.isprintable()) or " " in key:
        # Header construction would otherwise fail with the key in its message. Never echo it.
        raise ValueError(f"{key_name} contains whitespace, control or non-ASCII characters; fix the stored secret (value not shown)")
    headers = {"Content-Type": "application/json", **(
        {"x-api-key": key, "anthropic-version": "2023-06-01"} if provider == "anthropic" else
        {"Authorization": f"Bearer {key}"})}
    workspace = os.environ.get("ANTHROPIC_WORKSPACE_ID", "") if provider == "anthropic" else ""
    if workspace:
        if not (workspace.isascii() and workspace.isprintable()) or " " in workspace:
            raise ValueError("ANTHROPIC_WORKSPACE_ID contains whitespace, control or non-ASCII characters; "
                             "fix the runtime workspace setting (value not shown)")
        headers["anthropic-workspace-id"] = workspace

    def redacted(message: str) -> str:
        message = message.replace(key, "[REDACTED_API_KEY]")
        return message.replace(workspace, "[REDACTED_WORKSPACE_ID]") if workspace else message

    # Resolve provider mapping before acquiring a marker or logging any request.
    for slot in manifest["slots"]:
        response_payload(slot, settings)
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
    # retry_rejected sends those prompts after every other pending slot, which keep the schedule's order, and the one
    # rejected longest ago first, so they never hold up the rest, and a retry that --limit or the stop below cuts short
    # moves on to the others next time.
    turn = {prompt: index for index, prompt in enumerate(earlier)} if retry_rejected else {}
    completed = rejected = skipped = 0
    streak = []  # prompts rejected one after another in this command, with no generation between them
    transport = run / "transport.jsonl"
    # Partition the already-frozen schedule, never regenerate IDs or prompts.
    # Stale/unknown calls still stop their owning shard until reconciled.
    selected_slots = [slot for index, slot in enumerate(manifest["slots"])
                      if index % shard_count == shard_index]
    for slot in sorted(selected_slots, key=lambda item: turn.get(item["prompt_hash"], -1)):
        # Rejected prompts were requests too, so they count toward the limit. Skipped slots were not.
        if limit is not None and completed + rejected >= limit:
            break
        if generation_path(run, slot["attempt_id"]).exists():
            continue
        marker = run / "inflight" / f"{slot['attempt_id']}.json"
        try:
            lock = in_flight(marker, slot["attempt_id"])
        except FileExistsError:
            raise RuntimeError(f"Unresolved in-flight call for {slot['attempt_id']}; nothing is sent for it until "
                               f"reconcile --run {run} resolves it")
        held.close()  # the previous call's lock; that call is resolved and its marker removed
        held.enter_context(lock)
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
            request = urllib.request.Request(endpoint,
                       data=json.dumps(payload).encode(), method="POST",
                       headers=headers)
            started = time.monotonic()
            retry_after = 0
            try:
                with urllib.request.urlopen(request, timeout=settings.get("timeout_seconds", 180)) as reply:
                    received = reply.read()
                    request_id = reply.headers.get(request_id_header)
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
                    body = redacted(error.read(65536).decode(errors="replace"))
                except (OSError, ValueError, http.client.HTTPException):
                    body = None
                finally:
                    error.close()
                last_error = {"http_status": error.code, "body": body, "retry_after": (error.headers or {}).get("Retry-After")}
                append_event(transport, {**event, "event": "http_error", **last_error})
                if error.code not in (408, 429, 500, 502, 503, 504) and not (provider == "anthropic" and error.code == 529):
                    break
                retry_after = retry_after_seconds(last_error["retry_after"])
                if retry_after > retry.get("max_retry_after_seconds", 60):
                    break
            except urllib.error.URLError as error:
                # urllib raises this only while connecting or writing the request, before the provider has it.
                last_error = {"type": type(error).__name__, "message": redacted(str(error))}
                append_event(transport, {**event, "event": "request_not_sent", **last_error})
            except (OSError, http.client.HTTPException, ValueError) as error:
                # A lost or unreadable response may have been billed. Do not silently create a second completion.
                append_event(transport, {**event, "event": "outcome_unknown", "type": type(error).__name__,
                             "message": redacted(str(error))})
                raise RuntimeError("Provider outcome is unknown; the in-flight marker and transport evidence were retained "
                                   "for reconcile")
            if transport_index + 1 < maximum:
                backoffs = retry.get("backoff_seconds", [1, 4])
                time.sleep(max(backoffs[min(transport_index, len(backoffs) - 1)], retry_after))
        parsed = None if received is None else parse_body(received, slot["model_snapshot"], provider)
        if parsed is not None and parsed["generation_status"] is None:
            # Possibly billed but unreadable: never resend it automatically. Keep the marker, as for a lost response.
            append_event(transport, {"attempt_id": slot["attempt_id"], "event": "outcome_unknown", "capture": capture,
                         "reason": parsed["unreadable"]})
            raise RuntimeError(f"Provider outcome is unknown ({parsed['unreadable']}); the in-flight marker and transport "
                               f"evidence were retained for reconcile, with the response in {capture['path']}")
        if parsed is None or parsed["generation_status"] == "provider_failure":
            # Not a model outcome, so no generation is recorded. A later generate resends this attempt ID, unless the
            # provider rejected its prompt. A failed response the log cannot hold was classified when it was read.
            provider_response = None if parsed is None else parsed["provider_response"]
            refused = (parsed or {}).get("prompt_rejected") or prompt_rejected(provider_response, last_error)
            append_event(transport, {"attempt_id": slot["attempt_id"], "event": "no_model_outcome", "capture": capture,
                         "provider_response": provider_response, "last_error": last_error, "prompt_rejected": refused})
            marker.unlink()
            if refused:
                # Only this prompt was refused, not the run: its slots stay pending and the run incomplete.
                rejected += 1
                unsent.add(slot["prompt_hash"])
                streak.append(slot["prompt_hash"])
                # Different prompts rejected one after another are systemic, so they never sweep the schedule; nor do
                # prompts rejected before, sent again. A rejected prompt gets no further request here, so these differ.
                if len(streak) >= retry.get("max_consecutive_prompt_rejections", 3):
                    again = sum(prompt in earlier for prompt in streak)
                    count = ("a prompt" if len(streak) == 1 else f"{len(streak)} different prompts in a row") + (
                        "" if not again else " again" if len(streak) == 1 else f" ({again} of them again)")
                    raise RuntimeError(
                        f"Provider rejected {count} as invalid_prompt (generation.retry."
                        "max_consecutive_prompt_rejections), so further model calls stopped. Their slots stay pending "
                        "with nothing recorded; the transport log has the details. Check the shared prompt text and "
                        "the provider account. generate skips rejected prompts from now on; pass --retry-rejected "
                        "to send them again, those rejected longest ago first")
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
        streak = []
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


def _shown(value):
    """The value, if it is strict JSON the CLI can print; otherwise why it is not."""
    problem = unstorable(value)
    return value if problem is None else {"unstorable": problem}


def _body_metadata(body: bytes, provider: str = "openai") -> dict:
    """A captured body's size and, for a JSON object, its provider metadata; never its text."""
    try:
        response = json.loads(body)
    except (ValueError, RecursionError):
        response = None
    if not isinstance(response, dict):
        return {"bytes": len(body), "json_object": False}
    error = response.get("error") if isinstance(response.get("error"), dict) else {}
    return {"bytes": len(body), "json_object": True, **{key: _shown(value) for key, value in (
        ("provider_response_id", response.get("id")), ("returned_model", response.get("model")),
        ("provider_status", response.get("stop_reason") if provider == "anthropic" else response.get("status")),
        ("usage", response.get("usage")),
        ("incomplete_details", response.get("incomplete_details")), ("error_code", error.get("code")),
        ("error_type", error.get("type")))}, **(
            {"stop_reason": _shown(response.get("stop_reason")), "stop_sequence": _shown(response.get("stop_sequence"))}
            if provider == "anthropic" else {})}


def _named(event: dict) -> str | None:
    """The SHA-256 of the captured body a transport event names, if any."""
    capture = event.get("capture")
    return capture["body_sha256"] if isinstance(capture, dict) and isinstance(capture.get("body_sha256"), str) else None


def _transport_evidence(run: Path, attempts) -> dict:
    """One streamed pass over transport.jsonl for these attempts: each request logged (transport index, request hash),
    the events of the latest call from its first request on, whether a no_model_outcome event ended it (ended), the
    kinds of the events naming each captured body by its SHA-256, and how many captured lines of each body
    no_model_outcome events resolved."""
    evidence = {attempt: {"requests": [], "call": [], "open": False, "ended": None, "named": {}, "resolved": Counter()}
                for attempt in attempts}
    for event in _log_lines(run / "transport.jsonl"):
        attempt = event.get("attempt_id") if event else None
        if not isinstance(attempt, str) or attempt not in evidence:
            continue
        found, kind, sha = evidence[attempt], event.get("event"), _named(event)
        if kind == "request_started":
            found["requests"].append((event.get("transport_index"), event.get("request_hash")))
            if event.get("transport_index") == 0:
                found.update(call=[], open=True, ended=None)
        found["call"].append(event)
        if sha is not None:
            found["named"].setdefault(sha, []).append(kind)
        if kind == "no_model_outcome":
            found.update(open=False, ended=event)
            # It resolves one line of the body it names; one logged before these events named a capture, its call's body.
            found["resolved"].update(set(filter(None, map(_named, [event] if "capture" in event else found["call"]))))
    return evidence


def _since(event: dict, started: dict | None) -> bool:
    """Whether a transport event was logged once the in-flight marker had been written; False if either time is unread."""
    try:
        return datetime.fromisoformat(event["recorded_at"]) >= datetime.fromisoformat(started["started_at"])
    except (KeyError, TypeError, ValueError):
        return False


def _capture_entries(run: Path, slot: dict, settings: dict, requests: list):
    """Each body captured for the slot's attempt, in order: (its capture line, a summary of it without the model's
    text, parse_body's reading of it or None).

    A body is recoverable, so reconcile_run may record it as the generation, only if its line is intact and names the
    attempt, every request logged for the attempt carries the frozen payload's hash and one was logged at its transport
    index, and it reads as a model outcome of the pinned snapshot.
    """
    attempt, model = slot["attempt_id"], slot["model_snapshot"]
    expected = digest(response_payload(slot, settings))
    sent = {index for index, request_hash in requests if type(index) is int and request_hash == expected}
    for entry, line in enumerate(_log_lines(run / "captures" / f"{attempt}.jsonl"), 1):
        line, body, parsed = line or {}, None, None
        try:
            body = base64.b64decode(line.get("body_base64"), validate=True)
        except (TypeError, ValueError):
            pass
        summary = {"entry": entry, **{key: _shown(line.get(key)) for key in ("transport_index", "request_id", "body_sha256")},
                   "captured_at": _shown(line.get("recorded_at")), **({} if body is None else
                   _body_metadata(body, settings.get("provider", "openai")))}
        problem = ("the capture line is not a JSON object" if not line else
                   "the capture line names another attempt" if line.get("attempt_id") != attempt else
                   "its body_base64 does not decode" if body is None else
                   "the body does not match its body_sha256" if hashlib.sha256(body).hexdigest() != line.get("body_sha256") else None)
        if problem is None:
            parsed = parse_body(body, model, settings.get("provider", "openai"))
            status = summary["generation_status"] = parsed["generation_status"]
            if status == "provider_failure":
                summary["prompt_rejected"] = bool(parsed.get("prompt_rejected") or prompt_rejected(parsed["provider_response"], None))
            problem = ("a logged request for this attempt is not the frozen one" if any(h != expected for _, h in requests) else
                       "no request is logged at its transport index" if type(line.get("transport_index")) is not int
                       or line["transport_index"] not in sent else
                       f"unreadable: {parsed['unreadable']}" if status is None else
                       "a provider failure, not a model outcome" if status not in GENERATION_OUTCOMES else
                       f"the response is from {parsed.get('returned_model')!r}, not the pinned snapshot"
                       if parsed.get("returned_model") != model else None)
        summary.update(recoverable=problem is None, reason=problem)
        yield line, summary, parsed


def reconcile_run(run: Path, *, attempt: str | None = None, apply: bool = False, release_unreadable: bool = False) -> dict:
    """Resolve the calls whose in-flight marker generate kept, from their logged evidence alone: no request is made.

    Nothing changes unless apply. A marker locked by a running command is left alone, and one whose attempt already has
    a generation, or whose call's no_model_outcome generate already logged, is removed. Otherwise the bodies captured
    for the attempt that no_model_outcome events have not resolved, one line each, are decoded, and the first
    recoverable one (_capture_entries) is recorded as the generation generate would have recorded, naming its capture.
    Without one the call's outcome stays unknown and its marker held, unless release_unreadable: then the call is logged
    as no_model_outcome as generate would log it, naming the newest of those bodies and the call's last logged error,
    so a later generate sends the attempt ID again unless the prompt was rejected. Each change is first appended to
    amendments.jsonl.
    """
    manifest = load_run(run)
    slots = {slot["attempt_id"]: slot for slot in manifest["slots"]}
    if attempt is not None and attempt not in slots:
        raise ValueError(f"Not a scheduled attempt ID: {attempt}")
    markers = [path for path in sorted((run / "inflight").glob("*.json")) if attempt in (None, path.stem)]
    if attempt is not None and not markers:
        raise ValueError(f"Attempt {attempt} has no in-flight marker")
    with contextlib.ExitStack() as locks:
        # Each abandoned marker is locked before its evidence is read, so no command can add to that evidence meanwhile.
        abandoned = set()
        for marker in markers:
            lock = _abandoned(marker, protocol=False) if marker.stem in slots else None
            if lock is not None:
                locks.callback(os.close, lock)
                abandoned.add(marker)
        evidence = _transport_evidence(run, {marker.stem for marker in abandoned})
        entries = []
        for marker in markers:
            if marker in abandoned:
                entries.append(_reconcile(run, marker, slots[marker.stem], manifest["config"]["generation"],
                                          evidence[marker.stem], apply=apply, release_unreadable=release_unreadable))
            elif marker.stem not in slots:
                entries.append({"attempt_id": marker.stem, "action": "none", "finding": "not a scheduled attempt"})
            elif marker.exists():
                entries.append({"attempt_id": marker.stem, "action": "none",
                                "finding": "a running generate or reconcile holds its lock"})
    return {"run": str(run), "applied": apply, "in_flight_markers": len(markers),
            "actions": dict(sorted(Counter(entry["action"] for entry in entries).items())), "attempts": entries}


def _reconcile(run: Path, marker: Path, slot: dict, settings: dict, evidence: dict, *, apply: bool,
               release_unreadable: bool) -> dict:
    """reconcile_run for one abandoned marker, whose lock the caller holds."""
    attempt = slot["attempt_id"]
    try:
        started = read_json(marker)
    except (OSError, ValueError):
        started = None
    started = started if isinstance(started, dict) and unstorable(started) is None else None
    amend = lambda resolution, **fields: append_event(run / "amendments.jsonl", {
        "kind": "in_flight_reconciliation", "attempt_id": attempt, "resolution": resolution, "marker": started,
        **fields}, once=True)
    ended = evidence["ended"]
    # generate logged the outcome of the call this marker was written for, then stopped before removing it.
    outcome_logged = ended is not None and "reconciliation" not in ended and _since(ended, started)
    entry = {"attempt_id": attempt, "marker": started, "call": [{key: _shown(event[key]) for key in CALL_FIELDS if key in event}
             for event in evidence["call"]] if evidence["open"] or outcome_logged else []}
    target = generation_path(run, attempt)
    if target.exists():
        # A command stopped between recording the generation and removing the marker: nothing can be resent.
        generation = verified_generation(read_json(target), slot)
        entry.update(action="remove_marker", finding="the attempt already has a generation")
        if apply:
            amend("generation_already_recorded", raw_response_hash=generation["raw_response_hash"])
            marker.unlink()
        return entry
    logged = ended.get("reconciliation") if ended else None
    if started is not None and isinstance(logged, dict) and logged.get("marker") == started:
        # A reconcile stopped between logging this marker's release and removing it.
        entry.update(action="remove_marker", finding="transport.jsonl already logs its release")
        if apply:
            marker.unlink()
        return entry
    if outcome_logged:
        # Its no_model_outcome event stands, as for a release: nothing is logged in transport.jsonl again.
        entry.update(action="remove_marker", finding="transport.jsonl already logs its outcome")
        if apply:
            amend("outcome_already_logged")
            marker.unlink()
        return entry
    # Each resolution is of one captured line, the earliest of its body, so a later call's identical bytes are still read.
    resolved, considered = evidence["resolved"].copy(), []
    for found in _capture_entries(run, slot, settings, evidence["requests"]):
        sha = found[0].get("body_sha256")
        if isinstance(sha, str) and resolved[sha] > 0:
            resolved[sha] -= 1
        else:
            considered.append(found)
    entry["captures"] = [summary for _, summary, _ in considered]
    chosen = next((found for found in considered if found[1]["recoverable"]), None)
    if chosen is not None:
        line, summary, parsed = chosen
        capture = {"path": f"captures/{attempt}.jsonl", "body_sha256": line["body_sha256"]}
        provenance = {"marker": started, "transport_index": line["transport_index"], "request_id": line.get("request_id"),
                      "captured_at": line.get("recorded_at"), "request_hash": digest(response_payload(slot, settings))}
        entry.update(action="record", capture=capture, finding=f"captured entry {summary['entry']} reads as "
                     f"{parsed['generation_status']!r} from the pinned snapshot")
        if apply:
            raw = parsed.pop("raw_response")
            amend("generation_recorded", capture=capture, generation_status=parsed["generation_status"],
                  raw_response_hash=digest(raw), **{key: provenance[key] for key in provenance if key != "marker"})
            record_generation(run, slot, raw, **parsed, response_capture=capture, generation_settings=settings,
                              reconciliation=provenance)
            marker.unlink()
        return entry
    line, summary, parsed = considered[-1] if considered else ({}, None, None)
    unknown = next((event for event in reversed(entry["call"]) if event.get("event") == "outcome_unknown"), None)
    detail = unknown and (unknown.get("reason") or f"{unknown.get('type')}: {unknown.get('message')}")
    # As generate would log the call: with the last error it logged, rejecting the prompt if the captured body or an
    # HTTP 400 it logged did.
    error = next((event for event in reversed(evidence["call"] if evidence["open"] else [])
                  if event.get("event") in ("http_error", "request_not_sent")), None)
    last_error = error and _shown({key: error[key] for key in error
                                   if key not in ("attempt_id", "transport_index", "event", "recorded_at")})
    failed = parsed is not None and parsed["generation_status"] == "provider_failure"
    provider_response = parsed["provider_response"] if failed else None
    refused = bool(failed and summary["prompt_rejected"]) or prompt_rejected(provider_response, last_error)
    stopped = "no response body was captured, and the call stopped without a logged outcome" + (
        "; its logged HTTP 400 rejected the prompt as invalid_prompt" if refused else "")
    # Without an open call, none was sent since the marker was written, unless the marker cannot tell when that was.
    finding = (summary["reason"] if summary else
               f"no response body was captured; the call logged outcome_unknown ({detail})" if unknown else
               stopped if evidence["open"] else
               "no request is logged for this call, so none was sent" if started is not None or not evidence["requests"] else
               "its marker cannot be read, so whether its call sent a request is unknown")
    entry.update(action="release" if release_unreadable else "keep", finding=finding)
    if not release_unreadable:
        entry["next"] = "--apply --release-unreadable releases it, so a later generate may send this attempt ID again"
    if not (apply and release_unreadable):
        return entry
    capture = {"path": f"captures/{attempt}.jsonl", "body_sha256": line["body_sha256"]} if isinstance(
        line.get("body_sha256"), str) else None
    amend("released_without_model_outcome", reason=finding, capture=capture)
    # A later generate resends the attempt ID, unless the provider rejected the prompt itself.
    append_event(run / "transport.jsonl", {
        "attempt_id": attempt, "event": "no_model_outcome", "capture": capture, "last_error": last_error,
        "provider_response": provider_response, "prompt_rejected": refused,
        "reconciliation": {"marker": started, "reason": finding}})
    marker.unlink()
    return entry


def inspect_captures(run: Path, *, attempt: str | None = None) -> dict:
    """The run's captured response bodies decoded for people, read-only (_capture_entries), with the transport events
    that name each one and whether it is the attempt's recorded generation; never the model's text or a credential."""
    manifest = load_run(run)
    slots = {slot["attempt_id"]: slot for slot in manifest["slots"]}
    if attempt is not None and attempt not in slots:
        raise ValueError(f"Not a scheduled attempt ID: {attempt}")
    directory = run / "captures"
    chosen = [slot for slot in manifest["slots"] if attempt in (None, slot["attempt_id"])
              and (directory / f"{slot['attempt_id']}.jsonl").exists()]
    evidence = _transport_evidence(run, {slot["attempt_id"] for slot in chosen})
    captures = []
    for slot in chosen:
        found, target = evidence[slot["attempt_id"]], generation_path(run, slot["attempt_id"])
        recorded = verified_generation(read_json(target), slot).get("response_capture") if target.exists() else None
        entries = []
        for line, summary, _ in _capture_entries(run, slot, manifest["config"]["generation"], found["requests"]):
            sha = line.get("body_sha256") if isinstance(line.get("body_sha256"), str) else None
            entries.append({**summary, "named_by": found["named"].get(sha, []),
                            "recorded_as_generation": isinstance(recorded, dict) and sha is not None
                            and recorded.get("body_sha256") == sha})
        captures.append({"attempt_id": slot["attempt_id"], "path": f"captures/{slot['attempt_id']}.jsonl",
                         "generation_recorded": target.exists(),
                         "in_flight_marker": (run / "inflight" / f"{slot['attempt_id']}.json").exists(), "entries": entries})
    unscheduled = sorted(path.name for path in directory.glob("*.jsonl") if path.stem not in slots) if attempt is None else []
    return {"run": str(run), "captures": captures, "unscheduled_capture_files": unscheduled}
