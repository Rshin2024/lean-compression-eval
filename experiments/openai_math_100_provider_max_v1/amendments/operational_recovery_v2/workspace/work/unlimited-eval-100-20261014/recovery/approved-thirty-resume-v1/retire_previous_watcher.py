"""Retire only the superseded credential-free eight-attempt watcher after review.

Never signals the credential parent, generation child or any provider worker.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import signal
import time
import watch_resume as watcher

HERE, WORK = watcher.HERE, watcher.WORK
EXPECTED_PID = 68390


def execute(plan_sha256, apply=False):
    plan = watcher.bound(HERE / "watch-plan.json", plan_sha256)
    if plan["retirement_helper_sha256"] != watcher.file_hash(Path(__file__)):
        raise ValueError("Retirement helper changed")
    previous = plan["superseded_watcher"]
    if previous["pid"] != EXPECTED_PID or "approved-eight-resume-v1/watch_resume.py" not in previous["identity"]:
        raise ValueError("Only the known credential-free watcher can be retired")
    activation = watcher.bound(WORK / previous["activation_path"], previous["activation_sha256"])
    watcher.bound(WORK / previous["plan_path"], previous["plan_sha256"])
    if activation.get("pid") != EXPECTED_PID or watcher.file_hash(WORK / previous["source_path"]) != previous["source_sha256"]:
        raise ValueError("Superseded watcher source/activation differs")
    old = WORK / "recovery/approved-eight-resume-v1"
    if (old / "one-use-intent.json").exists() or (WORK / "resume_controller.json").exists() or any(WORK.glob("resume_controller.applied-*.json")):
        raise ValueError("A resume was already prepared or published; retire nothing")
    receipt_path = HERE / "retirement-receipt.json"
    if receipt_path.exists():
        receipt = watcher.read_json(receipt_path)
        if receipt.get("replacement_watch_plan_sha256") != plan_sha256:
            raise ValueError("Another retirement receipt exists")
        return {"state": "already_retired", "signal_sent": False}
    actual = watcher.process_identity(EXPECTED_PID)
    if actual is not None and actual != previous["identity"]:
        raise ValueError("The PID now belongs to another process; signal nothing")
    if not apply:
        return {"state": "reviewed_no_signals", "old_watcher_alive": actual is not None, "provider_signals_sent": 0}
    sent = False
    if actual is not None:
        # Recheck immediately before signalling this one non-provider process.
        if watcher.process_identity(EXPECTED_PID) != previous["identity"]:
            raise ValueError("Watcher identity changed before retirement")
        os.kill(EXPECTED_PID, signal.SIGTERM)
        sent = True
        for _ in range(50):
            if watcher.process_identity(EXPECTED_PID) != previous["identity"]:
                break
            time.sleep(.1)
        else:
            raise ValueError("Watcher retirement was not confirmed")
    raced = {"old_intent_exists": (old / "one-use-intent.json").exists(),
             "pending_control_exists": (WORK / "resume_controller.json").exists(),
             "applied_control_exists": any(WORK.glob("resume_controller.applied-*.json"))}
    if any(raced.values()):
        watcher.exclusive_json(HERE / "retirement-race.json", {"state": "resume_preparation_or_publication_raced_retirement",
            "replacement_watch_plan_sha256": plan_sha256, "old_watcher_pid": EXPECTED_PID,
            "signal_sent": sent, "provider_signals_sent": 0, **raced})
        raise ValueError("A previous resume raced retirement; exact30 activation is blocked")
    receipt = {"state": "retired", "old_watcher_pid": EXPECTED_PID,
        "replacement_watch_plan_sha256": plan_sha256, "old_activation_sha256": previous["activation_sha256"],
        "retirement_helper_sha256": plan["retirement_helper_sha256"], "old_resume_published": False,
        "signal_sent": sent, "signal": "SIGTERM" if sent else None, "provider_signals_sent": 0,
        "new_model_calls": 0, "retired_at": time.time()}
    watcher.exclusive_json(receipt_path, receipt)
    return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan-sha256", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY"):os.environ.pop(key, None)
    with (HERE / "retirement.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        print(json.dumps(execute(args.plan_sha256, args.apply), indent=2))
