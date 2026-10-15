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
import subprocess

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

APPROVED |= {'ab30b73be0ef9e2c2608a31dcf36951da28f78f9d724f2b7241def49a31c7657', '99c441c0b8105d452922e93075079a9acc480e9111a92a92e2b32ffa1e92cdba', 'bad8dda80fa7794933725f2d65a8a66f0b6c99b533c2584cedca229272863d3e', '7db8bf980fdfb4fa928bf146fa57e2dc98684d4ca59f68d0ec882c6f1f8b99c7', 'de7e827971497f0ed4043c8f56202e2bd36f65323fe2e897567154fae877a8cb', 'b4138a57391e7c85a841764f56f09c17fc9e5a03aa1ecfb9873b7affb2124378', '21f2cd2c3d1be0d84d0913bf1da10f4e1a8ec46966b4bb4385586ed32142085c', '99f069c0e034ba9e432e3229b26b7b5a1ba2dee6da1054c80bd207c68eae5a30', '23120aa50142c5b0c11f1608b39cd73108e960fae86ff8a86c6699fa0120adb0', 'fc38f54ca242035bb42206cfce032ee0fd4d53a6a09028affd4ec5dcd8587644', '2e8c19a46b60a9cbe0bb4a4499504319a4fe2c80cfc6190b2c3f3ea99bf719d9', '2169cd729a3d79f275870deddb76018001ae7f8b985abeeb02c812dfd6c37fda', '4bc3a52d0f90c478eab18cd17e688202157051a7257940d8e685c37bd2b259fb', 'a3a356a147ff38718133d2157d004305de082bd928a6ddd02f33635793425462', 'b1b3b08a3df5102946f29a0e2843223923f22c24a8e596532eb11312e3d30089', '2d2f4d82623d8cfff2f5c871669f255a18a330a453328b92c5a56e8f96c3a7c4', '9efbbef86d42aed807031ada7caac9615cc8dc9dcef41b50c5fc7bafe38551b9', 'd31dd77f98bd93c942265a0e856a35d58e434ff3f234d197c48f6a1bf1a14c1e', 'd89075ddac40da0dd307a4de6fb7d67f79dfe3db62dfc155c1ac626bef544658'}

APPROVED |= {'aa64ce7315d722221998d6e5a6a0919bef13d55c55b928267cfda3cf1f4b00ba', '1fc617b548c7bb7a52b8359dc069094f5b9e25b8fe14b6f14e69717879969865', 'a1d751dea6a3d74889d8231c2b9fd5349ed7ed67b08713d452113426195aad03'}

def stamp(path):
    s = path.stat()
    return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)


def process_identity(pid):
    if type(pid) is not int or pid <= 0:
        raise ValueError("Invalid process identifier")
    result = subprocess.run(["ps", "-p", str(pid), "-o", "pid=,ppid=,lstart=,command="], capture_output=True, text=True)
    if result.returncode == 0 and result.stdout.strip() and not result.stderr.strip():
        return result.stdout.strip()
    if result.returncode == 1 and not result.stdout.strip() and not result.stderr.strip():
        return None
    raise ValueError("Process inspection failed; absence is not confirmed")


def retirement_ok(context):
    plan = context["watch_plan"]
    path = HERE / "retirement-receipt.json"
    if not path.exists():
        return False
    receipt = read_json(path)
    previous = plan["superseded_watcher"]
    if (receipt.get("state") != "retired" or receipt.get("old_watcher_pid") != previous["pid"]
            or receipt.get("replacement_watch_plan_sha256") != context["plan_sha256"]
            or receipt.get("old_activation_sha256") != previous["activation_sha256"]
            or receipt.get("retirement_helper_sha256") != plan["retirement_helper_sha256"]
            or receipt.get("old_resume_published") is not False):
        raise ValueError("Superseded watcher retirement is not bound to this operation")
    actual = file_hash(path)
    if context.get("retirement_receipt_sha256", actual) != actual:
        raise ValueError("Watcher retirement receipt changed")
    context["retirement_receipt_sha256"] = actual
    return process_identity(previous["pid"]) != previous["identity"]


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
        prefix_hash = receipt.get("transport_prefix_hash")
        if prefix_hash is None:
            evidence_path = proposal_path.parent / proposal["evidence_file"]
            evidence = bound(evidence_path, proposal["evidence_sha256"])
            prefix_hash = evidence["transport_prefix_hash"]
        if entry["transport_prefix_hash"] != prefix_hash:
            raise ValueError("Original transport prefix is not bound by its release evidence")
    return launch


def load_context(plan_path, plan_sha256):
    plan = bound(plan_path, plan_sha256)
    if len(plan.get("releases", [])) != 30 or {x["attempt_id"] for x in plan["releases"]} != APPROVED:
        raise ValueError("Resume scope must be exactly the thirty approved attempts")
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
        runs[run] = (stamp(run / "manifest.json"), manifest, entry["manifest_sha256"])
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
    for run, (original, manifest, expected_sha) in list(context["runs"].items()):
        if stamp(run / "manifest.json") != original:
            if file_hash(run / "manifest.json") != expected_sha:
                raise ValueError("Frozen manifest bytes changed")
            context["runs"][run] = (stamp(run / "manifest.json"), manifest, expected_sha)
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
            record = verified_generation(read_json(path), slot)
            if record.get("generation_settings") != context["runs"][run][1]["config"]["generation"]:
                raise ValueError("A generation differs from the frozen generation settings")
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
    if process_identity(parent["parent_pid"]) != plan["credential_parent_identity"]:
        raise ValueError("Credential parent process identity changed")
    if type(parent.get("child_exit_code")) is not int:
        return {"ready": False, "reason": "parent_has_not_reaped_original_child"}
    if parent["child_exit_code"] < 0:
        raise ValueError("Original child ended by signal; this natural-exit plan does not authorize a resume")
    if parent["child_exit_code"] != plan["expected_original_child_exit_code"]:
        raise ValueError("Original child exit differs from the reviewed natural-exit incident")
    if process_identity(plan["original_generation_child_pid"]) == plan["original_generation_child_identity"]:
        return {"ready": False, "reason": "original_generation_child_is_still_alive"}
    if not retirement_ok(context):
        return {"ready": False, "reason": "superseded_eight_watcher_not_retired"}
    # Status counters may be stale after the known metadata-only cache failure.
    # Parent.wait() acknowledgement, actual process absence and no markers are
    # authoritative here; the stale generation-status.json is never rewritten.
    if any(list((run / "inflight").glob("*.json")) for run in context["runs"]):
        return {"ready": False, "reason": "an_inflight_marker_remains"}
    present, hashes = collected_generations(context)
    missing = set(context["slots"]) - present
    if missing != APPROVED:
        return {"ready": False, "reason": "waiting_for_exactly9570_other_generations", "present": len(present), "missing": len(missing)}
    # The approved originals must still be released, with no replacement starts.
    for entry in plan["releases"]:
        attempt = entry["attempt_id"]
        run, slot = context["slots"][attempt]
        events = [json.loads(line) for line in (run / "transport.jsonl").read_text().splitlines()]
        events = [e for e in events if e.get("attempt_id") == attempt]
        if not events or events[-1].get("event") != "authorized_unknown_release" or digest(events[:-1]) != entry["transport_prefix_hash"]:
            raise ValueError("Approved original has another request or a changed transport prefix")
        if events[-1].get("reconciliation", {}).get("proposal_sha256") != entry["proposal_sha256"]:
            raise ValueError("Transport release is not bound to the approved proposal")
    if (WORK / "resume_controller.json").exists():
        return {"ready": False, "reason": "another_resume_control_is_already_pending"}
    if any(WORK.glob("resume_controller.applied-*.json")):
        raise ValueError("A controller resume has already been consumed")
    if (WORK / "recovery/approved-eight-resume-v1/one-use-intent.json").exists():
        raise ValueError("The superseded watcher consumed a resume")
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
    intent = {"kind": "approved_thirty_one_use_parent_resume_v1", "watch_plan_sha256": context["plan_sha256"],
        "control": control, "approved_attempt_ids": sorted(APPROVED), "generation_count": repeated["generation_count"],
        "generation_snapshot_hash": repeated["generation_snapshot_hash"], "original_parent_state": parent,
        "superseded_watcher_retirement_sha256": context["retirement_receipt_sha256"],
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
