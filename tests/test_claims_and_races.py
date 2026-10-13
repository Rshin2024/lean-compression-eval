"""Claims, events and races of workers sharing a directory.

Claims and policies record the claim protocol and the harness code, so workers of other code are refused and a
claim that may be live without its lock is never taken over. A resumed evaluation finishes the work of a worker
killed after publishing a record. A container whose removal is unconfirmed keeps its task or slot claimed until
an operator clears it. A worker refused the version it raced to bind logs no amendment.

Lean and the Docker client are stand-ins; no Lean, Docker daemon, model or network is used.
"""
import contextlib
import io
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))  # sibling fixtures, also when run as tests.test_claims_and_races

import test_review_followups_provenance as provenance

from lean_compression import cli
from lean_compression.io import (CLAIM_PROTOCOL, append_event, claim, claim_locks, digest, evaluator_hash, file_hash,
                                 kept_claims, read_json, read_jsonl, remove_abandoned_claim, retain_claims, write_json)
from lean_compression.runner import evaluate_run, execute_engine
from lean_compression.store import create_run, record_generation

PROJECT = Path(__file__).resolve().parents[1]
ENGINE = PROJECT / "lean/Evaluator.lean"
IMAGE = "sha256:" + "0" * 64
REAL_POPEN = subprocess.Popen
UNREACHABLE = "Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the docker daemon running?"
# An environment record for Lean in containers; no container runs.
ENVIRONMENT = {"environment_hash": "fixture", "runtime_backend": "docker", "docker_image": IMAGE,
               "lean_binary": "/opt/lean/bin/lean", "toolchain_root": "/opt/lean", "lean_search_paths": [],
               "source_exclusion_audit": {"lean_sources": 0, "git_directories": 0}}
TASKS = [{"task_id": f"tiny_{n}", "family_id": f"family_{n}", "domain": "sets_logic", "split": "pilot", "origin": "fresh",
          "stratum": "provisional_easy", "imports": ["Init"], "context": "", "premise_context": "",
          "forbidden_declarations": [], "target_name": f"tiny_target_{n}", "binders": "(P : Prop) (h : P)",
          "proposition": " ∧ ".join(["P"] * n)} for n in (1, 2)]
# Claims no worker takes over whatever their lock: as code before claim protocols wrote them, of another protocol,
# and unreadable.
FOREIGN = [json.dumps({"pid": os.getpid(), "host": socket.gethostname(), "started_at": "2026-10-12T00:00:00+00:00",
                       "command": ["evaluate"]}),
           json.dumps({"claim_protocol": CLAIM_PROTOCOL + 1, "pid": os.getpid(), "host": socket.gethostname()}),
           "{", "", "[]"]
# A worker that evaluates a run and is killed while it publishes its first record: with the record in its temporary
# file only (unpublished), once it is published but before that file is deleted (published), or once that file is
# deleted but before the record's event is logged (unlogged).
KILLED = r"""
import os, signal, sys
from pathlib import Path
from unittest.mock import patch
from lean_compression import io, runner

run, engine, stage = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
real_link, real_append = os.link, runner.append_event
def link(source, destination):
    destination = Path(destination)
    record = destination.parent == run / "evaluations/v1" and destination.suffix == ".json" and destination.name != "policy.json"
    if record and stage == "unpublished":
        os.kill(os.getpid(), signal.SIGKILL)
    real_link(source, destination)
    if record and stage == "published":
        os.kill(os.getpid(), signal.SIGKILL)
def append(path, event, **options):
    if Path(path).name == "events.jsonl":
        os.kill(os.getpid(), signal.SIGKILL)
    return real_append(path, event, **options)
def checked(task, proof, environment, engine, config):
    return {"validity_status": "success", "metric_status": "available", "engine_hash": io.file_hash(engine),
            "validation": {"validity_status": "success"}, "evaluated_at": io.now(),
            "metrics": {"metric_status": "available", "W_S": 1, "W_B": 3, "U_S_decimal": "1", "U_B_decimal": "7", "D": 2}}
with patch("os.link", link), patch("lean_compression.runner.append_event", append), \
        patch("lean_compression.runner.evaluate_proof", checked):
    runner.evaluate_run(run, engine)
"""


def lean_success(*args):
    return {"validity_status": "success", "metric_status": "available", "engine_hash": file_hash(ENGINE),
            "validation": {"validity_status": "success", "resources": {"elapsed_seconds": 0.5}},
            "metrics": {"metric_status": "available", "W_S": 1, "W_B": 3, "U_S_decimal": "1", "U_B_decimal": "7", "D": 2}}


def claimable(path):
    """Whether a worker can claim `path` now; a claim it gets is released again."""
    with claim(path) as held:
        return held


def proof(slot):
    return f"by exact h -- sample {slot['schedule_index']}"


def run_cli(*argv):
    """Exit status, printed result (None on failure) and stderr of the CLI, with synthetic environment records."""
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr), \
            patch("lean_compression.cli.load_environment", read_json):
        try:
            cli.main([str(arg) for arg in argv])
        except SystemExit as stopped:
            return stopped.code, None, stderr.getvalue()
    return 0, json.loads(stdout.getvalue()), stderr.getvalue()


class Daemon:
    """A docker client whose daemon fails every container and cannot confirm removing it; it records what each run checks."""

    def __init__(self):
        self.runs, self.removals = [], []

    def popen(self, cmd, *args, **kwargs):
        if cmd[:3] == ["docker", "rm", "-f"]:
            self.removals.append(cmd[3])
            return REAL_POPEN(["/bin/sh", "-c", 'printf "%s\\n" "$1" >&2; exit 1', "docker", UNREACHABLE], *args, **kwargs)
        mount = next(value for flag, value in zip(cmd, cmd[1:]) if flag == "--mount" and "target=/job/request.json" in value)
        request = json.loads(Path(dict(part.partition("=")[::2] for part in mount.split(","))["source"]).read_text())
        self.runs.append((request["target_name"], request["proof"]))
        return REAL_POPEN(["/bin/sh", "-c", "exit 125"], *args, **kwargs)


class InterruptedClient(REAL_POPEN):
    """A docker client that hangs until the harness is interrupted while waiting for it, as by Ctrl-C."""

    def wait(self, timeout=None):
        if timeout is not None and not getattr(self, "interrupted", False):
            self.interrupted = True
            raise KeyboardInterrupt
        return super().wait(timeout)


class Workspace(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        # Attempts' scratch directories, which a container whose removal is unconfirmed keeps.
        (self.root / "scratch").mkdir()
        scratch = patch.object(tempfile, "tempdir", str(self.root / "scratch"))
        scratch.start()
        self.addCleanup(scratch.stop)
        (self.root / "tasks.jsonl").write_text("".join(json.dumps(task) + "\n" for task in TASKS))
        (self.root / "refs.jsonl").write_text("".join(json.dumps({"task_id": task["task_id"], "proof": "by exact foo"}) + "\n"
                                                      for task in TASKS))
        config = read_json(PROJECT / "configs/development.json")
        config["validation"].update(isolation="docker", docker_image=IMAGE)
        write_json(self.root / "config.json", config)
        write_json(self.root / "env.json", ENVIRONMENT)

    def container_run(self):
        """A development run of one task whose Lean checks run in containers, every slot generated."""
        config = read_json(PROJECT / "configs/pilot.json")
        config["validation"].update(isolation="docker", docker_image=IMAGE)
        environment = {**ENVIRONMENT, "lean_toolchain": config["environment"]["lean_toolchain"],
                       "mathlib_commit": config["environment"]["mathlib_commit"], "hash_mode": "development_metadata_only"}
        run = self.root / "run"
        slots = create_run(run, TASKS[:1], config, environment, split="pilot", model="pinned-model-2026-01-01",
                           development=True)["slots"]
        for slot in slots:
            record_generation(run, slot, proof(slot), generation_status="completed")
        return run, slots, run / "evaluations/v1"

    def check(self, command, output, *argv, popen=None):
        """The CLI's verify-references or baseline: its exit status, result and stderr, and how many checks Lean, stubbed,
        made. With popen, Lean runs in stand-in containers instead."""
        references = ["--references", self.root / "refs.jsonl"] if command == "verify-references" else []
        stub = (patch("lean_compression.runner.evaluate_proof", side_effect=lean_success) if popen is None else
                patch("lean_compression.runner.subprocess.Popen", popen))
        with stub as replaced:
            outcome = run_cli(command, "--tasks", self.root / "tasks.jsonl", *references, "--environment", self.root / "env.json",
                              "--config", self.root / "config.json", "--engine", ENGINE, "--output", output, *argv)
        return (*outcome, replaced.call_count if popen is None else None)


class ClaimProtocolTests(Workspace):
    """Issue 2: a claim records its protocol, and one that may be live without its lock is never taken over."""

    def test_a_claim_records_its_protocol(self):
        target = self.root / "slot.json"
        with claim(target) as held:
            self.assertEqual((held, read_json(target.with_suffix(".claim"))["claim_protocol"]), (True, CLAIM_PROTOCOL))

    def test_a_claim_of_another_protocol_or_unreadable_is_kept_whatever_its_lock(self):
        target = self.root / "slot.json"
        marker = target.with_suffix(".claim")
        for text in FOREIGN:
            with self.subTest(claim=text):
                marker.write_text(text)
                # No process holds its lock, yet its worker may be checking: it is neither taken over nor removed.
                self.assertFalse(claimable(target))
                write_json(target, {"recorded": True}, immutable=False)
                remove_abandoned_claim(target)
                self.assertEqual((marker.read_text(), kept_claims(self.root)), (text, [marker]))
                # Its operator deletes it once the process it names has exited.
                marker.unlink()
                target.unlink()
                self.assertTrue(claimable(target))
                self.assertEqual(kept_claims(self.root), [])

    def test_claims_released_out_of_order_leave_no_other_claim_behind(self):
        first, second = self.root / "first.json", self.root / "second.json"
        with contextlib.ExitStack() as outer:
            self.assertTrue(outer.enter_context(claim(first)))
            with claim(second) as held:
                outer.close()
                # Only the second claim is still held, and only it is kept.
                self.assertEqual((held, len(claim_locks.get())), (True, 1))
                self.assertEqual(retain_claims({"reason": "test"}), [second.with_suffix(".retained")])
        self.assertEqual((claim_locks.get(), retain_claims({"reason": "test"})), ((), []))
        self.assertEqual(sorted(path.name for path in self.root.glob("*.retained")), ["second.retained"])


class MixedCodeTests(Workspace):
    """Issue 2: policies record the claim protocol and the harness source, and workers of other code are refused."""

    def test_reference_and_baseline_directories_refuse_workers_of_other_code(self):
        for command in ("verify-references", "baseline"):
            output = self.root / command
            with self.subTest(command=command):
                self.assertEqual(self.check(command, output, "--limit", "1")[0], 0)
                policy = read_json(output / "policy.json")
                self.assertEqual((policy["claim_protocol"], policy["evaluator_source_hash"]), (CLAIM_PROTOCOL, evaluator_hash()))
                # Other harness code, or code of another claim protocol, joins before checking anything.
                for other, field in ((patch("lean_compression.io.evaluator_hash", return_value="other"), "evaluator_source_hash"),
                                     (patch("lean_compression.io.CLAIM_PROTOCOL", CLAIM_PROTOCOL + 1), "claim_protocol")):
                    with other:
                        code, result, log, checks = self.check(command, output)
                    self.assertEqual((code, checks), (2, 0), log)
                    self.assertIn(f"or harness code ({field}); use a new output directory", log)
                # A directory that code before claim protocols started is refused too.
                legacy = self.root / f"{command}-legacy"
                write_json(legacy / "policy.json", {key: value for key, value in policy.items()
                                                    if key not in ("claim_protocol", "evaluator_source_hash")})
                code, result, log, checks = self.check(command, legacy)
                self.assertEqual((code, checks), (2, 0))
                self.assertIn("or harness code (claim_protocol, evaluator_source_hash)", log)
                # A worker of other code that published its policy after this worker looked for one is refused alike.
                raced, real_exists = self.root / f"{command}-raced", Path.exists
                with patch("lean_compression.io.evaluator_hash", return_value="other"):
                    self.assertEqual(self.check(command, raced, "--limit", "0")[0], 0)
                with patch.object(Path, "exists", lambda path: path != raced / "policy.json" and real_exists(path)):
                    code, result, log, checks = self.check(command, raced)
                self.assertEqual((code, checks), (2, 0))
                self.assertIn("or harness code (evaluator_source_hash); use a new output directory", log)

    def test_an_evaluation_version_refuses_workers_of_other_code(self):
        run, slots, directory = self.container_run()
        evaluate_run(run, ENGINE, limit=0)
        policy = read_json(directory / "policy.json")
        self.assertEqual((policy["claim_protocol"], policy["evaluator_source_hash"]), (CLAIM_PROTOCOL, evaluator_hash()))
        for other in (patch("lean_compression.runner.CLAIM_PROTOCOL", CLAIM_PROTOCOL + 1),
                      patch("lean_compression.runner.evaluator_hash", return_value="other")):
            with self.subTest(other=other.attribute), other, patch("lean_compression.runner.evaluate_proof") as checker, \
                    self.assertRaisesRegex(ValueError, "was written by another engine, code or configuration"):
                evaluate_run(run, ENGINE)
            self.assertEqual(checker.call_count, 0)
        # A version that code before claim protocols started, and one another worker published after this one looked.
        legacy = self.root / "run/evaluations/legacy"
        write_json(legacy / "policy.json", {**{key: value for key, value in policy.items() if key != "claim_protocol"},
                                            "version": "legacy"})
        with self.assertRaisesRegex(ValueError, "Evaluation version legacy was written by another engine, code or configuration"):
            evaluate_run(run, ENGINE, version="legacy")
        with patch("lean_compression.runner.evaluator_hash", return_value="other"):
            evaluate_run(run, ENGINE, version="raced", limit=0)
        raced, real_exists = self.root / "run/evaluations/raced/policy.json", Path.exists
        with patch.object(Path, "exists", lambda path: path != raced and real_exists(path)), \
                self.assertRaisesRegex(ValueError, "Evaluation version raced was written by another engine, code or configuration"):
            evaluate_run(run, ENGINE, version="raced")
        # No refused worker claimed or recorded anything.
        self.assertEqual(sorted(path.name for path in run.glob("evaluations/*/*")),
                         ["policy.json", "policy.json", "policy.json"])

    def test_a_slot_claimed_under_another_protocol_waits_for_its_operator(self):
        run, slots, directory = self.container_run()
        evaluate_run(run, ENGINE, limit=0)
        marker = directory / f"{slots[0]['attempt_id']}.claim"
        marker.write_text(FOREIGN[0])
        with patch("lean_compression.runner.evaluate_proof", side_effect=lean_success) as checker:
            result = evaluate_run(run, ENGINE)
        self.assertEqual([call.args[1] for call in checker.call_args_list], [proof(slot) for slot in slots[1:]])
        self.assertEqual((result["new_evaluations"], result["kept_claims"]), (len(slots) - 1, [str(marker)]))
        marker.unlink()
        with patch("lean_compression.runner.evaluate_proof", side_effect=lean_success) as checker:
            result = evaluate_run(run, ENGINE)
        self.assertEqual(([call.args[1] for call in checker.call_args_list], result["new_evaluations"]), ([proof(slots[0])], 1))
        self.assertNotIn("kept_claims", result)


class KilledPublisherTests(Workspace):
    """Issue 3: a worker killed while it publishes a record leaves its temporary file and, once published, no event."""

    def killed(self, run, stage):
        worker = subprocess.run([sys.executable, "-c", KILLED, str(run), str(ENGINE), stage], capture_output=True,
                                env={**os.environ, "PYTHONPATH": str(PROJECT / "src"), "PYTHONDONTWRITEBYTECODE": "1"})
        self.assertEqual(worker.returncode, -signal.SIGKILL, worker.stderr.decode())

    def events(self, run):
        return [event["attempt_id"] for event in read_jsonl(run / "events.jsonl")] if (run / "events.jsonl").exists() else []

    def test_resuming_logs_the_published_records_event_once_and_deletes_only_its_own_temporary_file(self):
        run, slots, directory = self.container_run()
        self.killed(run, "published")
        first, second = (directory / f"{slot['attempt_id']}.json" for slot in slots[:2])
        own, = directory.glob(f".{first.name}.*")
        self.assertEqual((first.exists(), self.events(run), os.path.samefile(own, first)), (True, [], True))
        published = first.read_bytes()
        # Files resuming must not delete: the temporary file of a record a live worker has just published under its
        # claim, the policy's, a claim's, a link that only looks like this record's, and a signature's.
        outside = self.root / "outside"
        outside.write_text("kept")
        others = [directory / f".{second.name}.abcdefgh", directory / ".policy.json.abcdefgh",
                  directory / f".{first.stem}.claim.abcdefgh", directory / f".{first.name}.zzzzzzzz",
                  directory / "signatures" / f".{digest('tiny_1')}.json.abcdefgh"]
        (directory / "signatures").mkdir(exist_ok=True)
        for path in others[1:3] + others[4:]:
            path.write_text("{}")
        others[3].symlink_to(outside)
        with contextlib.ExitStack() as worker:
            self.assertTrue(worker.enter_context(claim(second)))
            write_json(second, {"attempt_id": slots[1]["attempt_id"], "validity_status": "success", "metric_status": "available"})
            os.link(second, others[0])
            with patch("lean_compression.runner.evaluate_proof", side_effect=lean_success) as checker:
                result = evaluate_run(run, ENGINE)
            self.assertEqual((result["new_evaluations"], checker.call_count), (len(slots) - 2, len(slots) - 2))
            self.assertTrue(all(os.path.lexists(path) for path in others))
        self.assertEqual(sorted(self.events(run)), sorted(slot["attempt_id"] for slot in slots if slot is not slots[1]))
        self.assertEqual((first.read_bytes(), own.exists(), list(directory.glob("*.claim"))), (published, False, []))
        self.assertEqual(outside.read_text(), "kept")
        # That worker stopped before finishing too: the next holder of its claim finishes; no event is logged twice.
        with patch("lean_compression.runner.evaluate_proof", side_effect=lean_success) as checker:
            self.assertEqual(evaluate_run(run, ENGINE)["new_evaluations"], 0)
            self.assertEqual(evaluate_run(run, ENGINE)["new_evaluations"], 0)
        self.assertEqual((sorted(self.events(run)), checker.call_count), (sorted(slot["attempt_id"] for slot in slots), 0))
        self.assertEqual([os.path.lexists(path) for path in others], [False, True, True, True, True])

    def test_resuming_logs_the_event_a_killed_worker_left_unlogged_once(self):
        run, slots, directory = self.container_run()
        self.killed(run, "unlogged")
        record = directory / f"{slots[0]['attempt_id']}.json"
        self.assertEqual((record.exists(), self.events(run), list(directory.glob(".*.json.*"))), (True, [], []))
        with patch("lean_compression.runner.evaluate_proof", side_effect=lean_success) as checker:
            self.assertEqual(evaluate_run(run, ENGINE)["new_evaluations"], len(slots) - 1)
            self.assertEqual(evaluate_run(run, ENGINE)["new_evaluations"], 0)
        self.assertEqual((sorted(self.events(run)), checker.call_count),
                         (sorted(slot["attempt_id"] for slot in slots), len(slots) - 1))
        event, = [event for event in read_jsonl(run / "events.jsonl") if event["attempt_id"] == slots[0]["attempt_id"]]
        self.assertEqual((event["event"], event["evaluation_version"], event["validity_status"], event["metric_status"]),
                         ("attempt_evaluated", "v1", "success", "available"))

    def test_resuming_checks_an_unpublished_record_again_and_deletes_its_temporary_file(self):
        run, slots, directory = self.container_run()
        self.killed(run, "unpublished")
        record = directory / f"{slots[0]['attempt_id']}.json"
        own, = directory.glob(f".{record.name}.*")
        self.assertEqual((record.exists(), self.events(run)), (False, []))
        with patch("lean_compression.runner.evaluate_proof", side_effect=lean_success):
            self.assertEqual(evaluate_run(run, ENGINE)["new_evaluations"], len(slots))
        self.assertEqual((own.exists(), sorted(self.events(run))), (False, sorted(slot["attempt_id"] for slot in slots)))

    def test_a_resumer_that_read_the_log_before_an_event_was_logged_logs_it_no_second_time(self):
        run, slots, directory = self.container_run()
        with patch("lean_compression.runner.evaluate_proof", side_effect=lean_success):
            evaluate_run(run, ENGINE)
            # Each record's worker logged its event after this resumer read the log.
            with patch("lean_compression.runner.logged_evaluations", return_value=set()):
                self.assertEqual(evaluate_run(run, ENGINE)["new_evaluations"], 0)
        self.assertEqual(sorted(self.events(run)), sorted(slot["attempt_id"] for slot in slots))
        self.assertEqual(list(directory.glob("*.claim")), [])

    def test_reference_and_baseline_reruns_delete_only_their_records_own_temporary_files(self):
        for command in ("verify-references", "baseline"):
            output = self.root / command
            with self.subTest(command=command):
                self.assertEqual(self.check(command, output)[0], 0)
                first, second = (output / f"{digest(task['task_id'])}.json" for task in TASKS)
                own, other = output / f".{first.name}.abcdefgh", output / f".{second.name}.abcdefgh"
                os.link(first, own)  # killed after publishing the record, before deleting its temporary file
                other.write_text("{}")
                with claim(second) as held:  # another worker holds the second record's claim
                    self.assertEqual((held, self.check(command, output)[1]["new_checks"]), (True, 0))
                self.assertEqual((own.exists(), other.exists(), first.exists()), (False, True, True))


class UnconfirmedRemovalTests(Workspace):
    """Issue 4: a container whose removal is unconfirmed keeps its task or slot claimed until an operator clears it."""

    def test_an_evaluated_slot_stays_claimed_until_its_operator_confirms_the_container_is_gone(self):
        run, slots, directory = self.container_run()
        daemon = Daemon()
        with patch("lean_compression.runner.subprocess.Popen", daemon.popen):
            result = evaluate_run(run, ENGINE, limit=1)
        retained = directory / f"{slots[0]['attempt_id']}.retained"
        kept = read_json(retained)
        self.assertEqual((result["checker_incidents"], result["kept_claims"]), (1, [str(retained)]))
        self.assertEqual((kept["reason"], kept["container"], kept["error"]),
                         ("container_removal_unconfirmed", daemon.removals[0], f"docker rm -f exited 1: {UNREACHABLE}"))
        self.assertEqual((kept["claim"]["claim_protocol"], kept["claim"]["pid"]), (CLAIM_PROTOCOL, os.getpid()))
        self.assertTrue((Path(kept["scratch_retained"]) / "job/request.json").is_file())
        incident, = read_jsonl(directory / "incidents.jsonl")
        self.assertEqual((incident["validation"]["resources"]["scratch_retained"], incident["validation"]["resources"]["claims_retained"]),
                         (kept["scratch_retained"], [str(retained)]))
        self.assertEqual(list(directory.glob("*.claim")), [])
        # While its container may still run, no worker checks that slot again; the others are checked.
        with patch("lean_compression.runner.subprocess.Popen", daemon.popen):
            result = evaluate_run(run, ENGINE, limit=1)
        self.assertEqual(daemon.runs, [("tiny_target_1", proof(slot)) for slot in slots[:2]])
        self.assertEqual(result["kept_claims"], sorted(str(directory / f"{slot['attempt_id']}.retained") for slot in slots[:2]))
        # Once the operator has confirmed both containers are gone and deleted these files, the slots are checked again.
        for path in directory.glob("*.retained"):
            path.unlink()
        with patch("lean_compression.runner.evaluate_proof", side_effect=lean_success) as checker:
            result = evaluate_run(run, ENGINE)
        self.assertEqual((result["new_evaluations"], checker.call_count), (len(slots), len(slots)))
        self.assertNotIn("kept_claims", result)

    def test_a_reference_or_baseline_task_stays_claimed_until_its_operator_confirms_the_container_is_gone(self):
        for command in ("verify-references", "baseline"):
            output, daemon = self.root / command, Daemon()
            with self.subTest(command=command):
                code, result, log, _ = self.check(command, output, "--limit", "1", popen=daemon.popen)
                retained = [output / f"{digest(task['task_id'])}.retained" for task in TASKS]
                self.assertEqual((code, result["checker_incidents"], result["kept_claims"]), (0, 1, [str(retained[0])]), log)
                incident, = read_jsonl(output / "incidents.jsonl")
                self.assertEqual(incident["validation"]["resources"]["claims_retained"], [str(retained[0])])
                code, result, log, _ = self.check(command, output, popen=daemon.popen)
                # The first task is not checked again; the baseline's first alternative's incident ends its portfolio.
                self.assertEqual([target for target, _ in daemon.runs], ["tiny_target_1", "tiny_target_2"])
                self.assertEqual((result["checker_incidents"], result["kept_claims"]), (1, sorted(map(str, retained))))
                for path in retained:
                    path.unlink()
                code, result, log, checks = self.check(command, output)
                self.assertEqual((code, result["new_checks"], checks), (0, len(TASKS), len(TASKS)), log)
                self.assertNotIn("kept_claims", result)

    def test_an_interrupted_check_whose_removal_is_unconfirmed_keeps_its_claim(self):
        target = self.root / "slot.json"
        retained = target.with_suffix(".retained")
        def popen(cmd, *args, **kwargs):
            if cmd[:3] == ["docker", "rm", "-f"]:
                return REAL_POPEN(["/bin/sh", "-c", 'printf "%s\\n" "$1" >&2; exit 1', "docker", UNREACHABLE], *args, **kwargs)
            return InterruptedClient(["/bin/sh", "-c", "exec sleep 60"], *args, **kwargs)
        with patch("lean_compression.runner.subprocess.Popen", popen), self.assertRaises(KeyboardInterrupt) as raised:
            with claim(target) as held:
                self.assertTrue(held)
                execute_engine({"mode": "validate"}, ENVIRONMENT, ENGINE, {"isolation": "docker", "docker_image": IMAGE},
                               mode="validate", wall_seconds=30)
        container = read_json(retained)["container"]
        self.assertEqual(raised.exception.__notes__[-1], f"Kept {retained}: delete it once container {container} is confirmed gone")
        # The interrupted worker left its claim to a lock no process holds, yet no worker takes the claim.
        self.assertTrue(target.with_suffix(".claim").exists())
        self.assertFalse(claimable(target))
        self.assertEqual(kept_claims(self.root), [retained])
        retained.unlink()
        self.assertTrue(claimable(target))

    def test_a_claim_kept_just_before_another_worker_publishes_its_own_is_not_held(self):
        target = self.root / "slot.json"
        marker, done, real_link = target.with_suffix(".claim"), [], os.link
        with contextlib.ExitStack() as holder:
            self.assertTrue(holder.enter_context(claim(target)))
            def link(source, destination):
                if Path(destination) == marker and not done:
                    # The holder keeps its claim and releases it before this worker publishes its own.
                    done.append(retain_claims({"reason": "container_removal_unconfirmed"}))
                    holder.close()
                return real_link(source, destination)
            with patch("os.link", link), claim(target) as held:
                self.assertEqual((held, done), (False, [[target.with_suffix(".retained")]]))
        self.assertEqual(sorted(path.name for path in self.root.iterdir() if path.name != "scratch"),
                         ["config.json", "env.json", "refs.jsonl", "slot.retained", "tasks.jsonl"])


class AmendmentRaceTests(provenance.Pipeline):
    """Issue 6: a version's reference_recheck amendment is logged only once its binding is the version's policy."""

    def setUp(self):
        super().setUp()
        self.verify("checks")
        self.assertEqual(self.freeze()[0], 0)
        self.generate()
        self.verify("rechecks", engine="E2")
        self.verify("other-rechecks", engine="E2", failures=("tiny_02",))
        self.policy = self.root / "run/evaluations/v3/policy.json"

    def amendments(self):
        path = self.root / "run/amendments.jsonl"
        return read_jsonl(path) if path.exists() else []

    def bind(self, checks, *argv):
        with patch("lean_compression.runner.evaluate_proof", side_effect=provenance.lean()):
            return provenance.run_cli("evaluate", "--run", self.root / "run", "--engine", self.engines["E2"], "--version", "v3",
                                      "--reference-checks", self.root / checks, *argv)

    def test_a_worker_refused_the_version_it_raced_to_bind_logs_no_amendment(self):
        self.assertEqual(self.bind("rechecks", "--limit", "0")[0], 0)
        real_exists = Path.exists
        # This worker looked for v3's policy just before the other worker published it.
        with patch.object(Path, "exists", lambda path: path != self.policy and real_exists(path)):
            code, result, log = self.bind("other-rechecks")
        self.assertEqual(code, 2)
        self.assertIn("Evaluation version v3 was written by other reference checks", log)
        event, = self.amendments()
        self.assertEqual((event["reference_policy_hash"], event["unanchored_reference_tasks"]),
                         (digest(read_json(self.root / "rechecks/policy.json")), {}))
        self.assertEqual(read_json(self.policy)["reference_policy_hash"], event["reference_policy_hash"])

    def test_an_amendment_a_killed_worker_left_unlogged_is_logged_once_by_the_next(self):
        real_append = append_event
        def killed(path, event, **options):
            if Path(path).name == "amendments.jsonl":
                raise KeyboardInterrupt  # killed after publishing v3's policy
            return real_append(path, event, **options)
        with patch("lean_compression.runner.append_event", side_effect=killed), self.assertRaises(KeyboardInterrupt):
            self.bind("rechecks")
        self.assertEqual((self.policy.exists(), self.amendments(), list(self.policy.parent.glob("*.claim"))), (True, [], []))
        code, result, log = self.bind("rechecks")
        self.assertEqual((code, result["new_evaluations"]), (0, 6), log)
        self.assertEqual(self.bind("rechecks")[:2], (0, {"new_evaluations": 0, "evaluation_version": "v3", "checker_incidents": 0}))
        event, = self.amendments()
        self.assertEqual((event["kind"], event["version"], event["reference_policy_hash"]),
                         ("reference_recheck", "v3", read_json(self.policy)["reference_policy_hash"]))


if __name__ == "__main__":
    unittest.main()
