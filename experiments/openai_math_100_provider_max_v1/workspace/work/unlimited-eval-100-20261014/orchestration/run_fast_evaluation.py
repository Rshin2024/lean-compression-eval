"""Keep inherited credentials alive while the separate generation child runs.

No automatic restart occurs after a noncomplete child exit. A hash-bound explicit
resume receipt is required. This parent never writes or prints key values.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

SESSION = Path(__file__).resolve().parents[1]
CHILD = Path(__file__).with_name("generate_parallel.py")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(value):
    path = SESSION / "generation-parent-status.json"
    temp = path.with_suffix(".tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump({"parent_pid": os.getpid(), "updated_at": time.time(), **value}, stream, indent=2)
    temp.replace(path)


def validate_sources(plan):
    pins = plan.get("operational_source_hashes", {})
    if "orchestration/generate_parallel.py" not in pins:
        raise ValueError("Missing generation-child source pin")
    for relative, expected in pins.items():
        path = (SESSION / relative).resolve()
        if not path.is_relative_to(SESSION) or not path.is_file() or sha(path) != expected:
            raise ValueError("Operational source differs from launch pin: " + relative)


def valid_resume(control, plan_path, failure_at):
    return (control.get("ready") is True and isinstance(control.get("nonce"), str)
        and len(control["nonce"]) >= 16 and control.get("issued_at", 0) > failure_at
        and control.get("launch_sha256") == sha(plan_path)
        and control.get("experiment") == json.loads(plan_path.read_text()).get("experiment"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path, default=SESSION / "launch_ready.json")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    if args.validate_only:
        # Offline/source/runtime validation does not need or start this parent.
        raise SystemExit(subprocess.run([sys.executable, str(CHILD), "--plan", str(args.plan), "--validate-only"]).returncode)
    while True:
        started = time.time()
        try:
            plan = json.loads(args.plan.read_text())
            validate_sources(plan)
            child = subprocess.Popen([sys.executable, str(CHILD), "--plan", str(args.plan)], stdin=subprocess.DEVNULL)
            save({"state": "child_running", "child_pid": child.pid, "child_started_at": started, "launch_sha256": sha(args.plan)})
            exit_code = child.wait()
            path = SESSION / "generation-status.json"
            status = json.loads(path.read_text()) if path.exists() else {}
            if exit_code == 0 and status.get("state") == "complete" and status.get("generation_total") == 9600:
                save({"state": "generation_complete", "child_pid": child.pid, "child_exit_code": exit_code})
                return
            failure = {"state": "holding_credentials_for_explicit_resume", "child_pid": child.pid, "child_exit_code": exit_code}
        except Exception as error:
            # Error values are restricted to our own file/launch operations.
            # Avoid printing arbitrary exception contents in the credential parent.
            failure = {"state": "holding_credentials_for_explicit_resume", "error_type": type(error).__name__}
        failure_at = time.time()
        save({**failure, "failure_at": failure_at})
        control_path = SESSION / "resume_controller.json"
        while True:
            if control_path.exists():
                try:
                    control = json.loads(control_path.read_text())
                    if valid_resume(control, args.plan, failure_at):
                        validate_sources(json.loads(args.plan.read_text()))
                        control_path.rename(SESSION / f"resume_controller.applied-{time.time_ns()}.json")
                        break
                except (ValueError, OSError, TypeError):
                    pass
            time.sleep(1)


if __name__ == "__main__":
    main()
