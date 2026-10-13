"""A frozen run manifest, immutable generations, and append-only evaluation revisions."""
from __future__ import annotations

import hashlib
import json
import random
import re
from collections import Counter
from collections.abc import KeysView
from datetime import datetime
from pathlib import Path

from .integrity import frozen_portfolio, is_content_digest, require_completed_pilot, require_implemented_policy, same_stored_response, verified_generation
from .io import digest, evaluator_hash, measurement_hash, now, read_json, reference_matches_task, same_measurement, task_input_hash, write_json
from .outcomes import GENERATION_OUTCOMES, PROVIDER_STATUSES, outcome_class
from .prompts import CONDITIONS, adapt_response, messages, prompt_policy_hash


def schedule(tasks: list[dict], config: dict, environment: dict, model: str) -> list[dict]:
    conditions = config.get("conditions", list(CONDITIONS))
    if conditions != list(CONDITIONS):
        raise ValueError("Core evaluation requires exactly the three protocol conditions in order")
    count = config.get("samples_per_goal_condition", 4)
    if not isinstance(count, int) or count < 1:
        raise ValueError("samples_per_goal_condition must be a positive integer")
    slots = []
    for task in tasks:
        for condition in conditions:
            prompt = messages(task, condition, environment["lean_toolchain"])
            if len(prompt[1]["content"].encode()) > config["generation"]["context_byte_cap"]:
                raise ValueError(f"Task exceeds frozen prompt byte cap: {task['task_id']}")
            for sample in range(count):
                identity = {"experiment": config["experiment"],
                            "protocol_version": config["protocol_version"],
                            "task_id": task["task_id"], "task_hash": digest(task),
                            "model_snapshot": model, "condition": condition,
                            "sample_index": sample, "config_hash": digest(config),
                            "environment_hash": environment["environment_hash"]}
                slots.append({**identity, "attempt_id": digest(identity), "messages": prompt,
                              "prompt_hash": digest(prompt)})
    random.Random(config.get("schedule_seed", 20261011)).shuffle(slots)
    return [{**slot, "schedule_index": index} for index, slot in enumerate(slots)]


def load_reference_checks(directory: Path) -> tuple[dict, dict | None]:
    """A verify-references directory's records by task, and its policy (None without policy.json)."""
    if not directory.is_dir():
        raise FileNotFoundError(f"Reference checks directory not found: {directory}")
    records = {r["task_id"]: r for p in sorted(directory.glob("*.json"))
               if isinstance(r := read_json(p), dict) and r.get("task_id")}
    return records, read_json(directory / "policy.json") if (directory / "policy.json").exists() else None


def reference_policy(policy: dict | None, environment: dict, measurement: dict) -> dict:
    """A verify-references policy made in this environment with these measurement settings."""
    policy = policy or {}
    if policy.get("kind") != "verify-references" or not policy.get("engine_hash"):
        raise ValueError("Reference checks must be a verify-references directory with its policy.json")
    if policy.get("environment_hash") != environment["environment_hash"]:
        raise ValueError("Reference checks were made in a different environment")
    if not same_measurement(policy, measurement):
        raise ValueError("Reference checks used different validation/measurement settings; reverify them with this configuration")
    return policy


def anchor_problems(task: dict, ref: dict | None, policy: dict, environment: dict, *, measurable: bool) -> list[str]:
    """The checks a task's reference record fails as its anchor; ["no record"] when it has none."""
    if ref is None:
        return ["no record"]
    return [field for field, ok in (
        ("record_kind", ref.get("record_kind") == "verified_reference"),
        ("validity_status", ref.get("validity_status") == "success"),
        ("metric_status", not measurable or ref.get("metric_status") == "available"),
        ("environment_hash", ref.get("environment_hash") == environment["environment_hash"]),
        ("engine_hash", ref.get("engine_hash") == policy["engine_hash"]),
        ("task identity", reference_matches_task(ref, task)),
        # The checked proof itself: a re-check after an engine fix must check the same one, and a
        # production freeze screens it for answer access.
        ("proof", isinstance(ref.get("proof"), str) and bool(ref["proof"].strip()))) if not ok]


def require_reference_anchors(tasks: list[dict], records: dict, policy: dict | None, environment: dict,
                              measurement: dict, *, measurable: bool) -> None:
    """Analyze's reference rules for every task's anchor, applied before anything depends on them."""
    policy = reference_policy(policy, environment, measurement)
    problems = [(task["task_id"], failed) for task in tasks if (failed := anchor_problems(
        task, records.get(task["task_id"]), policy, environment, measurable=measurable))]
    listed = lambda found: "; ".join(f"{task_id} ({', '.join(failed)})" for task_id, failed in found[:5]) + (
        f"; and {len(found) - 5} more" if len(found) > 5 else "")
    pending = [problem for problem in problems if problem[1] == ["no record"]]
    final = [problem for problem in problems if problem[1] != ["no record"]]
    message = []
    if pending:
        # No record: a checker incident left the task pending, and a rerun checks it again.
        message.append(f"Missing verified reference: {listed(pending)}. Finish verify-references first: every task needs a "
                       "verified record, so rerun it until it reports no checker incidents")
    if final:
        # A rerun skips existing records, so it cannot change a failed, unmeasurable or foreign one.
        message.append(f"Incompatible verified reference: {listed(final)}. Rerunning verify-references keeps these records: "
                       "correct or leave out a reference proof that failed or cannot be measured, and check the references "
                       "into a new directory")
    if message:
        raise ValueError(". ".join(message))


def lost_anchor(task: dict, ref: dict | None, policy: dict, environment: dict, *, measurable: bool) -> dict | None:
    """The re-checking engine's own verdict that a frozen proof is invalid (or, where required, unmeasurable)."""
    failed = anchor_problems(task, ref, policy, environment, measurable=measurable)
    if not failed or not set(failed) <= {"record_kind", "validity_status", "metric_status"}:
        return None
    rejected = ref.get("record_kind") == "failed_reference" and outcome_class(ref.get("validity_status")) == "model_failure"
    if failed != ["metric_status"] and not rejected:
        return None
    return {"validity_status": ref.get("validity_status"), "metric_status": ref.get("metric_status"),
            "record_digest": digest(ref)}


def rechecked_anchors(manifest: dict, directory: Path, engine_hash: str) -> dict:
    """Bind the frozen reference proofs, re-checked by a changed engine, as one evaluation version's anchors.

    Only the engine may differ: the proofs, tasks, environment and measurement settings are the frozen ones.
    A task whose frozen proof that engine rejects (or, in production, cannot measure) is bound unanchored:
    analyze then counts its reference as missing and never substitutes another.
    """
    if manifest.get("reference_records_hash") is None:
        raise ValueError("This run was frozen without reference checks; evaluate without --reference-checks")
    if manifest.get("reference_proof_hashes") is None:
        raise ValueError("This run's manifest does not record its frozen reference proofs, so it cannot bind re-checked references")
    if engine_hash == manifest.get("reference_engine_hash"):
        raise ValueError("This engine verified the frozen references; evaluate without --reference-checks")
    records, policy = load_reference_checks(directory)
    policy, measurable = reference_policy(policy, manifest["environment"], manifest), not manifest["development"]
    if policy["engine_hash"] != engine_hash:
        raise ValueError("Re-checked references must come from the engine being evaluated")
    unanchored = {t["task_id"]: verdict for t in manifest["tasks"] if (verdict := lost_anchor(
        t, records.get(t["task_id"]), policy, manifest["environment"], measurable=measurable))}
    require_reference_anchors([t for t in manifest["tasks"] if t["task_id"] not in unanchored], records, policy,
                              manifest["environment"], manifest, measurable=measurable)
    changed = [t["task_id"] for t in manifest["tasks"]
               if digest(records[t["task_id"]].get("proof")) != manifest["reference_proof_hashes"].get(t["task_id"])]
    if changed:
        raise ValueError("Re-checked references must check the proofs frozen with the run: " + ", ".join(changed))
    return {"reference_policy_hash": digest(policy),
            "reference_anchors": {t["task_id"]: digest(records[t["task_id"]]) for t in manifest["tasks"]
                                  if t["task_id"] not in unanchored},
            "unanchored_reference_tasks": dict(sorted(unanchored.items()))}


def create_run(path: Path, tasks: list[dict], config: dict, environment: dict, *,
               split: str, model: str, development: bool = False,
               reference_records: dict | None = None, reference_policy: dict | None = None,
               engine_hash: str | None = None, pilot_report: dict | None = None) -> dict:
    if path.exists() and (path / "manifest.json").exists():
        raise FileExistsError(f"Run already frozen: {path}; use the existing run to resume")
    require_implemented_policy(config)
    # As baseline and analyze --baseline will check it: otherwise no baseline could ever match the run.
    frozen_portfolio(config)
    adapter = config["generation"].get("response_adapter")
    try:
        adapt_response("", adapter)
    except ValueError:
        # evaluate adapts every response with it, so an unknown adapter would fail only after paid generation.
        raise ValueError(f"Unsupported generation.response_adapter {adapter!r}: prompts.adapt_response does not implement it") from None
    if config["generation"].get("model_snapshot") not in (None, model):
        raise ValueError("Requested model differs from generation.model_snapshot in the protocol")
    if not development:
        from .dataset import answer_access_errors, readiness_errors
        if not config["generation"].get("snapshot_pinned"):
            raise ValueError("Production run requires an explicitly pinned model snapshot")
        if not config["generation"].get("settings_resolved"):
            raise ValueError("Explicitly resolve the model's reasoning and sampling settings before freezing")
        if split == "main":
            require_completed_pilot(config, pilot_report, model)
        if environment.get("hash_mode") != "full":
            raise ValueError("Production run requires full source and compiled environment hashes")
        if config["validation"].get("isolation") != "docker":
            raise ValueError("Production run requires the enforced Docker CPU and memory profile")
        pending = readiness_errors(tasks)
        if pending:
            raise ValueError("Verify/audit every task before freezing a production run: " + "; ".join(pending))
        if environment.get("runtime_backend") != "docker":
            raise ValueError("Production environment hashes must be collected inside the pinned runtime image")
        if environment.get("docker_image") != config["validation"].get("docker_image"):
            raise ValueError("Runtime image differs from the image whose environment was indexed")
    for key in ("lean_toolchain", "mathlib_commit"):
        if config["environment"].get(key) != environment.get(key):
            raise ValueError(f"Environment {key} differs from the frozen protocol")
    # Lean runs in Docker, the runner's default, exactly when the environment record was collected in the image.
    isolation, image = config["validation"].get("isolation", "docker"), config["validation"].get("docker_image")
    if (environment.get("runtime_backend") == "docker") != (isolation == "docker"):
        raise ValueError(f"Environment runtime_backend {environment.get('runtime_backend')!r} does not match validation.isolation "
                         f"{isolation!r}: use a record collected inside the Docker image exactly when Lean runs in Docker")
    if isolation == "docker":
        if not is_content_digest(image):
            raise ValueError(f"validation.docker_image {image!r} is not an immutable content digest "
                             "(sha256: and 64 lowercase hex digits, optionally after name@)")
        if environment.get("docker_image") != image:
            raise ValueError("Container runs must use environment evidence collected inside the same pinned image")
        if environment.get("source_exclusion_audit") != {"lean_sources": 0, "git_directories": 0}:
            raise ValueError("Container runtime must pass the forbidden-source exclusion audit")
    selected = [t for t in tasks if t["split"] == split]
    if not selected:
        raise ValueError(f"No tasks in split {split}")
    measurement = {"measurement_hash": measurement_hash(config), "config_hash": digest(config)}
    if not development and reference_records is None:
        raise ValueError("Production freezing requires actual verified reference records for this environment")
    if reference_records is not None or reference_policy is not None:
        # Apply analyze's reference rules now, before any paid generation: a verified record for every task,
        # development included, so no anchor is left unbound. Production anchors must also be measurable.
        require_reference_anchors(selected, reference_records or {}, reference_policy, environment, measurement,
                                  measurable=not development)
        # The checks bind their engine (reference_engine_hash), so a run frozen for another could never be evaluated.
        if engine_hash not in (None, reference_policy["engine_hash"]):
            raise ValueError("Engine differs from the one that verified the reference checks; freeze with that engine")
    if not development:
        leaked = answer_access_errors(selected, reference_records.values())
        if leaked:
            raise ValueError("Reference proofs are copyable from the model prompts: " + "; ".join(leaked))
    slots = schedule(selected, config, environment, model)
    manifest = {"schema_version": 1, "created_at": now(), "development": development,
                "split": split, "config": config, "environment": environment,
                "tasks": selected, "model_snapshot": model,
                "prompt_policy_hash": prompt_policy_hash(), "task_manifest_hash": digest(selected),
                "config_hash": digest(config), "evaluator_source_hash": evaluator_hash(), "scheduled_attempts": len(slots),
                # evaluator_source_hash is the Python package (src/lean_compression/*.py); this is the Lean checker evaluate must use.
                "engine_hash": engine_hash if reference_policy is None else reference_policy["engine_hash"],
                "measurement_hash": measurement["measurement_hash"],
                "reference_records_hash": digest(reference_records) if reference_records is not None else None,
                # Each frozen task's reference record; analyze accepts only these anchors. The proofs let a
                # checker fix re-check the same anchors under a new engine (rechecked_anchors).
                "reference_anchors": None if reference_records is None else {
                    task["task_id"]: digest(reference_records[task["task_id"]]) for task in selected},
                "reference_proof_hashes": None if reference_records is None else {
                    task["task_id"]: digest(reference_records[task["task_id"]].get("proof")) for task in selected},
                "reference_policy_hash": digest(reference_policy) if reference_policy is not None else None,
                "reference_engine_hash": None if reference_policy is None else reference_policy["engine_hash"],
                "slots": slots}
    manifest["run_hash"] = digest(manifest)
    write_json(path / "manifest.json", manifest)
    return manifest


def load_run(path: Path) -> dict:
    value = read_json(path / "manifest.json")
    expected = value["run_hash"]
    unhashed = {k: v for k, v in value.items() if k != "run_hash"}
    if digest(unhashed) != expected:
        raise ValueError("Frozen run manifest was modified; create a versioned amendment/new run")
    return value


def generation_path(path: Path, attempt_id: str) -> Path:
    return path / "generations" / f"{attempt_id}.json"


def evaluation_directory(path: Path, version: str) -> Path:
    if not isinstance(version, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", version):
        raise ValueError("Evaluation version must be one safe directory name")
    return path / "evaluations" / version


def stored_generation(path: Path, slot: dict, raw: str, **metadata) -> dict | None:
    """Return an identical stored generation, if any; differing content is an error."""
    target = generation_path(path, slot["attempt_id"])
    if not target.exists():
        return None
    existing = read_json(target)
    if not same_stored_response(existing, slot, raw) or any(
            digest(existing.get(key)) != digest(value) for key, value in metadata.items()):
        raise FileExistsError(f"Attempt already has a different generation: {slot['attempt_id']}")
    return existing


def record_generation(path: Path, slot: dict, raw: str, **metadata) -> dict:
    existing = stored_generation(path, slot, raw, **metadata)
    if existing is not None:
        return existing
    value = {"attempt_id": slot["attempt_id"], "prompt_hash": slot["prompt_hash"],
             "created_at": now(), "raw_response": raw, "raw_response_hash": digest(raw), **metadata}
    write_json(generation_path(path, slot["attempt_id"]), value)
    return value


def without_graph(record: dict) -> dict:
    """The record minus its dependency graph (extraction.nodes), which only scoring and the audit bundle read."""
    extraction = record.get("extraction")
    if not isinstance(extraction, dict) or "nodes" not in extraction:
        return record
    return {**record, "extraction": {key: value for key, value in extraction.items() if key != "nodes"}}


def with_graph(path: Path | None, row: dict) -> dict:
    """Restore a graph that records(graphs=False) left out, from the evaluation file the row was read from."""
    source = row.get("evaluation_record")
    if source is None:
        return row
    if path is None:
        raise ValueError("A row read without its dependency graph needs its run directory")
    data = (Path(path) / source["path"]).read_bytes()
    if hashlib.sha256(data).hexdigest() != source["sha256"]:
        raise ValueError(f"Evaluation record changed after it was read: {source['path']}")
    extraction = json.loads(data.decode("utf-8")).get("extraction")
    if not isinstance(extraction, dict) or "nodes" not in extraction:
        return row
    return {**row, "extraction": {**row["extraction"], "nodes": extraction["nodes"]}}


def records(path: Path, evaluation_version: str = "v1", *, graphs: bool = True) -> list[dict]:
    """Materialize all scheduled outcomes, including never generated/never evaluated slots.

    With graphs=False an evaluated row leaves out extraction.nodes and names its evaluation file
    in evaluation_record (path within the run and SHA-256), so memory does not grow with graph size.
    """
    manifest = load_run(path)
    directory = evaluation_directory(path, evaluation_version)
    output = []
    for slot in manifest["slots"]:
        identity = {k: v for k, v in slot.items() if k != "messages"}
        evaluation = directory / f"{slot['attempt_id']}.json"
        genpath = generation_path(path, slot["attempt_id"])
        generation = verified_generation(read_json(genpath), slot) if genpath.exists() else None
        if evaluation.exists():
            data = evaluation.read_bytes()
            checked = json.loads(data.decode("utf-8"))
            if checked.get("attempt_id") != slot["attempt_id"]:
                raise ValueError("Evaluation record identity does not match its scheduled attempt")
            # Reports read the embedded copy, so it must be the verified stored generation.
            if generation is None or not checked.get("generation_hash") == digest(checked.get("generation")) == digest(generation):
                raise ValueError(f"Evaluation record does not embed its stored generation: {slot['attempt_id']}")
            for key in identity:
                if key in checked and checked[key] != identity[key]:
                    raise ValueError(f"Evaluation record changes frozen identity field {key}")
            row = {**checked, **identity}
            validation = checked.get("validation")
            if checked.get("validity_status") == "success" and not (
                    isinstance(validation, dict) and validation.get("validity_status") == "success"):
                # Only a Lean verdict is a success. Older imports could copy a generation label here.
                row.update(validity_status="unverified_success", scheduled_outcome_incomplete=True)
            if not graphs:
                row = {**without_graph(row), "evaluation_record": {
                    "path": evaluation.relative_to(path).as_posix(), "sha256": hashlib.sha256(data).hexdigest()}}
            output.append(row)
        else:
            status = "not_generated" if generation is None else generation.get("generation_status", "completed")
            if status == "completed":
                status = "not_evaluated"
            elif generation is not None and status not in GENERATION_OUTCOMES and status not in PROVIDER_STATUSES:
                # A generation label is never a verdict, and an unknown one is never a model failure.
                status = "unrecognized_generation_status"
            output.append({**identity, "validity_status": status, "metric_status": "unavailable",
                           "metrics": None, "generation": generation,
                           "scheduled_outcome_incomplete": outcome_class(status) == "incomplete"})
    return output


def _log_lines(path: Path):
    """Each non-blank line of an append-only JSONL log, read one at a time: its event, or None for a line that is
    not a JSON object, which callers count and never guess at. Memory is bounded by the longest line, not the log.

    Only "\\n" ends an event: JSON text keeps U+2028, U+2029 and U+0085 raw, which str.splitlines() would split.
    """
    if not path.exists():
        return
    with path.open("rb") as stream:
        for line in stream:
            if not line.strip():
                continue
            try:
                event = json.loads(line.decode("utf-8", errors="replace"))
            except (ValueError, RecursionError):
                event = None
            yield event if isinstance(event, dict) else None


def logged_evaluations(path: Path, evaluation_version: str) -> set[str]:
    """The attempts whose attempt_evaluated event of this version the run's events.jsonl holds, streamed."""
    return {event["attempt_id"] for event in _log_lines(path / "events.jsonl")
            if event is not None and event.get("event") == "attempt_evaluated"
            and event.get("evaluation_version") == evaluation_version and isinstance(event.get("attempt_id"), str)}


def _log_events(path: Path) -> tuple[list[dict], int]:
    """Events of an append-only JSONL log, and the number of lines that are not JSON objects."""
    events = list(_log_lines(path))
    return [event for event in events if event is not None], events.count(None)


def _provider_log(path: Path, slots: list[dict], events=None) -> dict:
    """One pass over the run's transport.jsonl (streamed unless its events are given): counts of events without a
    model outcome, unreadable lines, the scheduled attempts they name and, for each prompt rejected as invalid_prompt,
    its latest rejection time, ordered by that rejection, oldest first."""
    prompt_of = {slot["attempt_id"]: slot["prompt_hash"] for slot in slots}
    summary = {"no_model_outcome": 0, "outcome_unknown": 0, "unreadable": 0, "outages": set(), "refused": {}}
    for event in _log_lines(path / "transport.jsonl") if events is None else events:
        if event is None:
            summary["unreadable"] += 1
            continue
        kind, attempt_id = event.get("event"), event.get("attempt_id")
        if kind not in ("no_model_outcome", "outcome_unknown"):
            continue
        summary[kind] += 1
        if isinstance(attempt_id, str) and attempt_id in prompt_of:
            summary["outages"].add(attempt_id)
            if kind == "no_model_outcome" and event.get("prompt_rejected") is True:
                prompt = prompt_of[attempt_id]
                summary["refused"].pop(prompt, None)  # in the order of each prompt's latest rejection
                summary["refused"][prompt] = event.get("recorded_at")
    return summary


def _still_refused(path: Path, slots: list[dict], refused: dict) -> KeysView[str]:
    """The refused prompts none of whose slots has a generation recorded after the prompt's latest rejection."""
    def taken(slot):
        """Whether this slot's generation was recorded after its prompt's latest rejection; not if a time is unreadable."""
        target = generation_path(path, slot["attempt_id"])
        try:
            return target.exists() and (datetime.fromisoformat(read_json(target)["created_at"])
                                        > datetime.fromisoformat(refused[slot["prompt_hash"]]))
        except (OSError, ValueError, TypeError, KeyError):
            return False

    accepted = {slot["prompt_hash"] for slot in slots if slot["prompt_hash"] in refused and taken(slot)}
    return dict.fromkeys(prompt for prompt in refused if prompt not in accepted).keys()


def rejected_prompts(path: Path, slots: list[dict], transport: list[dict] | None = None) -> KeysView[str]:
    """Prompt hashes the provider rejected as invalid_prompt, per the run's transport.jsonl, and has not taken since.

    The samples of a task and condition share a prompt, so one rejection stands for all of its slots until a generation
    of any of them is recorded after it. The set is ordered by each prompt's latest rejection, oldest first.
    transport is that log's already-parsed events, if the caller has them; otherwise the log is streamed.
    """
    return _still_refused(path, slots, _provider_log(path, slots, transport)["refused"])


def _incident_summary(path: Path, log: Path, directory: Path, scheduled: set) -> dict:
    """A checker incident log's events, and the latest incident of each attempt still without a record in directory.

    The log is streamed: only counts and each scheduled attempt's latest status, phase and reason are kept.
    """
    def reason(event):
        phase = event.get("extraction" if event.get("failure_phase") == "extraction" else "validation")
        phase = phase if isinstance(phase, dict) else {}
        text = str(event.get("failure_reason") or phase.get("failure_reason") or phase.get("error")
                   or event.get("error") or "").strip()
        return text.splitlines()[0][:200] if text else "unreported"

    events = unreadable = 0
    latest = {}
    for event in _log_lines(log):
        if event is None:
            unreadable += 1
            continue
        events += 1
        attempt_id = event.get("attempt_id")
        if isinstance(attempt_id, str) and attempt_id in scheduled:
            latest[attempt_id] = (str(event.get("validity_status")), str(event.get("failure_phase", "validation")), reason(event))
    pending = [state for attempt_id, state in latest.items() if not (directory / f"{attempt_id}.json").exists()]
    return {"log": log.relative_to(path).as_posix(), "events": events, "unreadable_lines": unreadable,
            "attempts": len(latest), "unresolved_attempts": len(pending),
            "unresolved_statuses": dict(sorted(Counter(status for status, _, _ in pending).items())),
            "unresolved_phases": dict(sorted(Counter(phase for _, phase, _ in pending).items())),
            "unresolved_reasons": dict(sorted(Counter(text for _, _, text in pending).items()))}


def logged_outages(path: Path, evaluation_version: str = "v1") -> dict:
    """Why slots are pending: checker incidents of this version and provider calls without a model outcome.

    Such a slot has no evaluation or generation record, so records() calls it not_evaluated or
    not_generated. An attempt is unresolved while it still lacks that record. A rescored version copies
    records, never incidents, so the evaluated version it was rescored from reports the incidents it inherits.
    Every slot of a prompt the provider rejected is a provider outage, including those generate did not send,
    until a generation of that prompt is recorded after the rejection.
    """
    slots = load_run(path)["slots"]
    scheduled = {slot["attempt_id"] for slot in slots}
    directory = evaluation_directory(path, evaluation_version)
    source, seen = directory, {evaluation_version}
    while (source / "policy.json").exists():
        policy = read_json(source / "policy.json")
        if policy.get("kind") != "rescore" or policy.get("source_version") in seen:
            break
        seen.add(policy["source_version"])
        source = evaluation_directory(path, policy["source_version"])
    transport = _provider_log(path, slots)
    refused = _still_refused(path, slots, transport["refused"])
    rejected = {slot["attempt_id"] for slot in slots if slot["prompt_hash"] in refused}
    pending = lambda attempts: sum(not generation_path(path, a).exists() for a in attempts)
    return {"checker_incidents": _incident_summary(path, directory / "incidents.jsonl", directory, scheduled),
            "inherited_checker_incidents": None if source == directory else {
                "source_version": source.name, **_incident_summary(path, source / "incidents.jsonl", directory, scheduled)},
            "provider_outages": {
                "log": "transport.jsonl", "no_model_outcome_events": transport["no_model_outcome"],
                "outcome_unknown_events": transport["outcome_unknown"], "unreadable_lines": transport["unreadable"],
                "unresolved_attempts": pending(transport["outages"] | rejected),
                "prompt_rejected_attempts": pending(rejected),
                "in_flight_markers": len(list((path / "inflight").glob("*.json")))}}
