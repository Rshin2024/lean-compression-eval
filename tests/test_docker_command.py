"""Docker command, mounts and client environment, checked without a Docker daemon.

The real execute_engine() runs. Only the docker CLI (or the development sandbox launcher)
is replaced: a recorder keeps each command line and environment and plays the container in a
private directory standing in for its /job tmpfs, holding the request from its read-only mount.
The real runtime/run-lean then returns what the container left there on stdout, as in the image.
The opt-in acceptance test in test_docker_runtime.py observes an actual container.
"""
from __future__ import annotations

import errno
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from lean_compression.io import file_hash
from lean_compression.runner import docker_cli_env, execute_engine, sandbox_profile

try:
    from lean_runtime import pinned_runtime
except ModuleNotFoundError:  # python3 -m unittest tests.test_docker_command, run from the repository root
    from tests.lean_runtime import pinned_runtime


ROOT = Path(__file__).resolve().parents[1]
IMAGE = "sha256:" + "0" * 64
REAL_POPEN = subprocess.Popen
REAL_CHMOD = os.chmod
ENVIRONMENT = {"lean_binary": "/opt/lean/bin/lean", "toolchain_root": "/opt/lean",
               "lean_search_paths": ["/opt/lean/lib/lean"]}
REQUEST = {"imports": ["Init"], "target_name": "commandTest", "binders": "", "proposition": "True",
           "proof": "True.intro", "context": "", "forbidden_declarations": [], "permitted_axioms": [],
           "max_heartbeats": 200000}
SECRETS = {"OPENAI_API_KEY": "sk-unit-test-canary", "AWS_SECRET_ACCESS_KEY": "aws-unit-test-canary"}


class ReadBlocked(Exception):
    pass


def _read_blocked(signum, frame):
    raise ReadBlocked("reading the container result blocked")


def run_lean(root: Path, job: Path, cgroup_files: dict, lean: str) -> Path:
    """runtime/run-lean with /opt/eval, /sys/fs/cgroup and /job moved under root and job, running the shell `lean`."""
    (root / "bin").mkdir(parents=True)
    (root / "bin/lean").write_text("#!/bin/sh\n" + lean + "\n")
    (root / "bin/lean").chmod(0o755)
    (root / "lean-binary").write_text(f"{root}/bin/lean\n")
    (root / "lean-path").write_text(f"{root}/lib\n")
    for name, content in cgroup_files.items():
        (root / "cgroup" / name).parent.mkdir(parents=True, exist_ok=True)
        (root / "cgroup" / name).write_bytes(content)
    moved = {"/opt/eval/": f"{root}/", "/sys/fs/cgroup": f"{root}/cgroup", "/job": str(job)}
    text = (ROOT / "runtime/run-lean").read_text()
    script = root / "run-lean"
    script.write_text(re.sub("|".join(map(re.escape, moved)), lambda found: moved[found.group()], text))
    return script


class FakeDocker:
    """Records docker CLI calls and plays the container in a private /job, returning its output through run-lean."""

    def __init__(self, container="success", *, rm_error=None, rm_exit=(0, ""), secret=None, containers=None):
        self.container, self.rm_error, self.rm_exit, self.secret, self.calls = container, rm_error, rm_exit, secret, []
        self.writer, self.containers = None, containers

    def popen(self, cmd, *args, **kwargs):
        # env=None would mean the whole parent environment, credentials included.
        call = {"cmd": list(cmd), "env": kwargs.get("env") and dict(kwargs["env"]), "cwd": str(kwargs.get("cwd"))}
        self.calls.append(call)
        if cmd[:3] == ["docker", "rm", "-f"]:
            if self.rm_error:
                raise self.rm_error
            code, message = self.rm_exit
            return REAL_POPEN(["/bin/sh", "-c", 'printf "%s\\n" "$1" >&2; exit "$2"', "docker", message, str(code)],
                              *args, **kwargs)
        if cmd[0] == "docker":
            mounts = {}
            for flag, value in zip(cmd, cmd[1:]):
                if flag == "--mount":
                    fields = dict(part.partition("=")[::2] for part in value.split(","))
                    mounts[fields["target"]] = Path(fields["source"])
            host = mounts["/job/request.json"].parent
            # The container's /job is its own tmpfs, never a host directory; it holds the read-only request.
            container = Path(tempfile.mkdtemp(dir=self.containers))
            job = container / "job"
            job.mkdir()
            shutil.copyfile(mounts["/job/request.json"], job / "request.json")
            executed = mounts["/engine"] / Path(cmd[cmd.index("/opt/eval/run-lean") + 1]).relative_to("/engine")
            call["engine_sha256"] = hashlib.sha256(executed.read_bytes()).hexdigest() if executed.is_file() else None
        else:
            host = job = Path(cmd[-1]).parent
        call["job"] = {"path": str(host), "mode": stat.S_IMODE(host.stat().st_mode),
                       "parent_mode": stat.S_IMODE(host.parent.stat().st_mode),
                       "entries": {p.name: stat.S_IMODE(p.stat().st_mode) for p in host.iterdir()}}
        request = json.loads((job / "request.json").read_text())
        code, lean = 0, None
        if self.container == "success":
            (job / "result.json").write_text(json.dumps({"validity_status": "success", "metric_status": "not_requested",
                                                         "mode": request["mode"]}))
        elif self.container == "hang":
            lean = "exec sleep 60"
        elif self.container == "hang_and_write":
            # A container outliving its killed CLI keeps writing into its own /job.
            loop = 'i=0; while [ $i -lt 200 ]; do : > "$1/out$i"; i=$((i+1)); sleep 0.005; done'
            self.writer = REAL_POPEN(["/bin/sh", "-c", loop, "writer", str(job)], start_new_session=True)
            lean = "exec sleep 60"
        elif self.container in ("unowned_directory", "locked_directories", "link"):
            # Directories that hold files the host must delete: one emulates a directory the host user cannot open;
            # the host user's own lack owner bits; a link points out of scratch.
            if self.container == "unowned_directory":
                (job / "sub").mkdir()
                (job / "sub" / "f").write_text("x")
                REAL_CHMOD(job / "sub", 0o555)
            elif self.container == "locked_directories":
                (job / "kept" / "locked").mkdir(parents=True)
                (job / "kept" / "locked" / "f").write_text("x")
                REAL_CHMOD(job / "kept" / "locked", 0)
                REAL_CHMOD(job / "kept", 0o1500)
            else:
                os.symlink(self.secret, job / "escape")
            (job / "result.json").write_text(json.dumps({"validity_status": "success", "metric_status": "not_requested",
                                                         "mode": request["mode"]}))
        elif self.container == "fifo_peak":
            os.mkfifo(job / "peak_memory_bytes")
            code = 137
        elif self.container == "symlink":
            os.symlink(self.secret, job / "result.json")
        elif self.container == "fifo":
            os.mkfifo(job / "result.json")
        else:
            code = int(self.container)
        if cmd[0] != "docker":
            return REAL_POPEN(["/bin/sh", "-c", lean or f"exit {code}"], *args, **kwargs)
        script = run_lean(container, job, {"memory.peak": b"2097152\n"} if self.container == "success" else {},
                          lean or f"exit {code}")
        arguments = [str(job) + value[len("/job"):] if value.startswith("/job/") else value
                     for value in cmd[cmd.index("/opt/eval/run-lean") + 1:]]
        return REAL_POPEN(["/bin/sh", str(script), *arguments], *args, **kwargs)


class DockerClientEnvironmentTests(unittest.TestCase):
    def test_only_client_endpoint_and_configuration_are_passed(self):
        parent = {"PATH": "/opt/docker/bin:/usr/bin:/bin", "HOME": "/home/runner", "XDG_RUNTIME_DIR": "/run/user/1000",
                  "DOCKER_HOST": "unix:///run/user/1000/docker.sock", "DOCKER_CONTEXT": "rootless",
                  "DOCKER_CONFIG": "/home/runner/.docker-alt", "DOCKER_CERT_PATH": "/home/runner/certs",
                  "DOCKER_TLS_VERIFY": "1", "DOCKER_TLS": "", "DOCKER_API_VERSION": "1.43",
                  "SSH_AUTH_SOCK": "/tmp/agent.sock", "LEAN_PATH": "/opt/lean/lib", **SECRETS}
        with patch.dict(os.environ, parent, clear=True):
            env = docker_cli_env()
        self.assertEqual(env, {name: parent[name] for name in (
            "PATH", "HOME", "XDG_RUNTIME_DIR", "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG",
            "DOCKER_CERT_PATH", "DOCKER_TLS_VERIFY", "DOCKER_API_VERSION")})

    def test_ssh_agent_is_passed_only_to_reach_an_ssh_daemon(self):
        remote = {"PATH": "/usr/bin:/bin", "DOCKER_HOST": "ssh://builder@remote", "SSH_AUTH_SOCK": "/tmp/agent.sock"}
        with patch.dict(os.environ, remote, clear=True):
            self.assertEqual(docker_cli_env(), remote)
        with patch.dict(os.environ, {"SSH_AUTH_SOCK": "/tmp/agent.sock"}, clear=True):
            self.assertEqual(docker_cli_env(), {"PATH": "/usr/bin:/bin"})


class DockerCommandTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.temp = Path(temp.name).resolve()
        # Attempts make their scratch directories here, so each test sees exactly what an attempt leaves.
        self.scratch_root = self.temp / "scratch"
        self.scratch_root.mkdir()
        tempdir = patch.object(tempfile, "tempdir", str(self.scratch_root))
        tempdir.start()
        self.addCleanup(tempdir.stop)
        # Stand-ins for each container's own filesystem, which the host never sees.
        self.containers = self.temp / "containers"
        self.containers.mkdir()
        self.engines = self.temp / "engines"
        self.engines.mkdir()
        self.engine = self.engines / "Candidate-Engine.lean"
        self.engine.write_text("-- selected engine\n")
        (self.engines / "Evaluator.lean").write_text("-- different sibling engine\n")
        self.policy = {"isolation": "docker", "docker_image": IMAGE, "memory_mib": 1024, "cpu_quota": 0.5}

    def execute(self, docker, *, mode="validate", wall_seconds=30, policy=None):
        docker.containers = docker.containers or self.containers
        with patch("subprocess.Popen", docker.popen), \
             patch.dict(os.environ, {**SECRETS, "DOCKER_CONTEXT": "unit-test-context"}):
            self.client_env = docker_cli_env()
            return execute_engine(REQUEST, ENVIRONMENT, self.engine, policy or self.policy,
                                  mode=mode, wall_seconds=wall_seconds)

    def native(self, docker):
        """Development sandbox: Lean runs as the host user and writes into the scratch directory itself."""
        with patch("platform.system", return_value="Darwin"):
            return self.execute(docker, policy={"isolation": "macos_sandbox"})

    def test_exact_docker_command_runs_the_selected_engine(self):
        docker = FakeDocker()
        result = self.execute(docker)
        self.assertEqual(result["validity_status"], "success", result)
        self.assertEqual(len(docker.calls), 1, "a clean exit needs no forced removal")
        call = docker.calls[0]
        name = call["cmd"][4]
        self.assertRegex(name, r"^lean-compression-\d+-\d+$")
        self.assertEqual(call["cmd"], [
            "docker", "run", "--rm", "--name", name, "--network", "none", "--read-only",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--pids-limit", "64",
            "--memory", "1024m", "--memory-swap", "1024m",
            "--ulimit", "fsize=268435456:268435456", "--ulimit", "cpu=31:32",
            "--log-driver", "none", "--cpus", "0.5",
            "--tmpfs", "/job:rw,noexec,nosuid,size=320m",
            "--mount", f"type=bind,source={call['cwd']}/job/request.json,target=/job/request.json,readonly",
            "--mount", f"type=bind,source={self.engines},target=/engine,readonly",
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=128m", IMAGE,
            "/opt/eval/run-lean", "/engine/Candidate-Engine.lean", "/job/request.json", "/job/result.json"])
        # The container executes exactly the file whose hash evaluation records, not a sibling.
        self.assertEqual(call["engine_sha256"], file_hash(self.engine))
        self.assertNotEqual(call["engine_sha256"], file_hash(self.engines / "Evaluator.lean"))

    def test_job_mount_is_private_on_the_host_and_usable_by_any_container_uid(self):
        docker = FakeDocker()
        result = self.execute(docker)
        call = docker.calls[0]
        job = call["job"]
        self.assertEqual(Path(job["path"]), Path(call["cwd"]) / "job")
        self.assertEqual(job["parent_mode"], 0o700)
        # Only the request is mounted, read-only and world-readable for capability-less or remapped container uids.
        # The container's /job is its own tmpfs, so nothing it writes reaches the host.
        self.assertEqual(job["entries"], {"request.json": 0o644}, "host logs stay outside the mount")
        mounts = [value for flag, value in zip(call["cmd"], call["cmd"][1:]) if flag in ("--mount", "--tmpfs")]
        self.assertEqual([value for value in mounts if "/job" in value], [
            "/job:rw,noexec,nosuid,size=320m", f"type=bind,source={job['path']}/request.json,target=/job/request.json,readonly"])
        self.assertTrue(all(value.endswith(",readonly") for value in mounts if value.startswith("type=bind")))
        self.assertEqual((result["resources"]["peak_memory_mib"], result["resources"]["stdout_bytes"]), (2.0, 0))
        self.assertIsNone(result["resources"]["peak_memory_reason"])
        self.assertFalse(Path(call["cwd"]).exists(), "scratch is removed")

    def test_run_lean_keeps_results_readable_by_the_host_user(self):
        script = (ROOT / "runtime/run-lean").read_text().splitlines()
        self.assertIn("umask 022", script)
        self.assertLess(script.index("umask 022"), next(i for i, line in enumerate(script) if "--run" in line))

    def test_run_lean_returns_the_container_output_on_stdout_before_exiting(self):
        script = (ROOT / "runtime/run-lean").read_text()
        lines = script.splitlines()
        # Only container init stops what Lean left running; on a host, -1 would reach every process of the user.
        guard = lines.index('if [ "$$" = 1 ]; then')
        self.assertEqual(lines[guard + 1].strip(), "kill -KILL -1 2>/dev/null")
        returned = lines.index("printf 'lean-compression-output v1%s\\n' \"$sizes\"")
        self.assertLess(next(i for i, line in enumerate(lines) if "--run" in line), guard)
        self.assertLess(guard, returned)
        self.assertEqual(lines[returned + 1:], ['exit "$lean_exit"'])
        # Run it on the host with kill disabled: what a fake Lean printed comes first, then its result and the trailer.
        container = self.temp / "runtime"
        job = container / "job"
        job.mkdir(parents=True)
        lean = 'printf "printed by Lean"\nprintf \'{"validity_status": "success"}\' > "$4"\nexit 3'
        checked = subprocess.run(["/bin/sh", "-c", "kill() { :; }\n" + run_lean(container, job, {}, lean).read_text(),
                                  "run-lean", "/engine/Evaluator.lean", f"{job}/request.json", f"{job}/result.json"],
                                 capture_output=True, timeout=30)
        self.assertEqual(checked.returncode, 3, checked.stderr)
        self.assertEqual(checked.stdout, b'printed by Lean{"validity_status": "success"}lean-compression-output v1 30 - -\n')

    def test_docker_cli_receives_client_environment_only(self):
        docker = FakeDocker("137")
        self.execute(docker)
        self.assertEqual([call["cmd"][:2] for call in docker.calls], [["docker", "run"], ["docker", "rm"]])
        for call in docker.calls:
            self.assertEqual(call["env"], self.client_env)
            self.assertEqual(call["env"]["DOCKER_CONTEXT"], "unit-test-context")
            self.assertFalse(set(SECRETS) & set(call["env"]), call["env"])
            self.assertNotIn("canary", json.dumps(call["env"]))

    def test_cpu_time_backstop_never_binds_before_the_wall_limit(self):
        docker = FakeDocker()
        result = self.execute(docker, policy={**self.policy, "cpu_quota": 2}, wall_seconds=30)
        cmd = docker.calls[0]["cmd"]
        self.assertEqual(cmd[cmd.index("cpu=61:62") - 1], "--ulimit")
        self.assertEqual(cmd[cmd.index("--cpus") + 1], "2")
        self.assertEqual(result["resources"]["cpu_seconds_limit"], 61)

    def test_mutable_image_or_unbounded_cpu_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "immutable"):
            self.execute(FakeDocker(), policy={**self.policy, "docker_image": "ubuntu:24.04"})
        for quota in (0, None, "1"):
            with self.subTest(cpu_quota=quota), self.assertRaisesRegex(ValueError, "cpu_quota"):
                self.execute(FakeDocker(), policy={**self.policy, "cpu_quota": quota})
        self.assertEqual(list(self.scratch_root.iterdir()), [], "a rejected policy leaves no scratch")

    def test_timeouts_are_classified_and_the_container_is_removed(self):
        for mode, status in (("validate", "verification_timeout"), ("extract", "extractor_timeout")):
            with self.subTest(mode=mode):
                docker = FakeDocker("hang")
                result = self.execute(docker, mode=mode, wall_seconds=0.2)
                self.assertEqual(result["validity_status"], status, result)
                self.assertEqual(result["metric_status"], "unavailable")
                self.assertGreaterEqual(result["resources"]["elapsed_seconds"], 0.2)
                self.assertEqual(docker.calls[1]["cmd"], ["docker", "rm", "-f", docker.calls[0]["cmd"][4]])
                self.assertEqual(docker.calls[1]["env"], self.client_env)

    def test_unconfirmed_container_removal_is_an_infrastructure_outcome(self):
        stuck = ("Error response from daemon: cannot remove container: could not kill: "
                 "tried to kill container, but did not receive an exit event")
        unreachable = "Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the docker daemon running?"
        failures = [(dict(rm_error=error), str(error)) for error in
                    (subprocess.TimeoutExpired(["docker", "rm", "-f"], 10), FileNotFoundError(2, "No such file", "docker"))]
        failures += [(dict(rm_exit=(code, message)), f"docker rm -f exited {code}: {message}")
                     for code, message in ((1, stuck), (1, unreachable), (125, ""))]
        # fifo_peak: what a container that may still run wrote is not read before the removal is reported.
        for container in ("hang", "137", "fifo_peak"):
            for failure, error in failures:
                with self.subTest(container=container, error=error):
                    docker = FakeDocker(container, **failure)
                    result = self.execute(docker, wall_seconds=0.2 if container == "hang" else 30)
                    self.assertEqual(result["validity_status"], "infrastructure_error", result)
                    self.assertEqual(result["failure_reason"], "container_cleanup_unconfirmed")
                    self.assertEqual(result["error"], error)
                    self.assertEqual(result["metric_status"], "unavailable")
                    # The container may still be running, so its bind source is kept rather than deleted under it.
                    scratch = Path(docker.calls[0]["cwd"])
                    self.assertEqual(result["resources"]["scratch_retained"], str(scratch))
                    self.assertEqual(result["resources"]["scratch_retained_reason"], "container_cleanup_unconfirmed")
                    self.assertTrue((scratch / "job" / "request.json").is_file())

    def test_container_reported_missing_counts_as_removed(self):
        # Older Docker clients and Podman exit non-zero for a container that is already gone.
        messages = ("Error response from daemon: No such container: lean-compression-1-2",
                    'Error: no container with name or ID "lean-compression-1-2" found: no such container')
        for container, status in (("hang", "verification_timeout"), ("137", "verification_resource_limit")):
            for message in messages:
                with self.subTest(container=container, message=message):
                    docker = FakeDocker(container, rm_exit=(1, message))
                    result = self.execute(docker, wall_seconds=0.2 if container == "hang" else 30)
                    self.assertEqual(result["validity_status"], status, result)
                    self.assertNotIn("scratch_retained", result["resources"])
                    self.assertEqual(list(self.scratch_root.iterdir()), [])

    def test_bind_source_is_kept_while_an_unremoved_container_writes(self):
        docker = FakeDocker("hang_and_write", rm_exit=(1, "Error response from daemon: could not kill container"))
        try:
            result = self.execute(docker, wall_seconds=0.2)
        finally:
            if docker.writer:
                docker.writer.wait(timeout=60)
        self.assertEqual(result["failure_reason"], "container_cleanup_unconfirmed", result)
        scratch = Path(docker.calls[0]["cwd"])
        self.assertEqual(result["resources"]["scratch_retained"], str(scratch))
        # The stand-in container wrote on into its own /job; none of it reached the host.
        self.assertEqual(len(list(self.containers.glob("*/job/out*"))), 200)
        self.assertEqual(sorted(path.relative_to(scratch).as_posix() for path in scratch.rglob("*")),
                         ["job", "job/request.json", "stderr.log", "stdout.log"])

    @unittest.skipIf(os.geteuid() == 0, "root bypasses the directory permissions the host user meets")
    def test_unremovable_container_output_never_masks_the_outcome(self):
        # Only the development sandbox writes into the scratch directory; a container's output stays in its tmpfs.
        def not_owner(path, *args, **kwargs):
            # A directory whose mode the host user cannot change.
            if Path(os.fsdecode(path)).parts[-2:] == ("job", "sub"):
                raise PermissionError(errno.EPERM, "Operation not permitted", os.fsdecode(path))
            return REAL_CHMOD(path, *args, **kwargs)
        docker = FakeDocker("unowned_directory")
        with patch("os.chmod", not_owner):
            result = self.native(docker)
        self.assertEqual(result["validity_status"], "success", result)
        scratch = Path(docker.calls[0]["cwd"])
        self.assertEqual(result["resources"]["scratch_retained"], str(scratch))
        self.assertIn("Permission denied", result["resources"]["scratch_retained_reason"])
        self.assertEqual(sorted(path.relative_to(scratch).as_posix() for path in scratch.rglob("*")),
                         ["job", "job/sub", "job/sub/f"], "everything else is removed")

    def test_host_owned_directories_without_owner_bits_are_removed(self):
        result = self.native(FakeDocker("locked_directories"))
        self.assertEqual(result["validity_status"], "success", result)
        self.assertNotIn("scratch_retained", result["resources"])
        self.assertEqual(list(self.scratch_root.iterdir()), [])

    def test_cleanup_failure_of_any_kind_never_masks_the_outcome(self):
        # Python 3.11 deletes recursively: a deep enough tree from the container raises RecursionError.
        with patch("shutil.rmtree", side_effect=RecursionError("maximum recursion depth exceeded")):
            result = self.execute(FakeDocker())
        self.assertEqual(result["validity_status"], "success", result)
        self.assertEqual(result["resources"]["scratch_retained_reason"], "maximum recursion depth exceeded")
        self.assertTrue(Path(result["resources"]["scratch_retained"], "job", "request.json").is_file())

    def test_cleanup_never_follows_links_out_of_scratch(self):
        outside = self.temp / "outside"
        outside.mkdir()
        outside.chmod(0o755)
        (outside / "kept").write_text("host data")
        result = self.native(FakeDocker("link", secret=outside))
        self.assertEqual(result["validity_status"], "success", result)
        self.assertEqual(stat.S_IMODE(outside.stat().st_mode), 0o755)
        self.assertEqual((outside / "kept").read_text(), "host data")
        self.assertEqual(list(self.scratch_root.iterdir()), [])

    def test_signalled_and_failed_containers(self):
        cases = (("137", "validate", "verification_resource_limit"), ("152", "validate", "verification_resource_limit"),
                 ("153", "extract", "extractor_resource_limit"), ("1", "validate", "infrastructure_error"))
        for code, mode, status in cases:
            with self.subTest(code=code, mode=mode):
                docker = FakeDocker(code)
                result = self.execute(docker, mode=mode)
                self.assertEqual(result["validity_status"], status, result)
                self.assertEqual(result["process_returncode"], int(code))
                self.assertEqual(docker.calls[-1]["cmd"][:3], ["docker", "rm", "-f"])

    def test_container_cannot_redirect_or_block_result_reads(self):
        secret = self.temp / "host-secret.json"
        secret.write_text(json.dumps({"validity_status": "success", "token": "host-secret-canary"}))
        result = self.execute(FakeDocker("symlink", secret=secret))
        self.assertEqual(result["validity_status"], "infrastructure_error", result)
        self.assertNotIn("host-secret-canary", json.dumps(result))
        previous = signal.signal(signal.SIGALRM, _read_blocked)
        signal.alarm(20)
        try:
            result = self.execute(FakeDocker("fifo"))
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, previous)
        self.assertEqual(result["validity_status"], "infrastructure_error", result)
        self.assertIn("not a regular file", result["error"])


class NativeSandboxTests(unittest.TestCase):
    def test_profile_denies_network_and_confines_writes_to_scratch(self):
        with tempfile.TemporaryDirectory() as directory:
            scratch = str(Path(directory).resolve())
            lines = sandbox_profile(["/opt/lean"], scratch).splitlines()
        self.assertIn("(deny network*)", lines)
        writes = [line for line in lines if line.startswith("(allow file-write")]
        self.assertEqual(writes, [f"(allow file-write* (subpath {json.dumps(scratch, ensure_ascii=False)}))",
                                  '(allow file-write* (literal "/dev/null"))'])
        self.assertLess(lines.index("(deny file-write*)"), lines.index(writes[0]))

    def test_native_lean_gets_only_the_scrubbed_environment(self):
        docker = FakeDocker()
        with tempfile.TemporaryDirectory() as directory:
            engine = Path(directory).resolve() / "Evaluator.lean"
            engine.write_text("-- engine\n")
            with patch("platform.system", return_value="Darwin"), patch("subprocess.Popen", docker.popen), \
                 patch.dict(os.environ, SECRETS):
                result = execute_engine(REQUEST, ENVIRONMENT, engine, {"isolation": "macos_sandbox"},
                                        mode="validate", wall_seconds=30)
        self.assertEqual(result["validity_status"], "success", result)
        call = docker.calls[0]
        scratch = call["cwd"]
        self.assertEqual(call["cmd"], ["/usr/bin/sandbox-exec", "-f", f"{scratch}/sandbox.sb", "/opt/lean/bin/lean",
                                       "--run", str(engine), f"{scratch}/job/request.json", f"{scratch}/job/result.json"])
        self.assertEqual(call["env"], {"PATH": "/opt/lean/bin:/usr/bin:/bin", "LANG": "en_US.UTF-8",
                                       "LC_ALL": "en_US.UTF-8", "TMPDIR": scratch, "LEAN_PATH": "/opt/lean/lib/lean"})
        self.assertIs(result["resources"]["credentials_inherited"], False)

    @unittest.skipUnless(platform.system() == "Darwin" and Path("/usr/bin/sandbox-exec").exists(),
                         "the development sandbox is macOS-only")
    def test_real_sandboxed_lean_checks_init_proofs(self):
        environment, _ = pinned_runtime()
        statuses = {proof: execute_engine({**REQUEST, "proof": proof}, environment, ROOT / "lean/Evaluator.lean",
                                          {"isolation": "macos_sandbox"}, mode="validate",
                                          wall_seconds=60)["validity_status"]
                    for proof in ("True.intro", "by sorry")}
        self.assertEqual(statuses, {"True.intro": "success", "by sorry": "disallowed_axiom"})


if __name__ == "__main__":
    unittest.main()
