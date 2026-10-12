"""Runtime gates checked without a Docker daemon.

runtime/run-lean runs for real under the local POSIX shells, with its container paths
(/opt/eval, /sys/fs/cgroup, /job) moved into a temporary directory holding fake cgroup files
and a stand-in for Lean. The runner's Docker branch then records what it copied. The CLI
checks bind verify-references and baseline to the image that runs Lean, and index-closures
to a verify-references directory. freeze, verify-references, baseline, the runner and
runtime/prepare_image.py accept only a content digest as the image.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from lean_compression import cli
from lean_compression.integrity import is_content_digest
from lean_compression.io import read_json
from lean_compression.runner import execute_engine
from lean_compression.store import create_run


ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "lean/Evaluator.lean"
IMAGE, OTHER_IMAGE = "sha256:" + "1" * 64, "sha256:" + "2" * 64
DIGESTS = (IMAGE, "registry.example/lean@sha256:" + "a" * 64, "lean-eval:latest@sha256:" + "0123456789abcdef" * 4)
# A tag-like reference, an empty and a truncated ID, a malformed digest, an empty and a malformed
# name before "@sha256:", a plain tag, uppercase hex and a trailing newline.
NOT_DIGESTS = ("sha256:latest", "sha256:", "sha256:5d0da3dc9764", "lean-eval:latest@sha256:x", "@" + IMAGE,
               "lean eval@" + IMAGE, "lean-compression-eval:latest", "sha256:" + "A" * 64, IMAGE + "\n")
REAL_POPEN = subprocess.Popen
# The image runs run-lean with Ubuntu's /bin/sh, which is dash.
SHELLS = [shell for shell in ("/bin/sh", "/bin/dash") if Path(shell).is_file()]
REQUEST = {"imports": ["Init"], "target_name": "gateTest", "binders": "", "proposition": "True",
           "proof": "True.intro", "context": "", "forbidden_declarations": [], "permitted_axioms": [],
           "max_heartbeats": 200000}
ENVIRONMENT = {"lean_binary": "/opt/lean/bin/lean", "toolchain_root": "/opt/lean",
               "lean_search_paths": ["/opt/lean/lib/lean"]}
# Plays Lean and the evaluated code inside it: may plant entries under the evidence names and
# update the cgroup files in CGROUP as the kernel would, then dies by SIGKILL (as on OOM), runs
# past any wall limit, or writes its result and exits with LEAN_OUTCOME.
FAKE_LEAN = """#!/bin/sh
job=$(dirname "$4")
case "$PLANT" in
    files) echo "${PLANTED_PEAK:-1}" > "$job/peak_memory_bytes"; echo 'anon 1' > "$job/memory_stat" ;;
    directory) mkdir "$job/memory_stat" ;;
esac
if [ -n "$CGROUP" ]; then
    echo 8589934592 > "$CGROUP/memory.peak"
    echo 'anon 0' > "$CGROUP/memory.stat"
fi
if [ "$LEAN_OUTCOME" = KILL ]; then
    kill -s KILL $$
fi
if [ "$LEAN_OUTCOME" = HANG ]; then
    exec sleep 60
fi
echo '{"validity_status": "success", "metric_status": "not_requested"}' > "$4"
exit "$LEAN_OUTCOME"
"""
V2 = {"memory.peak": b"4294967296\n",
      "memory.stat": b"anon 1048576\nfile 3221225472\nkernel 524288\nfile_mapped 2097152\nshmem 0\n"
                     b"pgfault 900\npgmajfault 12345\n"}
V1 = {"memory/memory.max_usage_in_bytes": b"3221225472\n",
      "memory/memory.stat": b"cache 3221225472\nrss 1048576\nrss_huge 0\nmapped_file 2097152\npgfault 900\n"
                            b"pgmajfault 7\ntotal_cache 1\ntotal_rss 2\ntotal_mapped_file 3\ntotal_pgmajfault 4\n"}
SCOPE = "cgroup high-water mark of all memory charged to the container, page cache included"
NO_STAT = {"anon": None, "file": None, "file_mapped": None, "pgmajfault": None}


def container(root: Path, job: Path, cgroup_files: dict) -> Path:
    """Write run-lean with its container paths moved under root, plus fake cgroup files and Lean."""
    lean = root / "toolchain/bin/lean"
    lean.parent.mkdir(parents=True)
    lean.write_text(FAKE_LEAN)
    lean.chmod(0o755)
    (root / "opt").mkdir()
    (root / "opt/lean-path").write_text(f"{root}/lib\n")
    (root / "opt/lean-binary").write_text(f"{lean}\n")
    cgroup = root / "cgroup"
    cgroup.mkdir()
    for name, content in cgroup_files.items():
        (cgroup / name).parent.mkdir(exist_ok=True)
        (cgroup / name).write_bytes(content)
    moved = {"/opt/eval/": f"{root}/opt/", "/sys/fs/cgroup": str(cgroup), "/job": str(job)}
    text = (ROOT / "runtime/run-lean").read_text()
    if not all(path in text for path in moved):
        raise AssertionError("run-lean no longer uses the container paths this test moves")
    script = root / "run-lean"
    script.write_text(re.sub("|".join(map(re.escape, moved)), lambda found: moved[found.group()], text))
    return script


def tiny_task() -> dict:
    return {"task_id": "tiny_1", "family_id": "family_1", "domain": "sets_logic", "split": "pilot", "origin": "fresh",
            "stratum": "provisional_easy", "imports": ["Init"], "context": "", "premise_context": "",
            "forbidden_declarations": [], "target_name": "tiny_target_1", "binders": "(P : Prop) (h : P)",
            "proposition": "P ∧ P"}


def stub_engine(request, environment, engine, policy, *, mode, wall_seconds):
    result = {"validity_status": "success", "process_returncode": 0, "resources": {"wall_seconds_limit": wall_seconds}}
    if mode == "validate":
        return {**result, "metric_status": "not_requested"}
    return {**result, "metric_status": "unavailable", "metric_error": "stubbed extraction"}


@unittest.skipUnless(SHELLS, "POSIX sh is unavailable")
class RunLeanMemoryEvidenceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.temp = Path(temp.name).resolve()

    def run_lean(self, shell, cgroup_files, *, outcome="0", plant="", updated=False):
        root = Path(tempfile.mkdtemp(dir=self.temp))
        job = root / "job"
        job.mkdir()
        script = container(root, job, cgroup_files)
        done = subprocess.run([shell, str(script), "/engine/Evaluator.lean", f"{job}/request.json", f"{job}/result.json"],
                              env={"PATH": "/usr/bin:/bin", "LEAN_OUTCOME": outcome, "PLANT": plant,
                                   "CGROUP": f"{root}/cgroup" if updated else ""},
                              capture_output=True, timeout=30)
        return done.returncode, {name: (job / name).read_bytes() for name in ("peak_memory_bytes", "memory_stat")
                                 if (job / name).is_file()}

    def test_run_lean_parses_under_each_shell(self):
        for shell in SHELLS:
            with self.subTest(shell=shell):
                checked = subprocess.run([shell, "-n", str(ROOT / "runtime/run-lean")], capture_output=True, text=True, timeout=10)
                self.assertEqual(checked.returncode, 0, checked.stderr)

    def test_evidence_is_taken_after_container_init_stops_what_lean_left_running(self):
        # Nothing Lean started can plant or rewrite the evidence after it is dropped and copied. As container
        # init only, run-lean stops what Lean left running (kill -1 on a host would reach every process of
        # the user), so the order is checked in the script itself.
        lines = (ROOT / "runtime/run-lean").read_text().splitlines()
        steps = ["--run", "kill -KILL -1", "rm -f /job/peak_memory_bytes /job/memory_stat", "> /job/peak_memory_bytes",
                 "> /job/memory_stat", "chmod -R a+rwX,a-t /job"]
        order = [next(index for index, line in enumerate(lines) if step in line) for step in steps]
        self.assertEqual(order, sorted(order))

    def test_cgroup_v2_and_v1_files_are_copied_after_lean_exits(self):
        for shell in SHELLS:
            for files, peak, stat in ((V2, "memory.peak", "memory.stat"),
                                      (V1, "memory/memory.max_usage_in_bytes", "memory/memory.stat")):
                with self.subTest(shell=shell, peak=peak):
                    self.assertEqual(self.run_lean(shell, files),
                                     (0, {"peak_memory_bytes": files[peak], "memory_stat": files[stat]}))
                with self.subTest(shell=shell, updated_by_lean=True):
                    self.assertEqual(self.run_lean(shell, V2, updated=True),
                                     (0, {"peak_memory_bytes": b"8589934592\n", "memory_stat": b"anon 0\n"}))

    def test_lean_exit_status_is_kept_and_a_killed_lean_still_leaves_its_peak(self):
        for shell in SHELLS:
            with self.subTest(shell=shell):
                self.assertEqual(self.run_lean(shell, V2, outcome="3"),
                                 (3, {"peak_memory_bytes": V2["memory.peak"], "memory_stat": V2["memory.stat"]}))
                code, evidence = self.run_lean(shell, V2, outcome="KILL")
                self.assertEqual((code, evidence.get("peak_memory_bytes")), (137, V2["memory.peak"]))

    def test_missing_cgroup_files_leave_no_evidence_even_if_the_evaluated_code_planted_some(self):
        for shell in SHELLS:
            with self.subTest(shell=shell):
                self.assertEqual(self.run_lean(shell, {}, outcome="5", plant="files"), (5, {}))


@unittest.skipUnless(SHELLS, "POSIX sh is unavailable")
class DockerMemoryEvidenceTests(unittest.TestCase):
    """execute_engine's Docker branch, with run-lean playing the container against fake cgroup files."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.temp = Path(temp.name).resolve()
        self.engine = self.temp / "Evaluator.lean"
        self.engine.write_text("-- engine\n")

    def execute(self, cgroup_files, *, outcome="0", plant="", planted_peak="1", wall_seconds=30, removal_error=None):
        def popen(cmd, *args, **kwargs):
            if cmd[:3] == ["docker", "rm", "-f"]:
                if removal_error:
                    raise removal_error
                return REAL_POPEN(["/bin/sh", "-c", "exit 0"], *args, **kwargs)
            mounts = {}
            for flag, value in zip(cmd, cmd[1:]):
                if flag == "--mount":
                    fields = dict(part.partition("=")[::2] for part in value.split(","))
                    mounts[fields["target"]] = fields["source"]
            script = container(Path(tempfile.mkdtemp(dir=self.temp)), Path(mounts["/job"]), cgroup_files)
            arguments = [mounts["/job"] + value[len("/job"):] if value.startswith("/job/") else value
                         for value in cmd[cmd.index("/opt/eval/run-lean") + 1:]]
            return REAL_POPEN([SHELLS[-1], str(script), *arguments], *args,
                              **{**kwargs, "env": {"PATH": "/usr/bin:/bin", "LEAN_OUTCOME": outcome, "PLANT": plant,
                                                   "PLANTED_PEAK": planted_peak}})
        with patch("subprocess.Popen", popen):
            return execute_engine(REQUEST, ENVIRONMENT, self.engine,
                                  {"isolation": "docker", "docker_image": IMAGE, "memory_mib": 4096, "cpu_quota": 1},
                                  mode="validate", wall_seconds=wall_seconds)

    def test_cgroup_v2_and_v1_evidence_is_recorded_with_the_peak_scope(self):
        for files, peak_mib, faults in ((V2, 4096.0, 12345), (V1, 3072.0, 7)):
            with self.subTest(peak_mib=peak_mib):
                result = self.execute(files)
                resources = result["resources"]
                self.assertEqual(result["validity_status"], "success", result)
                self.assertEqual((resources["peak_memory_mib"], resources["peak_memory_reason"],
                                  resources["peak_memory_scope"]), (peak_mib, None, SCOPE))
                self.assertEqual(resources["memory_stat_at_exit"],
                                 {"anon": 1048576, "file": 3221225472, "file_mapped": 2097152, "pgmajfault": faults})

    def test_missing_unreadable_or_malformed_evidence_is_null_and_never_fails_the_check(self):
        # "²" passes str.isdigit() but not int().
        cases = (("missing", {}, ""), ("planted by the evaluated code", {}, "files"),
                 ("malformed", {"memory.peak": b"max\n",
                                "memory.stat": "anon -1\nfile 12 KiB\nfile_mapped ²\npgmajfault\n".encode()}, ""),
                 ("undecodable", {"memory.peak": b"\xff\n", "memory.stat": b"anon \xff\n"}, ""),
                 ("not a regular file", {"memory.stat": V2["memory.stat"]}, "directory"))
        for name, files, plant in cases:
            with self.subTest(name):
                result = self.execute(files, plant=plant)
                resources = result["resources"]
                self.assertEqual(result["validity_status"], "success", result)
                self.assertEqual((resources["peak_memory_mib"], resources["peak_memory_reason"]), (None, "not_instrumented"))
                self.assertNotIn("peak_memory_scope", resources)
                self.assertEqual(resources["memory_stat_at_exit"], NO_STAT)

    def test_out_of_range_values_and_oversized_files_are_null_and_never_fail_the_check(self):
        # int() refuses more than 4300 digits and 400 digits overflow the MiB division; a 64-bit counter
        # has at most 20. A file over 64 KiB is malformed even when it holds valid lines.
        cases = [(f"{len(value)} digits", {"memory.peak": value + b"\n", "memory.stat": b"anon " + value + b"\n"})
                 for value in (b"9" * 5000, b"9" * 400, b"1" * 21)]
        cases.append(("over 64 KiB", {"memory.peak": b"4294967296\n" + b"\n" * 65536,
                                      "memory.stat": V2["memory.stat"] + b"x 0\n" * 16384}))
        for name, files in cases:
            with self.subTest(name):
                result = self.execute(files)
                resources = result["resources"]
                self.assertEqual(result["validity_status"], "success", result)
                self.assertEqual((resources["peak_memory_mib"], resources["peak_memory_reason"]), (None, "not_instrumented"))
                self.assertEqual(resources["memory_stat_at_exit"], NO_STAT)
        largest = 2 ** 64 - 1
        result = self.execute({"memory.peak": b"%d\n" % largest, "memory.stat": b"anon %d\n" % largest})
        self.assertEqual((result["resources"]["peak_memory_mib"], result["resources"]["memory_stat_at_exit"]["anon"]),
                         (largest / (1024 * 1024), largest))

    def test_files_left_after_a_timeout_or_an_unconfirmed_removal_are_not_read(self):
        # run-lean was killed before its copy, or the container may still run: these files may be planted.
        removal = FileNotFoundError(2, "No such file", "docker")
        cases = (("timeout, 5000-digit peak", {"outcome": "HANG", "planted_peak": "9" * 5000, "wall_seconds": 2},
                  "verification_timeout"),
                 ("timeout, 400-digit peak", {"outcome": "HANG", "planted_peak": "9" * 400, "wall_seconds": 2},
                  "verification_timeout"),
                 ("unconfirmed removal", {"outcome": "3", "removal_error": removal}, "infrastructure_error"))
        for name, options, status in cases:
            with self.subTest(name):
                result = self.execute(V2, plant="files", **options)
                resources = result["resources"]
                self.assertEqual(result["validity_status"], status, result)
                self.assertEqual((resources["peak_memory_mib"], resources["peak_memory_reason"]),
                                 (None, "not_read_after_timeout_or_unconfirmed_cleanup"))
                self.assertNotIn("peak_memory_scope", resources)
                self.assertNotIn("memory_stat_at_exit", resources)

    def test_a_lean_killed_for_memory_keeps_its_peak(self):
        result = self.execute(V2, outcome="KILL")
        self.assertEqual((result["validity_status"], result["process_returncode"]), ("verification_resource_limit", 137))
        self.assertEqual((result["resources"]["peak_memory_mib"], result["resources"]["memory_stat_at_exit"]["file"]),
                         (4096.0, 3221225472))


class ContentDigestTests(unittest.TestCase):
    """freeze, the runner and prepare_image.py take only a complete image ID or repository digest; the reference
    checks' refusal is tested with the other gates in ReferenceCheckGateTests."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.temp = Path(temp.name).resolve()

    def test_only_a_complete_sha256_image_id_or_repository_digest_passes(self):
        for image in DIGESTS:
            self.assertTrue(is_content_digest(image), image)
        for image in (*NOT_DIGESTS, None, 1):
            self.assertFalse(is_content_digest(image), image)

    def test_freeze_and_the_runner_refuse_any_other_image_reference(self):
        config = read_json(ROOT / "configs/pilot.json")
        engine = self.temp / "Evaluator.lean"
        engine.write_text("-- engine\n")
        calls = []

        def popen(cmd, *args, **kwargs):
            calls.append(cmd)
            return REAL_POPEN(["/bin/sh", "-c", "exit 1"], *args, **kwargs)

        def freeze(path, image):
            environment = {"environment_hash": "fixture", "runtime_backend": "docker", "docker_image": image,
                           "source_exclusion_audit": {"lean_sources": 0, "git_directories": 0},
                           **{key: config["environment"][key] for key in ("lean_toolchain", "mathlib_commit")}}
            return create_run(path, [tiny_task()], {**config, "validation": {**config["validation"], "docker_image": image}},
                              environment, split="pilot", model="reference_fixture", development=True)

        def check(image):
            return execute_engine(REQUEST, ENVIRONMENT, engine, {**config["validation"], "docker_image": image},
                                  mode="validate", wall_seconds=30)
        for index, image in enumerate((*DIGESTS, *NOT_DIGESTS)):
            run = self.temp / f"run-{index}"
            calls.clear()
            with self.subTest(image=image), patch("subprocess.Popen", popen):
                if image in DIGESTS:
                    self.assertEqual(freeze(run, image)["environment"]["docker_image"], image)
                    check(image)
                    self.assertEqual(calls[0][calls[0].index("/opt/eval/run-lean") - 1], image)
                else:
                    with self.assertRaisesRegex(ValueError, "is not an immutable content digest"):
                        freeze(run, image)
                    self.assertFalse(run.exists())
                    with self.assertRaisesRegex(ValueError, "immutable docker_image content digest"):
                        check(image)
                    self.assertEqual(calls, [])

    def test_prepare_image_refuses_any_other_image_reference_before_running_docker(self):
        stand_in = self.temp / "bin/docker"
        stand_in.parent.mkdir()
        stand_in.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$DOCKER_CALLS"\nexit 1\n')
        stand_in.chmod(0o755)
        calls, output = self.temp / "docker-calls", self.temp / "environment.docker.json"
        for image in (*DIGESTS, *NOT_DIGESTS):
            with self.subTest(image=image):
                calls.write_text("")
                done = subprocess.run([sys.executable, str(ROOT / "runtime/prepare_image.py"), "--image-id", image,
                                       "--output", str(output)], capture_output=True, text=True, timeout=60,
                                      env={**os.environ, "PATH": f"{stand_in.parent}:/usr/bin:/bin", "DOCKER_CALLS": str(calls)})
                self.assertEqual(done.returncode, 1, done.stderr)
                self.assertFalse(output.exists())
                if image in DIGESTS:
                    # The stand-in fails the first container run, the one that reads the image's build record.
                    self.assertEqual(calls.read_text().splitlines()[0],
                                     f"run --rm --network none --read-only {image} cat /opt/eval/build-environment.json")
                else:
                    self.assertEqual(calls.read_text(), "")
                    self.assertTrue(done.stderr.startswith("Use an immutable content digest"), done.stderr)


class ReferenceCheckGateTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.temp = Path(temp.name).resolve()
        self.config = read_json(ROOT / "examples/demo-config.json")

    def write(self, name, value, lines=False):
        path = self.temp / name
        path.write_text("".join(json.dumps(v) + "\n" for v in value) if lines else json.dumps(value))
        return path

    def run_cli(self, *argv):
        err = io.StringIO()
        # Synthetic environment records: test_environment_integrity covers load_environment's own checks.
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err), \
                patch("lean_compression.cli.load_environment", read_json), \
                patch.dict("os.environ", {"LEAN_COMPRESSION_DEBUG": "0"}):
            try:
                cli.main([str(value) for value in argv])
            except SystemExit as stop:
                return stop.code, err.getvalue()
        return 0, err.getvalue()

    def test_docker_reference_checks_need_the_record_of_the_image_that_runs_lean(self):
        tasks = self.write("tasks.jsonl", [tiny_task()], lines=True)
        refs = self.write("refs.jsonl", [{"task_id": "tiny_1", "proof": "⟨h, h⟩"}], lines=True)
        record = {**read_json(ROOT / "configs/environment.local.json"), "runtime_backend": "docker"}
        validation = {**self.config["validation"], "isolation": "docker"}
        cases = (("same image", IMAGE, IMAGE, 0), ("other image", IMAGE, OTHER_IMAGE, 2),
                 ("record without image", None, IMAGE, 2), ("config without image", IMAGE, None, 2))
        for name, recorded, configured, expected in cases:
            env = self.write(f"env-{name}.json", {**record, **({"docker_image": recorded} if recorded else {})})
            config = self.write(f"config-{name}.json", {**self.config, "validation": {
                **validation, **({"docker_image": configured} if configured else {})}})
            for command, extra in (("verify-references", ["--references", refs]), ("baseline", [])):
                output = self.temp / f"{command}-{name}"
                with self.subTest(command=command, case=name), \
                        patch("lean_compression.runner.execute_engine", side_effect=stub_engine) as engine:
                    code, message = self.run_cli(command, "--tasks", tasks, *extra, "--environment", env,
                                                 "--config", config, "--output", output, "--engine", ENGINE)
                    self.assertEqual(code, expected, message)
                    self.assertEqual((engine.called, output.exists()), (expected == 0, expected == 0))
                    if expected:
                        self.assertEqual(message, f"Environment docker_image {recorded!r} differs from validation.docker_image "
                                                  f"{configured!r}: use the record collected inside the image that runs Lean\n")

    def test_reference_checks_refuse_any_other_image_reference_before_writing(self):
        tasks = self.write("tasks.jsonl", [tiny_task()], lines=True)
        refs = self.write("refs.jsonl", [{"task_id": "tiny_1", "proof": "⟨h, h⟩"}], lines=True)
        record = {**read_json(ROOT / "configs/environment.local.json"), "runtime_backend": "docker"}
        for index, image in enumerate((*DIGESTS, *NOT_DIGESTS)):
            # The record and the configuration name the same image, so only the digest rule can refuse it.
            env = self.write(f"env-{index}.json", {**record, "docker_image": image})
            config = self.write(f"config-{index}.json", {**self.config, "validation": {
                **self.config["validation"], "isolation": "docker", "docker_image": image}})
            for command, extra in (("verify-references", ["--references", refs]), ("baseline", [])):
                output = self.temp / f"{command}-{index}"
                with self.subTest(command=command, image=image), \
                        patch("lean_compression.runner.execute_engine", side_effect=stub_engine) as engine:
                    code, message = self.run_cli(command, "--tasks", tasks, *extra, "--environment", env,
                                                 "--config", config, "--output", output, "--engine", ENGINE)
                    if image in DIGESTS:
                        self.assertEqual((code, engine.called, (output / "policy.json").exists()), (0, True, True), message)
                    else:
                        self.assertEqual((code, engine.called, output.exists()), (2, False, False), message)
                        self.assertEqual(message, f"validation.docker_image {image!r} is not an immutable content digest "
                                                  "(sha256: and 64 lowercase hex digits, optionally after name@)\n")

    def test_index_closures_reads_only_a_verify_references_directory(self):
        checks = ROOT / "examples/demo-reference-check"
        records_only = self.temp / "records-only"
        records_only.mkdir()
        for record in checks.glob("*.json"):
            if record.name != "policy.json":
                shutil.copy(record, records_only / record.name)

        def index(references, output):
            return self.run_cli("index-closures", "--references", references,
                                "--environment", ROOT / "configs/environment.hashed.json",
                                "--config", ROOT / "examples/demo-config.json", "--output", output)
        not_directory = "--references must be an existing verify-references output directory"
        for path, expected in ((self.temp / "missing", not_directory), (ROOT / "examples/demo-reference.jsonl", not_directory),
                               (records_only, "--references has no policy.json, so it is not a verify-references output directory")):
            output = self.temp / f"index-{path.name}.json"
            with self.subTest(references=path.name):
                self.assertEqual(index(path, output), (2, f"{expected}: {path}\n"))
                self.assertFalse(output.exists())
        output = self.temp / "index.json"
        self.assertEqual(index(checks, output), (0, ""))
        self.assertTrue(output.exists())


if __name__ == "__main__":
    unittest.main()
