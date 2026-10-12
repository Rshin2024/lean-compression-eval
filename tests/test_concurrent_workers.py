"""Several workers sharing one evaluation or reference-check directory.

Lean is stubbed (evaluate_proof is patched, or a stand-in docker client runs instead), so no Lean,
Docker daemon or model is used. The multi-process tests start real worker processes on one directory.
"""
import contextlib
import fcntl
import io
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from lean_compression import cli
from lean_compression.io import claim, claim_locks, digest, file_hash, read_json, read_jsonl, write_json
from lean_compression.runner import evaluate_run
from lean_compression.store import create_run, record_generation

PROJECT = Path(__file__).resolve().parents[1]
ENGINE = PROJECT / "lean/Evaluator.lean"
CONFIG = PROJECT / "examples/demo-config.json"
LOCAL_ENV = PROJECT / "configs/environment.local.json"
MODEL = "pinned-model-2026-01-01"
TASKS = [{"task_id": f"tiny_{n}", "family_id": f"family_{n}", "domain": "sets_logic", "split": "pilot", "origin": "fresh",
          "stratum": "provisional_easy", "imports": ["Init"], "context": "", "premise_context": "",
          "forbidden_declarations": [], "target_name": f"tiny_target_{n}", "binders": "(P : Prop) (h : P)",
          "proposition": " ∧ ".join(["P"] * n)} for n in range(1, 7)]

# One worker process: the CLI with Lean stubbed. It logs each check; its first check waits until
# every worker has begun one, so all of them hold a claim at once. As with real records, each check
# differs (worker and time), so a slot checked twice could not be written twice.
WORKER = r"""
import os, sys, time
from pathlib import Path
from unittest.mock import patch
from lean_compression import cli, io

log, workers, stall = Path(sys.argv[1]), int(sys.argv[2]), sys.argv[3] == "stall"
def checked(task, proof, environment, engine, config):
    io.append_event(log, {"task_id": task["task_id"], "proof": proof})
    ready = log.with_name("ready")
    ready.mkdir(exist_ok=True)
    (ready / str(os.getpid())).touch()
    deadline = time.monotonic() + 60
    while len(list(ready.iterdir())) < workers and time.monotonic() < deadline:
        time.sleep(0.01)
    time.sleep(600 if stall else 0.02)
    return {"validity_status": "success", "metric_status": "available", "engine_hash": io.file_hash(engine),
            "validation": {"validity_status": "success"}, "worker": os.getpid(), "evaluated_at": io.now(),
            "metrics": {"metric_status": "available", "W_S": 1, "W_B": 3, "U_S_decimal": "1", "U_B_decimal": "7", "D": 2}}

with patch("lean_compression.runner.evaluate_proof", checked), patch("lean_compression.cli.load_environment", io.read_json), \
        patch("lean_compression.environment.verify_environment"):
    cli.main(sys.argv[4:])
"""

APPENDER = r"""
import sys, time
from pathlib import Path
from lean_compression.io import append_event

path, writer, count, size = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
while not path.with_name("go").exists():
    time.sleep(0.001)
for index in range(count):
    append_event(path, {"event": "checker_incident", "writer": writer, "index": index, "stdout": writer * size})
"""

# A worker that holds a claim until its release file appears.
HOLDER = r"""
import sys, time
from pathlib import Path
from lean_compression.io import claim

target, release = Path(sys.argv[1]), Path(sys.argv[2])
with claim(target) as held:
    assert held
    while not release.exists():
        time.sleep(0.01)
"""

# A worker whose check runs through the real runner in Docker mode, with a stand-in docker client first on PATH.
CHECKER = r"""
import os, signal, sys
from pathlib import Path
from lean_compression.io import claim
from lean_compression.runner import execute_engine

signal.signal(signal.SIGINT, signal.default_int_handler)
signal.signal(signal.SIGTERM, signal.SIG_DFL)
target, engine, bin = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
os.environ["PATH"] = bin + os.pathsep + os.environ["PATH"]
environment = {"lean_binary": "/opt/lean/bin/lean", "toolchain_root": "/opt/lean", "lean_search_paths": []}
with claim(target):
    execute_engine({}, environment, engine, {"isolation": "docker", "docker_image": "sha256:" + "0" * 64},
                   mode="validate", wall_seconds=600)
"""

# `docker run` marks that it started, then runs, like a container, until its finish file appears. Every call is logged.
FAKE_DOCKER = r"""#!/bin/sh
echo "$*" >> "$(dirname "$0")/calls"
[ "$1" = run ] || exit 0
touch "$(dirname "$0")/started"
while [ ! -e "$(dirname "$0")/finish" ]; do sleep 0.02; done
"""


# The engine's results for a valid proof whose separate extraction process then fails.
VALID = {"validity_status": "success", "metric_status": "not_requested", "kernel_replay": True,
         "candidate_declarations": ["tiny_target_1"], "diagnostics": {}}
OUTAGE = {"validity_status": "infrastructure_error", "metric_status": "unavailable",
          "error": "docker: Cannot connect to the Docker daemon"}

# Several workers starting the same amended version each log its amendment once the log is free.
ONCE = r"""
import sys, time
from pathlib import Path
from lean_compression.io import append_event

path, event = Path(sys.argv[1]), {"kind": "reference_recheck", "version": "v3", "anchors": sys.argv[2]}
while not path.with_name("go").exists():
    time.sleep(0.001)
append_event(path, event, once=True)
"""


def lean_success(*args):
    return {"validity_status": "success", "metric_status": "available", "engine_hash": file_hash(ENGINE),
            "validation": {"validity_status": "success"},
            "metrics": {"metric_status": "available", "W_S": 1, "W_B": 3, "U_S_decimal": "1", "U_B_decimal": "7", "D": 2}}


def child(*argv):
    return subprocess.Popen([sys.executable, "-c", *map(str, argv)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env={**os.environ, "PYTHONPATH": str(PROJECT / "src"), "PYTHONDONTWRITEBYTECODE": "1"})


def eventually(condition, seconds=60):
    """Poll `condition` until it holds; return False if `seconds` pass first."""
    deadline = time.monotonic() + seconds
    while not condition():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.01)
    return True


def claimable(path):
    """Whether a worker can claim `path` now; a claim it gets is released again."""
    with claim(path) as held:
        return held


class SharedDirectoryTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def workers(self, *argv, count=3):
        """Run `count` worker processes with the same arguments; return their summaries and logged checks."""
        log = self.root / f"workers-{time.time_ns()}" / "checks.jsonl"
        log.parent.mkdir()
        processes = [child(WORKER, log, count, "run", *argv) for _ in range(count)]
        outputs = [process.communicate(timeout=120) for process in processes]
        for process, (stdout, stderr) in zip(processes, outputs):
            self.assertEqual(process.returncode, 0, stderr.decode())
        return [json.loads(stdout) for stdout, stderr in outputs], [(row["task_id"], row["proof"]) for row in read_jsonl(log)]


class ClaimTests(SharedDirectoryTest):
    def setUp(self):
        super().setUp()
        self.target, self.marker = self.root / "record.json", self.root / "record.claim"

    def abandon(self):
        """Leave a claim that no process holds, as a killed worker does."""
        exited = subprocess.Popen([sys.executable, "-c", ""])
        exited.wait()
        self.marker.write_text(json.dumps({"pid": exited.pid, "host": socket.gethostname(),
                                           "started_at": "2026-10-12T00:00:00+00:00", "command": ["evaluate"]}))
        return exited.pid

    def test_claim_is_exclusive_names_its_worker_and_is_not_a_record(self):
        with claim(self.target) as held:
            owner = read_json(self.marker)
            # The claim's lock, which a checker inherits, is held through a read-only descriptor.
            lock, = claim_locks.get()
            self.assertEqual(fcntl.fcntl(lock, fcntl.F_GETFL) & os.O_ACCMODE, os.O_RDONLY)
            self.assertTrue(os.path.samestat(os.fstat(lock), self.marker.stat()))
            self.assertEqual(list(self.root.glob("*.json")), [])
            with claim(self.target) as again:
                self.assertEqual((held, again), (True, False))
            self.assertTrue(self.marker.exists())
        self.assertEqual(claim_locks.get(), ())
        self.assertEqual({key: owner[key] for key in ("pid", "host", "command")},
                         {"pid": os.getpid(), "host": socket.gethostname(), "command": sys.argv})
        self.assertIn("started_at", owner)
        # Released, and no temporary claim file is left behind.
        self.assertEqual(list(self.root.iterdir()), [])

    def test_a_claim_is_locked_before_it_is_published(self):
        real_link, rivals = os.link, []
        def link(source, destination):
            real_link(source, destination)
            if not rivals:
                rivals.append("published")
                rivals.append(claimable(self.target))  # Another worker finds the claim as soon as it appears.
        with patch("os.link", link), claim(self.target) as held:
            self.assertEqual((held, rivals[1]), (True, False))
        self.assertEqual(list(self.root.iterdir()), [])

    def test_claim_is_released_when_the_work_raises(self):
        with self.assertRaisesRegex(RuntimeError, "Lean crashed"), claim(self.target) as held:
            self.assertTrue(held)
            raise RuntimeError("Lean crashed")
        self.assertEqual(list(self.root.iterdir()), [])

    def test_an_interrupted_worker_leaves_its_claim_to_its_lock(self):
        with self.assertRaises(KeyboardInterrupt), claim(self.target) as held:
            self.assertTrue(held)
            raise KeyboardInterrupt
        # No checker inherited the lock here, so the next worker takes the claim over at once.
        self.assertTrue(self.marker.exists())
        self.assertTrue(claimable(self.target))
        self.assertEqual(list(self.root.iterdir()), [])

    def test_only_its_lock_keeps_a_claim_live_whatever_process_or_host_it_names(self):
        dead, host = self.abandon(), socket.gethostname()
        # The named process exited or its ID was reused (this one, pid 1), the host was renamed, or the claim is unreadable.
        for owner in ({"pid": dead, "host": host}, {"pid": os.getpid(), "host": host}, {"pid": 1, "host": host},
                      {"pid": dead, "host": "another-host"}, "{"):
            text = owner if isinstance(owner, str) else json.dumps(owner)
            for live in (True, False):
                self.marker.write_text(text)
                with self.subTest(owner=owner, live=live), open(self.marker, "rb") as worker:
                    if live:
                        fcntl.flock(worker.fileno(), fcntl.LOCK_EX)
                    with claim(self.target) as held:
                        self.assertEqual(held, not live)
                        self.assertEqual(read_json(self.marker)["pid"] if held else self.marker.read_text(),
                                         os.getpid() if held else text)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_the_claim_of_a_killed_worker_not_yet_reaped_is_taken_over(self):
        holder = child(HOLDER, self.target, self.root / "never")
        self.assertTrue(eventually(self.marker.exists))
        holder.kill()
        self.assertTrue(eventually(lambda: claimable(self.target), seconds=10))
        os.kill(holder.pid, 0)  # Still there: a zombie, which has dropped its lock.
        holder.communicate()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_a_taker_never_replaces_a_fresh_claim_made_after_a_live_holder_released(self):
        release, fresh, made = self.root / "release", contextlib.ExitStack(), []
        self.addCleanup(fresh.close)
        holder = child(HOLDER, self.target, release)
        self.assertTrue(eventually(self.marker.exists))
        def release_and_claim_afresh():
            # The holder's check fails, so it releases its claim and exits; then a third worker claims the task.
            if not made:
                made.append(None)
                release.touch()
                holder.communicate(timeout=60)
                made[0] = fresh.enter_context(claim(self.target))
        real_samestat = os.path.samestat
        def samestat(first, second):
            same = real_samestat(first, second)
            if same:
                release_and_claim_afresh()  # This taker stalls right after confirming the claim it read.
            return same
        with patch("os.path.samestat", samestat), claim(self.target) as held:
            release_and_claim_afresh()  # Or now, if this taker refused the live claim without such a check.
            self.assertEqual((held, made, read_json(self.marker)["pid"]), (False, [True], os.getpid()))
        self.assertTrue(self.marker.exists())
        fresh.close()
        self.assertEqual(sorted(self.root.iterdir()), [release])

    def test_a_taker_whose_claim_was_replaced_before_it_locked_it_refuses(self):
        for replaced_by in ("another taker", "a fresh claim after its holder released it"):
            with self.subTest(replaced_by=replaced_by), contextlib.ExitStack() as holder, contextlib.ExitStack() as others:
                if replaced_by == "another taker":
                    self.abandon()
                else:
                    self.assertTrue(holder.enter_context(claim(self.target)))
                real_flock, taken = fcntl.flock, []
                def flock(fd, operation):
                    if operation & fcntl.LOCK_NB and not taken:
                        # This taker has opened the claim; it is replaced before this one locks it.
                        taken.append("opened")
                        holder.close()
                        taken.append(others.enter_context(claim(self.target)))
                        taken.append(self.marker.stat())
                    return real_flock(fd, operation)
                with patch("fcntl.flock", flock), claim(self.target) as held:
                    self.assertEqual((taken[1], held), (True, False))
                self.assertTrue(os.path.samestat(self.marker.stat(), taken[2]))
            self.assertEqual(list(self.root.iterdir()), [])

    def test_racing_takers_of_an_abandoned_claim_cannot_both_take_it(self):
        self.abandon()
        real_replace, held = os.replace, {}
        taking, refused = threading.Event(), threading.Event()
        def replace(source, destination):
            if threading.current_thread().name == "first":
                taking.set()
                refused.wait(5)  # The first taker holds the abandoned claim's lock while the second tries.
            return real_replace(source, destination)
        def take(name):
            with claim(self.target) as mine:
                held[name] = mine
                if name == "second":
                    refused.set()
        first = threading.Thread(target=take, args=("first",), name="first", daemon=True)
        second = threading.Thread(target=take, args=("second",), name="second", daemon=True)
        with patch("os.replace", replace):
            first.start()
            self.assertTrue(taking.wait(5))
            second.start()
            first.join(10)
            second.join(10)
        self.assertEqual(held, {"first": True, "second": False})
        self.assertEqual(list(self.root.iterdir()), [])

    def test_release_never_removes_another_workers_claim(self):
        with contextlib.ExitStack() as other:
            with claim(self.target) as held:
                # Deleted by hand while this worker still runs, then claimed by another worker.
                self.marker.unlink()
                self.assertEqual((held, other.enter_context(claim(self.target))), (True, True))
                replacement = self.marker.stat()
            self.assertTrue(os.path.samestat(self.marker.stat(), replacement))
        self.assertEqual(list(self.root.iterdir()), [])

    def checking_worker(self, kill):
        """A worker whose check runs in a stand-in docker client, stopped by `kill` while that client runs."""
        case = self.root / kill.name
        (case / "bin").mkdir(parents=True)
        (case / "bin/docker").write_text(FAKE_DOCKER)
        (case / "bin/docker").chmod(0o755)
        self.addCleanup((case / "bin/finish").touch)
        worker = child(CHECKER, case / "record.json", ENGINE, case / "bin")
        self.assertTrue(eventually((case / "bin/started").exists))
        os.kill(worker.pid, kill)
        worker.communicate(timeout=60)
        return case, worker.pid

    def test_a_killed_workers_check_keeps_its_claim_live_until_it_exits(self):
        for kill in (signal.SIGKILL, signal.SIGTERM):
            with self.subTest(signal=kill.name):
                case, _ = self.checking_worker(kill)
                # The worker is gone, but the docker client it started runs on and holds the claim.
                self.assertFalse(claimable(case / "record.json"))
                self.assertTrue((case / "record.claim").exists())
                (case / "bin/finish").touch()
                self.assertTrue(eventually(lambda: claimable(case / "record.json")))
                self.assertFalse((case / "record.claim").exists())

    def test_an_interrupted_worker_stops_its_check_and_leaves_its_claim_to_the_lock(self):
        case, pid = self.checking_worker(signal.SIGINT)
        # Ctrl-C reaches only the worker: it kills its client and removes the container before it exits.
        run, removal = (case / "bin/calls").read_text().splitlines()
        name = run.split()[3]
        self.assertTrue(run.startswith(f"run --rm --name lean-compression-{pid}-"))
        self.assertEqual(removal, f"rm -f {name}")
        # The claim stays for its lock to decide, and no check holds that lock any more.
        self.assertTrue((case / "record.claim").exists())
        self.assertTrue(claimable(case / "record.json"))
        self.assertFalse((case / "record.claim").exists())


class EvaluateWorkerTests(SharedDirectoryTest):
    def setUp(self):
        super().setUp()
        config = read_json(PROJECT / "configs/pilot.json")
        image = "sha256:" + "0" * 64
        config["validation"].update(isolation="docker", docker_image=image)
        config["generation"].update(snapshot_pinned=True, settings_resolved=True)
        env = {"environment_hash": "fixture", "lean_toolchain": config["environment"]["lean_toolchain"],
               "mathlib_commit": config["environment"]["mathlib_commit"], "hash_mode": "development_metadata_only",
               "runtime_backend": "docker", "docker_image": image,
               "source_exclusion_audit": {"lean_sources": 0, "git_directories": 0}}
        task = {"task_id": "one", "family_id": "family", "domain": "sets_logic", "split": "pilot", "origin": "fresh",
                "target_name": "testTarget", "binders": "(P : Prop) (h : P)", "proposition": "P", "imports": ["Init"],
                "premise_context": "", "context": ""}
        self.run = self.root / "run"
        self.slots = create_run(self.run, [task], config, env, split="pilot", model=MODEL, development=True)["slots"]
        for slot in self.slots:
            record_generation(self.run, slot, self.proof(slot), generation_status="completed")
        self.directory = self.run / "evaluations/v1"

    def proof(self, slot):
        return f"by exact h -- sample {slot['schedule_index']}"

    def evaluate(self, *outcomes, limit=None):
        with patch("lean_compression.runner.evaluate_proof", side_effect=list(outcomes) or lean_success) as checker:
            result = evaluate_run(self.run, ENGINE, limit=limit)
        return result, [call.args[1] for call in checker.call_args_list]

    def test_slots_claimed_by_another_worker_are_skipped_and_limit_counts_own_work(self):
        with contextlib.ExitStack() as other:
            for slot in self.slots[:2]:
                self.assertTrue(other.enter_context(claim(self.directory / f"{slot['attempt_id']}.json")))
            result, checked = self.evaluate(limit=3)
            self.assertEqual((result["new_evaluations"], checked), (3, [self.proof(slot) for slot in self.slots[2:5]]))
        result, checked = self.evaluate()
        self.assertEqual(checked, [self.proof(slot) for slot in self.slots[:2] + self.slots[5:]])
        self.assertEqual(list(self.directory.glob("*.claim")), [])

    def test_slot_recorded_by_another_worker_after_the_first_check_is_skipped(self):
        late, other = self.directory / f"{self.slots[0]['attempt_id']}.json", {"recorded_by": "another worker"}
        @contextlib.contextmanager
        def racing(path):
            if path == late:
                write_json(path, other)
            with claim(path) as held:
                yield held
        with patch("lean_compression.runner.claim", racing):
            result, checked = self.evaluate()
        self.assertEqual((result["new_evaluations"], read_json(late)), (len(self.slots) - 1, other))
        self.assertEqual(checked, [self.proof(slot) for slot in self.slots[1:]])

    def test_failed_checks_release_their_claims_and_leave_slots_pending(self):
        with self.assertRaisesRegex(RuntimeError, "Lean crashed"):
            self.evaluate(RuntimeError("Lean crashed"))
        incident = {"validity_status": "infrastructure_error", "metric_status": "unavailable", "engine_hash": file_hash(ENGINE)}
        result, checked = self.evaluate(incident, limit=1)
        self.assertEqual((result["new_evaluations"], result["checker_incidents"]), (0, 1))
        self.assertEqual(read_jsonl(self.directory / "incidents.jsonl")[0]["attempt_id"], self.slots[0]["attempt_id"])
        self.assertEqual(list(self.directory.glob("*.claim")), [])
        result, checked = self.evaluate()
        self.assertEqual(checked, [self.proof(slot) for slot in self.slots])

    def test_extraction_incidents_release_their_claims_and_leave_slots_pending(self):
        # A valid proof whose extraction process fails: evaluate_proof reports a checker incident.
        with patch("lean_compression.runner.execute_engine", side_effect=[VALID, OUTAGE]) as engine:
            result = evaluate_run(self.run, ENGINE, limit=1)
        self.assertEqual([call.kwargs["mode"] for call in engine.call_args_list], ["validate", "extract"])
        self.assertEqual((result["new_evaluations"], result["checker_incidents"]), (0, 1))
        incident, = read_jsonl(self.directory / "incidents.jsonl")
        self.assertEqual((incident["attempt_id"], incident["validity_status"], incident["failure_phase"]),
                         (self.slots[0]["attempt_id"], "infrastructure_error", "extraction"))
        self.assertEqual(list(self.directory.glob("*.claim")), [])
        result, checked = self.evaluate()
        self.assertEqual(checked, [self.proof(slot) for slot in self.slots])

    def test_version_policy_published_by_a_concurrent_worker_is_not_taken_for_records(self):
        self.evaluate(limit=0)
        policy, real_exists = self.directory / "policy.json", Path.exists
        # This worker checked for the policy just before another worker published it.
        with patch.object(Path, "exists", lambda path: path != policy and real_exists(path)):
            result, checked = self.evaluate()
        self.assertEqual(result["new_evaluations"], len(self.slots))

    def test_a_claim_left_beside_a_written_record_is_removed_unless_a_process_holds_it(self):
        self.evaluate()
        abandoned, live = (self.directory / f"{slot['attempt_id']}.claim" for slot in self.slots[:2])
        abandoned.write_text("{}")  # Its worker was killed after writing the record.
        live.write_text("{}")
        with open(live, "rb") as worker:  # Its worker has just written the record.
            fcntl.flock(worker.fileno(), fcntl.LOCK_EX)
            result, checked = self.evaluate()
        self.assertEqual((result["new_evaluations"], checked), (0, []))
        self.assertEqual(list(self.directory.glob("*.claim")), [live])

    def test_three_worker_processes_check_each_slot_once(self):
        summaries, checked = self.workers("evaluate", "--run", self.run, "--engine", ENGINE)
        self.assertEqual(sorted(checked), sorted(("one", self.proof(slot)) for slot in self.slots))
        self.assertEqual(sum(summary["new_evaluations"] for summary in summaries), len(self.slots))
        self.assertTrue(all(summary["new_evaluations"] for summary in summaries))
        self.assertEqual(len(list(self.directory.glob("*.json"))), len(self.slots) + 1)
        self.assertEqual(sorted(event["attempt_id"] for event in read_jsonl(self.run / "events.jsonl")),
                         sorted(slot["attempt_id"] for slot in self.slots))
        self.assertEqual(list(self.directory.glob("*.claim")), [])


class ReferenceWorkerTests(SharedDirectoryTest):
    def setUp(self):
        super().setUp()
        (self.root / "tasks.jsonl").write_text("".join(json.dumps(task) + "\n" for task in TASKS))
        (self.root / "refs.jsonl").write_text("".join(json.dumps({"task_id": task["task_id"], "proof": "by simp [h]"}) + "\n"
                                                      for task in TASKS))

    def argv(self, command, output):
        references = ["--references", self.root / "refs.jsonl"] if command == "verify-references" else []
        return [command, "--tasks", self.root / "tasks.jsonl", *references, "--environment", LOCAL_ENV,
                "--config", CONFIG, "--engine", ENGINE, "--output", output]

    def check(self, command, *outcomes, limit=None):
        stdout, stderr = io.StringIO(), io.StringIO()
        argv = [str(arg) for arg in self.argv(command, self.root / command)] + ([] if limit is None else ["--limit", str(limit)])
        with patch("lean_compression.runner.evaluate_proof", side_effect=list(outcomes) or lean_success) as checker, \
                patch("lean_compression.cli.load_environment", read_json), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                cli.main(argv)
            except SystemExit:
                self.fail(stderr.getvalue())
        return json.loads(stdout.getvalue()), [call.args[0]["task_id"] for call in checker.call_args_list]

    def test_tasks_claimed_by_another_worker_are_skipped_and_limit_counts_own_work(self):
        incident = {"validity_status": "infrastructure_error", "metric_status": "unavailable", "engine_hash": file_hash(ENGINE)}
        ids = [task["task_id"] for task in TASKS]
        for command in ("verify-references", "baseline"):
            output = self.root / command
            with self.subTest(command=command):
                with contextlib.ExitStack() as other:
                    for task_id in ids[:2]:
                        self.assertTrue(other.enter_context(claim(output / f"{digest(task_id)}.json")))
                    result, checked = self.check(command, incident, lean_success(), lean_success(), limit=3)
                    self.assertEqual((result["new_checks"], result["checker_incidents"], checked), (2, 1, ids[2:5]))
                # The incident's task stays pending for the next run.
                result, checked = self.check(command)
                self.assertEqual((result["new_checks"], checked), (4, ids[:3] + ids[5:]))
                self.assertEqual(list(output.glob("*.claim")), [])

    def test_extraction_incidents_release_their_claims_and_leave_tasks_pending(self):
        ids = [task["task_id"] for task in TASKS]
        for command in ("verify-references", "baseline"):
            output, stdout, stderr = self.root / command, io.StringIO(), io.StringIO()
            with self.subTest(command=command):
                with patch("lean_compression.runner.execute_engine", side_effect=[VALID, OUTAGE]), \
                        patch("lean_compression.cli.load_environment", read_json), \
                        contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    cli.main([str(arg) for arg in self.argv(command, output)] + ["--limit", "1"])
                result = json.loads(stdout.getvalue())
                self.assertEqual((result["new_checks"], result["checker_incidents"]), (0, 1))
                self.assertIn(f"{ids[0]}: infrastructure_error during extraction (checker incident; task left pending)",
                              stderr.getvalue())
                incident, = read_jsonl(output / "incidents.jsonl")
                self.assertEqual((incident["task_id"], incident["failure_phase"]), (ids[0], "extraction"))
                self.assertEqual(list(output.glob("*.claim")), [])
                result, checked = self.check(command)
                self.assertEqual((result["new_checks"], checked), (len(TASKS), ids))

    def test_task_recorded_by_another_worker_after_the_first_check_is_skipped(self):
        for command in ("verify-references", "baseline"):
            late = self.root / command / f"{digest(TASKS[0]['task_id'])}.json"
            @contextlib.contextmanager
            def racing(path):
                if path == late:
                    write_json(path, {"validity_status": "success", "metric_status": "unavailable",
                                      "recorded_by": "another worker"})
                with claim(path) as held:
                    yield held
            with self.subTest(command=command), patch("lean_compression.io.claim", racing):
                result, checked = self.check(command)
                self.assertEqual((result["new_checks"], checked), (len(TASKS) - 1, [task["task_id"] for task in TASKS[1:]]))

    def test_a_claim_left_beside_a_written_record_is_removed_unless_a_process_holds_it(self):
        for command in ("verify-references", "baseline"):
            output = self.root / command
            with self.subTest(command=command):
                self.check(command)
                abandoned, live = (output / f"{digest(task['task_id'])}.claim" for task in TASKS[:2])
                abandoned.write_text("{}")  # Its worker was killed after writing the record.
                live.write_text("{}")
                with open(live, "rb") as worker:  # Its worker has just written the record.
                    fcntl.flock(worker.fileno(), fcntl.LOCK_EX)
                    result, checked = self.check(command)
                self.assertEqual((result["new_checks"], checked), (0, []))
                self.assertEqual(list(output.glob("*.claim")), [live])

    def test_three_worker_processes_check_each_task_once(self):
        for command in ("verify-references", "baseline"):
            output = self.root / command
            with self.subTest(command=command):
                summaries, checked = self.workers(*self.argv(command, output))
                self.assertEqual(sorted(task_id for task_id, proof in checked), [task["task_id"] for task in TASKS])
                self.assertEqual(sum(summary["new_checks"] for summary in summaries), len(TASKS))
                self.assertTrue(all(summary["new_checks"] for summary in summaries))
                self.assertEqual(len(list(output.glob("*.json"))), len(TASKS) + 1)
                self.assertEqual(list(output.glob("*.claim")), [])

    def test_rerunning_the_command_after_a_killed_worker_takes_over_its_claim(self):
        output, log = self.root / "checks", self.root / "killed/checks.jsonl"
        log.parent.mkdir()
        stalled = child(WORKER, log, 1, "stall", *self.argv("verify-references", output))
        deadline = time.monotonic() + 60
        while not (log.exists() and log.read_text().endswith("\n")) and time.monotonic() < deadline:
            time.sleep(0.01)
        stalled.kill()
        stalled.communicate()
        killed, = [row["task_id"] for row in read_jsonl(log)]
        claimed, = output.glob("*.claim")
        self.assertEqual((claimed.stem, read_json(claimed)["pid"]), (digest(killed), stalled.pid))
        summaries, checked = self.workers(*self.argv("verify-references", output))
        self.assertEqual(sorted(task_id for task_id, proof in checked), [task["task_id"] for task in TASKS])
        self.assertEqual(len(list(output.glob("*.json"))), len(TASKS) + 1)
        self.assertEqual(list(output.glob("*.claim")), [])


class EventLogTests(SharedDirectoryTest):
    def test_concurrent_appends_keep_every_line_intact(self):
        path, writers, count, size = self.root / "incidents.jsonl", "abcd", 20, 256 * 1024
        processes = [child(APPENDER, path, writer, count, size) for writer in writers]
        path.with_name("go").touch()
        for process in processes:
            stdout, stderr = process.communicate(timeout=120)
            self.assertEqual(process.returncode, 0, stderr.decode())
        rows = read_jsonl(path)
        self.assertEqual(sorted((row["writer"], row["index"]) for row in rows),
                         sorted((writer, index) for writer in writers for index in range(count)))
        self.assertTrue(all(row["stdout"] == row["writer"] * size for row in rows))

    def test_an_event_appended_once_by_concurrent_workers_is_logged_once(self):
        path = self.root / "amendments.jsonl"
        # An unreadable line is not a match, and a different event is not one either.
        path.write_text("{not json\n")
        processes = [child(ONCE, path, anchors) for anchors in ("same", "same", "same", "other")]
        path.with_name("go").touch()
        for process in processes:
            stdout, stderr = process.communicate(timeout=120)
            self.assertEqual(process.returncode, 0, stderr.decode())
        lines = path.read_text().splitlines()
        self.assertEqual(lines[0], "{not json")
        self.assertEqual(sorted(json.loads(line)["anchors"] for line in lines[1:]), ["other", "same"])


if __name__ == "__main__":
    unittest.main()
