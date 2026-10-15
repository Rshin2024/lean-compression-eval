"""Credential-free, one-use handoff to the existing credential parent.

Never runs a provider/controller, releases a claim, or changes a frozen run.
Publishes one existing resume control only after all fresh work has finished.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import uuid

HERE = Path(__file__).resolve().parent
WORK = HERE.parents[1]
REPO = WORK.parents[1] / "outputs/lean-compression-eval"
sys.path.insert(0, str(REPO / "src"))
from lean_compression.io import digest, evaluator_hash, file_hash, read_json
from lean_compression.integrity import verified_generation
from lean_compression.store import load_run

APPROVED = {
    "3932f38e255ba6ef806b0110719bacbb05c0bbba4ae9a5b2e3e75ff5417045ea",
    "fb6e512ee5aea4245f7391677c7f6f9b2621c31a1c6fc7a2f740b859e208cb84",
    "bca7a27c55aaa42f4be945bc92f4d52f8201d6bf3e4cd00f363da441cfd5de2f",
    "47fe381fab717179bd251a226b46f49c6ce6c071a2445c99fc053132f2410bc2",
    "142eef3bf02eee2f8233a914b1e9d629df06559474f19ba476eafd3101882fbd",
    "88e446649aa37e9d900b48130d580c5c87d9914751e419053c3df96044a62228",
    "5d3b679dbd40b955566e8698dd1289127bc1c23077a323a3ad54a88bb1539206",
    "68b64b8ac4296ed58c4c3a281986f8f8a5aa2c2a23d05a1ec585eedc2d961a5f",
}


def stamp(path):
    s = path.stat()
    return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)


def exclusive_json(path, value):
    """Publish complete bytes only if absent, without overwriting other controls."""
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".resume-publication-")
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(value, stream, sort_keys=True, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)


def bound(path, expected):
    if file_hash(path) != expected:
        raise ValueError("Hash-bound artifact changed: " + path.name)
    return read_json(path)


def source_and_receipt_checks(context):
    plan = context["watch_plan"]
    if file_hash(context["plan_path"]) != context["plan_sha256"]:
        raise ValueError("Resume watcher plan changed")
    if file_hash(Path(__file__)) != plan["watcher_sha256"]:
        raise ValueError("Resume watcher changed")
    launch = bound(WORK / "launch_ready.json", plan["launch_plan_sha256"])
    if launch["source_hash"] != evaluator_hash() or launch["engine_hash"] != file_hash(REPO / "lean/Evaluator.lean"):
        raise ValueError("Frozen evaluator changed")
    for relative, expected in launch["operational_source_hashes"].items():
        path = (WORK / relative).resolve()
        if not path.is_relative_to(WORK) or file_hash(path) != expected:
            raise ValueError("Pinned generation source changed")
    for relative, expected in plan["recovery_source_hashes"].items():
        path = (WORK / relative).resolve()
        if not path.is_relative_to(WORK) or file_hash(path) != expected:
            raise ValueError("Reviewed recovery source changed")
    for entry in plan["releases"]:
        path = (WORK / entry["receipt_path"]).resolve()
        proposal_path = (WORK / entry["proposal_path"]).resolve()
        if not path.is_relative_to(WORK) or not proposal_path.is_relative_to(WORK):
            raise ValueError("Release evidence path escaped session")
        receipt = bound(path, entry["receipt_sha256"])
        proposal = bound(proposal_path, entry["proposal_sha256"])
        if (receipt.get("attempt_id") != entry["attempt_id"] or proposal.get("attempt_id") != entry["attempt_id"]
                or receipt.get("proposal_sha256") != entry["proposal_sha256"]
                or receipt.get("state") != "released_once" or receipt.get("maximum_release_count") != 1
                or receipt.get("original_marker_removed") is not True or receipt.get("new_model_calls") != 0
                or receipt.get("controller_resume_requested") is not False or receipt.get("original_charge_usd") is not None):
            raise ValueError("Release receipt does not attest the reviewed one-use action")
    return launch


def load_context(plan_path, plan_sha256):
    plan = bound(plan_path, plan_sha256)
    if len(plan.get("releases", [])) != 8 or {x["attempt_id"] for x in plan["releases"]} != APPROVED:
        raise ValueError("Resume scope must be exactly the eight approved attempts")
    context = {"watch_plan": plan, "plan_path": plan_path, "plan_sha256": plan_sha256, "known": {}}
    launch = source_and_receipt_checks(context)
    slots, runs = {}, {}
    for entry in launch["runs"]:
        run = (REPO / "runs" / entry["directory"]).resolve()
        if run.parent != (REPO / "runs").resolve() or file_hash(run / "manifest.json") != entry["manifest_sha256"]:
            raise ValueError("Frozen manifest changed")
        manifest = load_run(run)
        if manifest["run_hash"] != entry["run_hash"]:
            raise ValueError("Run identity changed")
        runs[run] = (stamp(run / "manifest.json"), manifest)
        for slot in manifest["slots"]:
            if slot["attempt_id"] in slots:
                raise ValueError("Duplicate scheduled identifier")
            slots[slot["attempt_id"]] = (run, slot)
    if len(slots) != 9600 or len(runs) != 8 or not APPROVED <= set(slots):
        raise ValueError("Expected the exact new9600-attempt cohort")
    snapshot = bound(HERE / plan["baseline_generations"]["path"], plan["baseline_generations"]["sha256"])
    if set(snapshot["sha256_by_attempt"]) - (set(slots) - APPROVED):
        raise ValueError("Baseline includes unscheduled or approved retry attempts")
    context.update(slots=slots, runs=runs, baseline=snapshot["sha256_by_attempt"])
    return context


def collected_generations(context):
    present, hashes = set(), {}
    for run, (original, manifest) in context["runs"].items():
        if stamp(run / "manifest.json") != original:
            raise ValueError("Frozen manifest filesystem identity changed")
        scheduled = {s["attempt_id"] for s in manifest["slots"]}
        if {p.stem for p in (run / "generations").glob("*.json")} - scheduled:
            raise ValueError("Unscheduled generation files exist")
    for attempt, (run, slot) in context["slots"].items():
        path = run / "generations" / (attempt + ".json")
        if not path.exists():
            continue
        current = stamp(path)
        known = context["known"].get(attempt)
        if known is None or known["stamp"] != current:
            sha = file_hash(path)
            if ((attempt in context["baseline"] and context["baseline"][attempt] != sha)
                    or (known is not None and known["sha256"] != sha)):
                raise ValueError("A previously recorded generation changed")
            verified_generation(read_json(path), slot)
            context["known"][attempt] = {"sha256": sha, "stamp": current}
        present.add(attempt)
        hashes[attempt] = context["known"][attempt]["sha256"]
    return present, hashes


def readiness(context):
    source_and_receipt_checks(context)
    plan = context["watch_plan"]
    parent = read_json(WORK / "generation-parent-status.json")
    if parent.get("parent_pid") != plan["credential_parent_pid"]:
        raise ValueError("Credential parent identity changed")
    if parent.get("state") != "holding_credentials_for_explicit_resume":
        return {"ready": False, "reason": "generation_child_still_running"}
    if parent.get("child_pid") != plan["original_generation_child_pid"]:
        raise ValueError("A different or replacement child has already run")
    os.kill(parent["parent_pid"], 0)
    status = read_json(WORK / "generation-status.json")
    if status.get("pid") != plan["original_generation_child_pid"] or status.get("active_workers") != 0:
        return {"ready": False, "reason": "generation_child_has_not_finished_cleanly"}
    if any(list((run / "inflight").glob("*.json")) for run in context["runs"]):
        return {"ready": False, "reason": "an_inflight_marker_remains"}
    present, hashes = collected_generations(context)
    missing = set(context["slots"]) - present
    if missing != APPROVED:
        return {"ready": False, "reason": "waiting_for_exactly9592_other_generations", "present": len(present), "missing": len(missing)}
    # The approved originals must still be released, with no replacement starts.
    for entry in plan["releases"]:
        attempt = entry["attempt_id"]
        run, slot = context["slots"][attempt]
        events = [json.loads(line) for line in (run / "transport.jsonl").read_text().splitlines()]
        events = [e for e in events if e.get("attempt_id") == attempt]
        if [e.get("event") for e in events] != ["request_started", "outcome_unknown", "authorized_unknown_release"]:
            raise ValueError("Approved original has another request or a changed transport prefix")
        if events[-1].get("reconciliation", {}).get("proposal_sha256") != entry["proposal_sha256"]:
            raise ValueError("Transport release is not bound to the approved proposal")
    if (WORK / "resume_controller.json").exists():
        return {"ready": False, "reason": "another_resume_control_is_already_pending"}
    if any(WORK.glob("resume_controller.applied-*.json")):
        raise ValueError("A controller resume has already been consumed")
    return {"ready": True, "parent": parent, "generation_count": len(present),
            "generation_hashes": hashes, "generation_snapshot_hash": digest(hashes)}


def publish_once(context, prepared, apply=False):
    intent_path = HERE / "one-use-intent.json"
    if intent_path.exists():
        return {"state": "one_use_authorization_already_consumed", "new_model_calls": 0}
    if not prepared.get("ready"):
        return {"state": "waiting", **prepared, "new_model_calls": 0}
    if not apply:
        return {"state": "ready_for_one_resume", "generation_count": prepared["generation_count"], "new_model_calls": 0}
    repeated = readiness(context)
    if not repeated.get("ready") or repeated["generation_snapshot_hash"] != prepared["generation_snapshot_hash"]:
        raise ValueError("Readiness changed before publication")
    for attempt, expected in repeated["generation_hashes"].items():
        run, slot = context["slots"][attempt]
        if file_hash(run / "generations" / (attempt + ".json")) != expected:
            raise ValueError("Generation bytes changed during final full rehash")
    parent = repeated["parent"]
    issued = time.time()
    if issued <= parent.get("failure_at", issued):
        raise ValueError("Resume timestamp is not after the parent's hold")
    control = {"ready": True, "experiment": "openai_math_100_provider_max_v2_20261014", "nonce": str(uuid.uuid4()),
               "issued_at": issued, "launch_sha256": context["watch_plan"]["launch_plan_sha256"]}
    intent = {"kind": "approved_eight_one_use_parent_resume_v1", "watch_plan_sha256": context["plan_sha256"],
        "control": control, "approved_attempt_ids": sorted(APPROVED), "generation_count": repeated["generation_count"],
        "generation_snapshot_hash": repeated["generation_snapshot_hash"], "original_parent_state": parent,
        "new_model_calls": 0, "tools_executed": 0, "automatic_replacement_retry": False}
    # Consume before publication: a crash cannot create a second automatic resume.
    exclusive_json(intent_path, intent)
    exclusive_json(HERE / "final-other-generations.json", repeated["generation_hashes"])
    try:
        exclusive_json(WORK / "resume_controller.json", control)
    except FileExistsError:
        result = {"state": "consumed_without_publication_existing_control", "new_model_calls": 0}
        exclusive_json(HERE / "publication-receipt.json", result)
        return result
    result = {"state": "published_once", "control_sha256": hashlib.sha256(
              json.dumps(control, sort_keys=True, allow_nan=False).encode()).hexdigest(), "control": control,
              "watch_plan_sha256": context["plan_sha256"], "new_model_calls": 0}
    exclusive_json(HERE / "publication-receipt.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan-sha256", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--watch", action="store_true")
    args = parser.parse_args()
    for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        os.environ.pop(name, None)
    with (HERE / "watcher.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if (HERE / "one-use-intent.json").exists():
            print(json.dumps({"state": "one_use_authorization_already_consumed"}));return
        context = load_context(HERE / "watch-plan.json", args.plan_sha256)
        while True:
            result = publish_once(context, readiness(context), args.apply)
            print(json.dumps(result, sort_keys=True), flush=True)
            if not args.watch or result["state"] != "waiting":
                return
            time.sleep(5)


if __name__ == "__main__":
    main()
