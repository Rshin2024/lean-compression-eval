"""Runner and store follow-ups: resource kills, streamed logs, and a container with no writable host mount.

R2-incidents-docker-1: a process stopped by a resource limit gets the resource-limit outcome, whatever result record
it left, and an unreadable record without one stays a checker incident; both keep the process return code.
R2-incidents-docker-6: the incident and transport logs are read one line at a time, with unchanged summaries.
I31: the container's /job is a tmpfs and its request a read-only mount, so it has no writable host directory;
run-lean returns the result and the memory evidence at the end of stdout, under the caps the runner reads them with,
and a client log reaching its file-size limit is a resource limit too.
The real-Lean tests skip without the pinned runtime. The real-container tests opt in like test_docker_runtime.py:
LEAN_COMPRESSION_DOCKER_ENV names the environment record of an image built with this run-lean.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import re
import resource
import shutil
import subprocess
import tempfile
import tracemalloc
import unittest
from unittest.mock import patch

from lean_compression import runner
from lean_compression.io import append_event, canonical, read_json, write_json
from lean_compression.runner import (EVIDENCE_LIMIT, FILE_SIZE_LIMIT, _container_output, docker_cli_env, evaluate_proof,
                                     evaluate_run, execute_engine)
from lean_compression.store import (_log_events, _log_lines, create_run, evaluation_directory, generation_path,
                                    logged_outages, record_generation, records, rejected_prompts)
from lean_compression.integrity import is_content_digest

try:
    from lean_runtime import pinned_runtime
except ModuleNotFoundError:  # python3 -m unittest tests.test_runner_remaining, run from the repository root
    from tests.lean_runtime import pinned_runtime


ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "lean/Evaluator.lean"
REAL_POPEN = subprocess.Popen
IMAGE = "sha256:" + "4" * 64
SHELLS = [shell for shell in ("/bin/sh", "/bin/dash") if Path(shell).is_file()]
ENVIRONMENT = {"environment_hash": "fixture", "lean_binary": "/opt/lean/bin/lean", "toolchain_root": "/opt/lean",
               "lean_search_paths": ["/opt/lean/lib/lean"]}
REQUEST = {"imports": ["Init"], "target_name": "evaluation_target", "binders": "(P : Prop) (h : P)",
           "proposition": "P ∧ P", "proof": "⟨h, h⟩", "context": "", "forbidden_declarations": [],
           "permitted_axioms": ["propext", "Classical.choice", "Quot.sound"], "max_heartbeats": 2000000}
TASK = {"task_id": "remaining_1", "family_id": "remaining", "domain": "sets_logic", "split": "pilot", "origin": "fresh",
        "stratum": "provisional_easy", "imports": ["Init"], "context": "", "premise_context": "",
        "forbidden_declarations": [], "target_name": "remaining_target", "binders": "(P : Prop) (h : P)",
        "proposition": "P ∧ P"}
VALID = {"validity_status": "success", "metric_status": "not_requested", "kernel_replay": True,
         "candidate_declarations": ["remaining_target"], "diagnostics": {}}
# What Lean wrote when the file-size limit stopped it: the start of a pretty-printed record.
PARTIAL = '{"validity_status": "success",\n "nodes": [{"name": "And.in'
SIGNAL_EXITS = {"SIGKILL": -9, "SIGXCPU": -24, "SIGXFSZ": -25}


def lean_stand_in(script: str, left: str | bytes | None = None):
    """A Popen stand-in for the development sandbox. Lean leaves `left` as its result record, if given, and the shell
    script then plays its exit."""
    def popen(cmd, *args, **kwargs):
        if left is not None:
            Path(cmd[-1]).write_bytes(left.encode() if isinstance(left, str) else left)
        return REAL_POPEN(["/bin/sh", "-c", script], *args, **kwargs)
    return popen


def native(script: str, left: str | bytes | None = None, *, mode="validate") -> dict:
    with patch("platform.system", return_value="Darwin"), patch("subprocess.Popen", lean_stand_in(script, left)):
        return execute_engine(REQUEST, ENVIRONMENT, ENGINE, {"isolation": "macos_sandbox"}, mode=mode, wall_seconds=30)


def shlex_quote(text: str) -> str:
    return "'" + text.replace("'", "'\"'\"'") + "'"


def moved_run_lean(root: Path, job: Path, lean: str, cgroup_files: dict | None = None, text: str | None = None) -> Path:
    """runtime/run-lean (or text) with /opt/eval, /sys/fs/cgroup and /job moved under root and job; `lean` is a shell
    body whose $4 is the result path, as `lean --run ENGINE REQUEST RESULT` gives it."""
    (root / "bin").mkdir(parents=True)
    (root / "bin/lean").write_text("#!/bin/sh\n" + lean + "\n")
    (root / "bin/lean").chmod(0o755)
    (root / "lean-binary").write_text(f"{root}/bin/lean\n")
    (root / "lean-path").write_text(f"{root}/lib\n")
    (root / "cgroup").mkdir()
    for name, content in (cgroup_files or {}).items():
        (root / "cgroup" / name).write_bytes(content)
    moved = {"/opt/eval/": f"{root}/", "/sys/fs/cgroup": f"{root}/cgroup", "/job": str(job)}
    script = root / "run-lean"
    script.write_text(re.sub("|".join(map(re.escape, moved)), lambda found: moved[found.group()],
                             text or (ROOT / "runtime/run-lean").read_text()))
    return script


class ContainerStandIn:
    """Plays `docker run` with the real run-lean in a private directory standing in for the container's /job tmpfs."""

    def __init__(self, root: Path, lean: str, *, cgroup_files=None, text=None):
        self.root, self.lean, self.cgroup_files, self.text, self.calls = root, lean, cgroup_files, text, []

    def popen(self, cmd, *args, **kwargs):
        self.calls.append(list(cmd))
        if cmd[:3] == ["docker", "rm", "-f"]:
            return REAL_POPEN(["/bin/sh", "-c", "exit 0"], *args, **kwargs)
        mounts = {fields["target"]: fields["source"] for flag, value in zip(cmd, cmd[1:]) if flag == "--mount"
                  for fields in [dict(part.partition("=")[::2] for part in value.split(","))]}
        container = Path(tempfile.mkdtemp(dir=self.root))
        job = container / "job"
        job.mkdir()
        shutil.copyfile(mounts["/job/request.json"], job / "request.json")
        script = moved_run_lean(container / "image", job, self.lean, self.cgroup_files, self.text)
        arguments = [str(job) + value[len("/job"):] if value.startswith("/job/") else value
                     for value in cmd[cmd.index("/opt/eval/run-lean") + 1:]]
        # The image runs run-lean with Ubuntu's /bin/sh, which is dash.
        return REAL_POPEN([SHELLS[-1], str(script), *arguments], *args, **kwargs)


class TemporaryTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.temp = Path(temp.name).resolve()


class ResourceKillTests(TemporaryTest):
    """R2-incidents-docker-1: the resource exit is decided before the result record is trusted."""

    def test_a_resource_kill_is_the_resource_limit_outcome_whatever_result_it_left(self):
        for name, code in SIGNAL_EXITS.items():
            for mode, status in (("validate", "verification_resource_limit"), ("extract", "extractor_resource_limit")):
                for left, error in ((PARTIAL, "Unterminated string"), (json.dumps(VALID), "which is not used"),
                                    (None, "Lean did not produce its result record")):
                    with self.subTest(signal=name, mode=mode, left=left and left[:20]):
                        result = native(f"kill -s {name[3:]} $$", left, mode=mode)
                        self.assertEqual((result["validity_status"], result["metric_status"]), (status, "unavailable"))
                        self.assertEqual(result["process_returncode"], code)
                        self.assertIn(error, result["error"])
                        self.assertIn("elapsed_seconds", result["resources"])
                        self.assertEqual((result["stdout"], result["stderr"]), ("", ""))

    def test_an_unreadable_result_without_a_resource_exit_is_an_incident_that_keeps_its_return_code(self):
        # Nested deeper than the interpreter recurses, and bytes that are not UTF-8.
        cases = ((PARTIAL, 0, "Unterminated string"), (PARTIAL, 1, "Unterminated string"), ("", 0, "Expecting value"),
                 ("[1, 2]", 0, "not a JSON object"), ('"success"', 0, "not a JSON object"),
                 ("[" * 1000000 + "]" * 1000000, 0, "while decoding a JSON array"), (b"\xff\xfe", 0, "can't decode"),
                 (None, 3, "Lean did not produce its result record"))
        for left, code, error in cases:
            with self.subTest(left=left and left[:20], code=code):
                result = native(f'printf "said" >&2; exit {code}', left)
                self.assertEqual((result["validity_status"], result["metric_status"]), ("infrastructure_error", "unavailable"))
                self.assertEqual(result["process_returncode"], code)
                self.assertIn(error, result["error"])
                self.assertEqual(result["stderr"], "said")
                self.assertIn("elapsed_seconds", result["resources"])

    def test_a_complete_result_without_a_resource_exit_is_still_the_verdict(self):
        result = native("exit 0", json.dumps({**VALID, "heartbeats": {"max_heartbeats_units": {"total": 1.5}}}))
        self.assertEqual((result["validity_status"], result["process_returncode"]), ("success", 0))
        self.assertEqual(result["resources"]["heartbeats_used"], 1.5)

    @unittest.skipUnless(SHELLS, "POSIX sh is unavailable")
    def test_a_container_stopped_by_a_resource_limit_after_a_partial_write(self):
        # run-lean reports a signalled Lean as 128+N and returns the partial result like any other.
        for code, mode, status in ((153, "extract", "extractor_resource_limit"), (137, "validate", "verification_resource_limit"),
                                   (152, "extract", "extractor_resource_limit")):
            with self.subTest(code=code):
                stand_in = ContainerStandIn(self.temp, f"printf '%s' {shlex_quote(PARTIAL)} > \"$4\"\nexit {code}",
                                            cgroup_files={"memory.peak": b"1048576\n"})
                with patch("subprocess.Popen", stand_in.popen):
                    result = execute_engine(REQUEST, ENVIRONMENT, ENGINE, {"isolation": "docker", "docker_image": IMAGE},
                                            mode=mode, wall_seconds=30)
                self.assertEqual((result["validity_status"], result["process_returncode"]), (status, code))
                self.assertIn("Unterminated string", result["error"])
                self.assertEqual(result["resources"]["peak_memory_mib"], 1.0)
                self.assertEqual(stand_in.calls[-1][:3], ["docker", "rm", "-f"])
        for code in (0, 1):
            with self.subTest(code=code):
                stand_in = ContainerStandIn(self.temp, f"printf '%s' {shlex_quote(PARTIAL)} > \"$4\"\nexit {code}")
                with patch("subprocess.Popen", stand_in.popen):
                    result = execute_engine(REQUEST, ENVIRONMENT, ENGINE, {"isolation": "docker", "docker_image": IMAGE},
                                            mode="extract", wall_seconds=30)
                self.assertEqual((result["validity_status"], result["process_returncode"]), ("infrastructure_error", code))
                self.assertIn("Unterminated string", result["error"])

    def test_an_extraction_stopped_by_a_resource_limit_is_a_final_valid_record(self):
        """The reviewer's scenario: every rerun used to log the same incident, and freeze refused forever."""
        def popen(cmd, *args, **kwargs):
            if json.loads(Path(cmd[-2]).read_text())["mode"] == "validate":
                return lean_stand_in("exit 0", json.dumps(VALID))(cmd, *args, **kwargs)
            return lean_stand_in("kill -s XFSZ $$", PARTIAL)(cmd, *args, **kwargs)
        run = self.temp / "run"
        config = read_json(ROOT / "configs/development.json")
        config["samples_per_goal_condition"] = 1
        environment = {**ENVIRONMENT, "lean_toolchain": config["environment"]["lean_toolchain"],
                       "mathlib_commit": config["environment"]["mathlib_commit"], "hash_mode": "development_metadata_only"}
        manifest = create_run(run, [TASK], config, environment, split="pilot", model="reference_fixture", development=True)
        for slot in manifest["slots"]:
            record_generation(run, slot, "⟨h, h⟩", generation_status="completed")
        with patch("platform.system", return_value="Darwin"), patch("subprocess.Popen", popen):
            first = evaluate_run(run, ENGINE)
            again = evaluate_run(run, ENGINE)
        self.assertEqual((first["new_evaluations"], first["checker_incidents"]), (3, 0))
        self.assertEqual((again["new_evaluations"], again["checker_incidents"]), (0, 0))
        self.assertFalse((run / "evaluations/v1/incidents.jsonl").exists())
        for row in records(run):
            self.assertEqual((row["validity_status"], row["metric_status"]), ("success", "unavailable"))
            self.assertTrue(row["metric_unavailable_reason"].startswith("Unterminated string"), row["metric_unavailable_reason"])
            self.assertEqual((row["extraction"]["validity_status"], row["extraction"]["process_returncode"]),
                             ("extractor_resource_limit", -25))


@unittest.skipUnless(platform.system() == "Darwin" and Path("/usr/bin/sandbox-exec").exists(),
                     "the development sandbox is macOS-only")
class RealLeanResourceKillTests(unittest.TestCase):
    def test_real_extraction_cut_by_the_file_size_limit_is_final(self):
        environment, _ = pinned_runtime()
        real_limits, real_execute = runner._limits, runner.execute_engine

        def low(memory_mib, wall_seconds):
            # Only the extraction process: its record is larger than this, the validation's is not cut.
            real_limits(memory_mib, wall_seconds)
            resource.setrlimit(resource.RLIMIT_FSIZE, (2048, 2048))

        def execute(*args, mode, **kwargs):
            if mode != "extract":
                return real_execute(*args, mode=mode, **kwargs)
            with patch.object(runner, "_limits", low):
                return real_execute(*args, mode=mode, **kwargs)
        config = read_json(ROOT / "configs/development.json")
        with patch.object(runner, "execute_engine", execute):
            result = evaluate_proof(TASK, "⟨h, h⟩", environment, ENGINE, config)
        self.assertEqual((result["validity_status"], result["metric_status"]), ("success", "unavailable"), result)
        self.assertNotIn("failure_phase", result)
        self.assertEqual(result["validation"]["process_returncode"], 0)
        self.assertEqual((result["extraction"]["validity_status"], result["extraction"]["process_returncode"]),
                         ("extractor_resource_limit", -25))
        self.assertEqual(result["metric_unavailable_reason"], result["extraction"]["error"])


class StreamedLogTests(TemporaryTest):
    """R2-incidents-docker-6: the logs are streamed, with the summaries of the whole-log reader."""

    def make_run(self) -> tuple[Path, list[dict]]:
        config = read_json(ROOT / "configs/pilot.json")
        config["validation"].update(isolation="docker", docker_image=IMAGE)
        config["samples_per_goal_condition"] = 2
        environment = {**ENVIRONMENT, "lean_toolchain": config["environment"]["lean_toolchain"],
                       "mathlib_commit": config["environment"]["mathlib_commit"], "hash_mode": "development_metadata_only",
                       "runtime_backend": "docker", "docker_image": IMAGE,
                       "source_exclusion_audit": {"lean_sources": 0, "git_directories": 0}}
        tasks = [{**TASK, "task_id": f"remaining_{i}", "family_id": f"remaining_{i}", "target_name": f"target_{i}"}
                 for i in range(2)]
        run = self.temp / "run"
        return run, create_run(run, tasks, config, environment, split="pilot", model="m", development=True)["slots"]

    def test_summaries_of_awkward_logs(self):
        run, slots = self.make_run()
        ids = [slot["attempt_id"] for slot in slots]
        line = lambda value: canonical(value) + b"\n"
        v1 = evaluation_directory(run, "v1")
        v1.mkdir(parents=True)
        write_json(v1 / "policy.json", {"kind": "evaluate"})
        v2 = evaluation_directory(run, "v2")
        v2.mkdir(parents=True)
        write_json(v2 / "policy.json", {"kind": "rescore", "source_version": "v1"})
        (v1 / "incidents.jsonl").write_bytes(b"".join([
            line({"attempt_id": ids[0], "validity_status": "infrastructure_error",
                  "validation": {"error": "Cannot connect\u2028to the daemon\nsecond line"}}),
            # Its latest event decides an attempt's status, phase and reason.
            line({"attempt_id": ids[0], "validity_status": "evaluator_error", "failure_phase": "extraction",
                  "extraction": {"failure_reason": "x" * 300}, "adapted_response": "a\u2029b\u0085c"}),
            b"\n", b"  \t\r\n",
            line({"attempt_id": ids[1], "validity_status": "infrastructure_error", "error": "resolved later"}),
            b'{"attempt_id": "' + ids[2].encode() + b'", "validity_st',  # torn by a killed writer
            b"\n" + line({"attempt_id": ids[2], "validity_status": "infrastructure_error", "failure_phase": None}),
            line([1]), b"null\n", b"[" * 100000 + b"]" * 100000 + b"\n", b"\xff\xfe\n",
            line({"attempt_id": "unscheduled", "validity_status": "infrastructure_error"}),
            line({"attempt_id": 7, "validity_status": "infrastructure_error"}),
            b'{"attempt_id": "' + ids[3].encode() + b'", "validity_status": "\xffbad", "error": "\\n\\nlast"}']))
        (v1 / f"{ids[1]}.json").write_text("{}")
        (run / "transport.jsonl").write_bytes(b"".join([
            line({"event": "no_model_outcome", "attempt_id": ids[0], "prompt_rejected": True, "error": "a\u2028b"}),
            line({"event": "no_model_outcome", "attempt_id": ids[4], "prompt_rejected": "true"}),
            line({"event": "no_model_outcome", "attempt_id": ids[5]}),
            line({"event": "outcome_unknown", "attempt_id": ids[6]}),
            line({"event": "outcome_unknown", "attempt_id": None}),
            line({"event": ["no_model_outcome"], "attempt_id": ids[7]}),
            line({"event": "request_sent", "attempt_id": ids[8]}),
            line({"event": "no_model_outcome", "attempt_id": "unscheduled", "prompt_rejected": True}),
            b'{"event": "no_model_outcome", "attempt_id": "' + ids[9].encode() + b'", "prompt_rej',
            b"\n" + line({"event": "no_model_outcome", "attempt_id": ids[10], "prompt_rejected": True}),
            b"\r\n", line([]), b'{"event": "outcome_unknown", "attempt_id": "' + ids[11].encode() + b'"}']))
        generation_path(run, ids[5]).parent.mkdir(parents=True, exist_ok=True)
        generation_path(run, ids[5]).write_text("{}")
        rejected = {slot["prompt_hash"] for slot in slots if slot["attempt_id"] in (ids[0], ids[10])}
        sharing = {slot["attempt_id"] for slot in slots if slot["prompt_hash"] in rejected}
        # Values the whole-log reader gave for these logs.
        incidents = {"log": "evaluations/v1/incidents.jsonl", "events": 7, "unreadable_lines": 5, "attempts": 4,
                     "unresolved_attempts": 3,
                     "unresolved_statuses": {"evaluator_error": 1, "infrastructure_error": 1, "\ufffdbad": 1},
                     "unresolved_phases": {"None": 1, "extraction": 1, "validation": 1},
                     "unresolved_reasons": {"x" * 200: 1, "unreported": 1, "last": 1}}
        # Every slot sharing a rejected prompt is an outage; one with a generation is resolved.
        provider = {"log": "transport.jsonl", "no_model_outcome_events": 5, "outcome_unknown_events": 3,
                    "unreadable_lines": 2,
                    "unresolved_attempts": len(({ids[0], ids[4], ids[5], ids[6], ids[10], ids[11]} | sharing) - {ids[5]}),
                    "prompt_rejected_attempts": len(sharing - {ids[5]}), "in_flight_markers": 0}
        summary = logged_outages(run, "v1")
        self.assertEqual(summary, {"checker_incidents": incidents, "inherited_checker_incidents": None,
                                   "provider_outages": provider})
        self.assertEqual(logged_outages(run, "v2")["inherited_checker_incidents"],
                         {"source_version": "v1", **incidents, "unresolved_attempts": 4,
                          "unresolved_statuses": {"evaluator_error": 1, "infrastructure_error": 2, "\ufffdbad": 1},
                          "unresolved_phases": {"None": 1, "extraction": 1, "validation": 2},
                          "unresolved_reasons": {"x" * 200: 1, "resolved later": 1, "unreported": 1, "last": 1}})
        self.assertEqual(rejected_prompts(run, slots), rejected)
        events, unreadable = _log_events(run / "transport.jsonl")
        self.assertEqual(rejected_prompts(run, slots, events), rejected)
        self.assertEqual((len(events), unreadable), (10, 2))
        self.assertEqual(list(_log_lines(run / "absent.jsonl")), [])

    def test_memory_stays_bounded_by_the_longest_line(self):
        run, slots = self.make_run()
        directory = evaluation_directory(run, "v1")
        directory.mkdir(parents=True)
        padding = "by exact h -- \u2028" * 1000
        with (directory / "incidents.jsonl").open("wb") as incidents, (run / "transport.jsonl").open("wb") as transport:
            for index in range(3000):
                slot = slots[index % len(slots)]
                incidents.write(canonical({"event": "checker_incident", "attempt_id": slot["attempt_id"],
                                           "validity_status": "infrastructure_error", "adapted_response": padding,
                                           "validation": {"error": f"daemon down {index % 3}"}}) + b"\n")
                transport.write(canonical({"event": "no_model_outcome", "attempt_id": slot["attempt_id"],
                                           "prompt_rejected": index == 7, "provider_response": padding}) + b"\n")
        size = (directory / "incidents.jsonl").stat().st_size
        self.assertGreater(size, 40 * 1024 * 1024)
        tracemalloc.start()
        try:
            summary = logged_outages(run, "v1")
            rejected = rejected_prompts(run, slots)
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        # Reading a whole log at once took about five times its size.
        self.assertLess(peak, 4 * 1024 * 1024, peak)
        self.assertEqual((summary["checker_incidents"]["events"], summary["checker_incidents"]["unresolved_attempts"]),
                         (3000, len(slots)))
        self.assertEqual(summary["checker_incidents"]["unresolved_reasons"], {"daemon down 0": 4, "daemon down 1": 4,
                                                                              "daemon down 2": 4})
        self.assertEqual(summary["provider_outages"]["no_model_outcome_events"], 3000)
        self.assertEqual(rejected, {slots[7]["prompt_hash"]})

    def test_events_appended_after_a_torn_line_are_streamed(self):
        log = self.temp / "incidents.jsonl"
        append_event(log, {"attempt_id": "a1"})
        with log.open("ab") as out:
            out.write(b'{"attempt_id": "a2", "stderr": "' + b"x" * 9000)
        append_event(log, {"attempt_id": "a3", "text": "\u2028"})
        self.assertEqual([event and event["attempt_id"] for event in _log_lines(log)], ["a1", None, "a3"])


class ReturnedOutputTests(TemporaryTest):
    """I31: the container's output comes back on stdout, after what Lean printed, under the runner's caps."""

    def log(self, data: bytes) -> Path:
        path = self.temp / "stdout.log"
        path.write_bytes(data)
        return path

    def test_only_the_trailer_ending_the_stream_counts(self):
        result, peak, stat = b'{"validity_status": "success"}', b"2097152\n", b"anon 1\nfile 2\n"
        trailer = b"lean-compression-output v1 %d %d %d\n" % (len(result), len(peak), len(stat))
        forged = b'{"validity_status": "success", "forged": true}lean-compression-output v1 46 - -\n'
        printed = b"Lean printed this, then a trailer of its own:\n" + forged
        self.assertEqual(_container_output(self.log(printed + result + peak + stat + trailer)),
                         (len(printed), {"result.json": result, "peak_memory_bytes": peak, "memory_stat": stat}))
        self.assertEqual(_container_output(self.log(result + b"lean-compression-output v1 30 - !\n")),
                         (0, {"result.json": result, "peak_memory_bytes": "-", "memory_stat": "!"}))
        # No trailer (run-lean killed, or an older image), a trailer that is not last, or sizes beyond the stream.
        for data in (b"", printed[:-1], result + trailer + b"\n", b"x" + b"lean-compression-output v1 9 - -\n",
                     b"lean-compression-output v1 30 -\n", b"lean-compression-output v1 99999999999 - -\n"):
            with self.subTest(data=data[-40:]):
                self.assertEqual(_container_output(self.log(data)), (len(data), None))

    def test_returned_files_over_their_cap_are_never_read(self):
        evidence = b"9" * (EVIDENCE_LIMIT + 1)
        data = b'{"a": 1}' + evidence + b"lean-compression-output v1 8 %d -\n" % len(evidence)
        self.assertEqual(_container_output(self.log(data)), (0, {"result.json": b'{"a": 1}', "peak_memory_bytes": ">",
                                                                  "memory_stat": "-"}))
        with patch.object(runner, "FILE_SIZE_LIMIT", 7):
            self.assertEqual(_container_output(self.log(data))[1]["result.json"], ">")

    @unittest.skipUnless(SHELLS, "POSIX sh is unavailable")
    def test_caps_agree_and_no_host_directory_is_writable(self):
        script = (ROOT / "runtime/run-lean").read_text()
        self.assertIn(f'if [ "$size" -gt {FILE_SIZE_LIMIT} ]; then size={FILE_SIZE_LIMIT + 1}; fi', script)
        self.assertEqual(script.count(f"head -c {EVIDENCE_LIMIT + 1} "), 2)
        stand_in = ContainerStandIn(self.temp, "exit 0")
        with patch("subprocess.Popen", stand_in.popen):
            execute_engine(REQUEST, ENVIRONMENT, ENGINE, {"isolation": "docker", "docker_image": IMAGE},
                           mode="validate", wall_seconds=30)
        cmd = stand_in.calls[0]
        mounts = [value for flag, value in zip(cmd, cmd[1:]) if flag == "--mount"]
        self.assertTrue(mounts and all(value.startswith("type=bind,") and value.endswith(",readonly") for value in mounts))
        self.assertIn("--read-only", cmd)
        self.assertIn(f"fsize={FILE_SIZE_LIMIT}:{FILE_SIZE_LIMIT}", cmd)
        tmpfs = dict(value.split(":", 1) for flag, value in zip(cmd, cmd[1:]) if flag == "--tmpfs")
        self.assertEqual(set(tmpfs), {"/job", "/tmp"})
        size = re.search(r"size=(\d+)m", tmpfs["/job"])
        # A result too large stops Lean at the file-size limit, a resource limit, before /job fills.
        self.assertGreater(int(size.group(1)) * 1024 * 1024, FILE_SIZE_LIMIT + 2 * (EVIDENCE_LIMIT + 1))

    @unittest.skipUnless(SHELLS, "POSIX sh is unavailable")
    def test_run_lean_returns_only_readable_regular_files(self):
        secret = self.temp / "host-secret"
        secret.write_text("host-secret-canary")
        leans = {"symlink": f'ln -s {secret} "$4"', "fifo": 'mkfifo "$4"', "directory": 'mkdir "$4"',
                 "absent": ":", "empty": ': > "$4"', "unreadable": 'printf "{}" > "$4"; chmod 000 "$4"'}
        expected = {"symlink": "!", "fifo": "!", "directory": "!", "absent": "-", "empty": b"",
                    "unreadable": "!" if os.geteuid() else b"{}"}
        for shell in SHELLS:
            for name, lean in leans.items():
                with self.subTest(shell=shell, lean=name):
                    root = Path(tempfile.mkdtemp(dir=self.temp))
                    (root / "job").mkdir()
                    script = moved_run_lean(root / "image", root / "job", "printf 'printed'\n" + lean + "\nexit 5",
                                            {"memory.peak": b"7" * (EVIDENCE_LIMIT + 10), "memory.stat": b"anon 1\n"})
                    done = subprocess.run([shell, str(script), "/engine/E.lean", f"{root}/job/request.json",
                                           f"{root}/job/result.json"], capture_output=True, timeout=30)
                    self.assertEqual(done.returncode, 5, done.stderr)
                    (root / "stdout.log").write_bytes(done.stdout)
                    printed, files = _container_output(root / "stdout.log")
                    self.assertEqual(done.stdout[:printed], b"printed")
                    # The peak copy stops one byte over the evidence cap, so the runner sees it is oversized.
                    self.assertEqual(files, {"result.json": expected[name], "peak_memory_bytes": ">",
                                             "memory_stat": b"anon 1\n"})
                    self.assertNotIn(b"canary", done.stdout)

    @unittest.skipUnless(SHELLS, "POSIX sh is unavailable")
    def test_an_image_without_the_trailer_is_a_checker_incident(self):
        # An older run-lean leaves the result in /job, which is now the container's own tmpfs.
        legacy = (ROOT / "runtime/run-lean").read_text().split("# No host directory is writable")[0] + 'exit "$lean_exit"\n'
        stand_in = ContainerStandIn(self.temp, f"printf '%s' {shlex_quote(json.dumps(VALID))} > \"$4\"\nexit 0",
                                    cgroup_files={"memory.peak": b"1048576\n"}, text=legacy)
        with patch("subprocess.Popen", stand_in.popen):
            result = execute_engine(REQUEST, ENVIRONMENT, ENGINE, {"isolation": "docker", "docker_image": IMAGE},
                                    mode="validate", wall_seconds=30)
        self.assertEqual((result["validity_status"], result["process_returncode"]), ("infrastructure_error", 0))
        self.assertIn("predates returning them", result["error"])
        self.assertEqual((result["resources"]["peak_memory_mib"], result["resources"]["peak_memory_reason"]),
                         (None, "not_instrumented"))

    def test_a_client_log_at_the_file_size_limit_is_a_resource_limit(self):
        # The docker client ignores SIGXFSZ: it reports the failed write and exits 1, leaving its log at the limit.
        def client(stream):
            def popen(cmd, *args, **kwargs):
                if cmd[:3] == ["docker", "rm", "-f"]:
                    return REAL_POPEN(["/bin/sh", "-c", "exit 0"], *args, **kwargs)
                write = "head -c 10000 /dev/zero" + (" >&2" if stream == "stderr" else "")
                return REAL_POPEN(["/bin/sh", "-c", f"trap '' XFSZ; {write} || exit 1; exit 0"], *args, **kwargs)
            return popen
        for stream in ("stdout", "stderr"):
            for mode, status in (("validate", "verification_resource_limit"), ("extract", "extractor_resource_limit")):
                with self.subTest(stream=stream, mode=mode), patch.object(runner, "FILE_SIZE_LIMIT", 4096), \
                        patch("subprocess.Popen", client(stream)):
                    result = execute_engine(REQUEST, ENVIRONMENT, ENGINE, {"isolation": "docker", "docker_image": IMAGE},
                                            mode=mode, wall_seconds=30)
                    self.assertEqual((result["validity_status"], result["process_returncode"]), (status, 1))
                    self.assertEqual(result["resources"][f"{stream}_bytes"], 4096)
        # Output that fits, even exactly, is read as usual.
        with patch.object(runner, "FILE_SIZE_LIMIT", 10000), patch("subprocess.Popen", client("stdout")):
            result = execute_engine(REQUEST, ENVIRONMENT, ENGINE, {"isolation": "docker", "docker_image": IMAGE},
                                    mode="validate", wall_seconds=30)
        self.assertEqual((result["validity_status"], result["process_returncode"]), ("infrastructure_error", 0))
        self.assertIn("predates returning them", result["error"])



def docker_environment() -> dict:
    """The opt-in Docker environment record, as test_docker_runtime.py finds it, or SkipTest."""
    configured = os.environ.get("LEAN_COMPRESSION_DOCKER_ENV")
    if not configured:
        raise unittest.SkipTest("set LEAN_COMPRESSION_DOCKER_ENV to an existing Docker manifest to opt in")
    manifest = Path(configured).expanduser()
    if not manifest.is_file():
        raise unittest.SkipTest(f"Docker environment manifest does not exist: {manifest}")
    docker = shutil.which("docker")
    if not docker:
        raise unittest.SkipTest("Docker CLI is unavailable")
    try:
        if subprocess.run([docker, "info", "--format", "{{.ServerVersion}}"], env=docker_cli_env(),
                          capture_output=True, timeout=10).returncode:
            raise unittest.SkipTest("Docker daemon is unavailable")
        environment = json.loads(manifest.read_text())
        if environment.get("runtime_backend") != "docker" or not is_content_digest(environment.get("docker_image")):
            raise ValueError("the supplied manifest must describe runtime_backend=docker with an immutable docker_image")
        if subprocess.run([docker, "image", "inspect", environment["docker_image"]], env=docker_cli_env(),
                          capture_output=True, timeout=10).returncode:
            raise unittest.SkipTest("the manifest's immutable image is not available locally; no image is pulled")
    except subprocess.TimeoutExpired:
        raise unittest.SkipTest("Docker availability check timed out")
    return environment


# Trusted instrumentation, not a candidate proof: Lean runs a Python probe inside the same container.
PROBE_LEAN = r'''def main (args : List String) : IO UInt32 := do
  let probe ← IO.Process.output { cmd := "/usr/bin/python3", args := #["/engine/channel_probe.py", args[0]!, args[1]!] }
  IO.print probe.stdout
  IO.eprint probe.stderr
  return probe.exitCode
'''
PROBE = r'''import json, os, sys
request, result = sys.argv[1], sys.argv[2]
mounts = []
for line in open("/proc/self/mountinfo"):
    left, _, right = line.partition(" - ")
    mounts.append({"target": left.split()[4], "options": left.split()[5].split(","), "fstype": right.split()[0]})
try:
    open(request, "a").close()
    request_writable = True
except OSError:
    request_writable = False
# Files the host must never see: 64 MiB in /job.
for index in range(64):
    with open(f"/job/fill-{index}", "wb") as out:
        out.write(b"x" * (1 << 20))
job = os.statvfs("/job")
print("printed by the probe")
padding = "p" * (20 * 1024 * 1024)
with open(result, "w") as out:
    json.dump({"validity_status": "success", "metric_status": "not_requested", "padding": padding, "probe": {
        "writable_mounts": sorted({(m["target"], m["fstype"]) for m in mounts if "rw" in m["options"]}),
        "job_fstype": [m["fstype"] for m in mounts if m["target"] == "/job"],
        "job_bytes": job.f_blocks * job.f_frsize, "request_writable": request_writable,
        "request_mode": json.load(open(request))["mode"]}}, out)
'''


class DockerChannelAcceptanceTests(TemporaryTest):
    """A real container: nothing it writes reaches the host, and its result comes back whole on stdout."""

    @classmethod
    def setUpClass(cls):
        cls.environment = docker_environment()

    def test_no_writable_host_mount_and_a_large_result_returns_whole(self):
        fixture = self.temp / "engine"
        fixture.mkdir()
        (fixture / "ChannelProbe.lean").write_text(PROBE_LEAN)
        (fixture / "channel_probe.py").write_text(PROBE)
        fixture.chmod(0o755)
        for path in fixture.iterdir():
            path.chmod(0o644)
        seen, real_remove = [], runner._remove_scratch

        def remove(scratch):
            seen.append(sorted(path.relative_to(scratch).as_posix() for path in scratch.rglob("*")))
            return real_remove(scratch)
        with patch.object(runner, "_remove_scratch", remove):
            result = execute_engine(REQUEST, self.environment, fixture / "ChannelProbe.lean",
                                    {"isolation": "docker", "docker_image": self.environment["docker_image"],
                                     "memory_mib": 1024, "cpu_quota": 1}, mode="validate", wall_seconds=120)
        self.assertEqual((result["validity_status"], result["process_returncode"]), ("success", 0),
                         {key: value for key, value in result.items() if key != "padding"})
        probe = result["probe"]
        self.assertEqual(probe["job_fstype"], ["tmpfs"])
        self.assertEqual(probe["job_bytes"], 320 * 1024 * 1024)
        self.assertEqual((probe["request_writable"], probe["request_mode"]), (False, "validate"))
        # Every writable mount is memory: tmpfs, proc, devpts or mqueue; the image, engine and request are read-only.
        self.assertLessEqual({fstype for _, fstype in probe["writable_mounts"]}, {"tmpfs", "proc", "devpts", "mqueue"},
                             probe["writable_mounts"])
        self.assertIn(["/job", "tmpfs"], probe["writable_mounts"])
        # 64 MiB went to /job and a 20 MiB result came back; the host saw only the request and the client's logs.
        self.assertEqual(seen, [["job", "job/request.json", "stderr.log", "stdout.log"]])
        self.assertEqual(hashlib.sha256(result["padding"].encode()).hexdigest(),
                         hashlib.sha256(b"p" * (20 * 1024 * 1024)).hexdigest())
        self.assertEqual((result["stdout"], result["resources"]["stdout_bytes"]), ("printed by the probe\n", 21))
        self.assertGreater(result["resources"]["peak_memory_mib"], 64)
        self.assertIsNotNone(result["resources"]["memory_stat_at_exit"]["file"])

    def test_extraction_cut_by_the_file_size_limit_is_final(self):
        lowered = []

        def popen(cmd, *args, **kwargs):
            if cmd[:2] == ["docker", "run"]:
                source = next(value for value in cmd if value.endswith("target=/job/request.json,readonly"))
                request = Path(source.split(",")[1].removeprefix("source="))
                if json.loads(request.read_text())["mode"] == "extract":
                    # Only the extraction: its record is larger than this, the validation's is not cut.
                    cmd = [("fsize=2048:2048" if part == f"fsize={FILE_SIZE_LIMIT}:{FILE_SIZE_LIMIT}" else part)
                           for part in cmd]
                    lowered.append(cmd)
            return REAL_POPEN(cmd, *args, **kwargs)
        config = read_json(ROOT / "configs/development.json")
        config["validation"].update(isolation="docker", docker_image=self.environment["docker_image"], wall_seconds=120)
        config["extraction"]["wall_seconds"] = 120
        with patch("subprocess.Popen", popen):
            result = evaluate_proof(TASK, "⟨h, h⟩", self.environment, ENGINE, config)
        self.assertEqual(len(lowered), 1)
        self.assertEqual((result["validity_status"], result["metric_status"]), ("success", "unavailable"), result)
        self.assertEqual((result["extraction"]["validity_status"], result["extraction"]["process_returncode"]),
                         ("extractor_resource_limit", 153))
        self.assertEqual(result["metric_unavailable_reason"], result["extraction"]["error"])
        self.assertIsNotNone(result["extraction"]["resources"]["peak_memory_mib"])


if __name__ == "__main__":
    unittest.main()
