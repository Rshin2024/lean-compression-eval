"""Opt-in acceptance checks against an existing immutable Docker runtime.

No mock Docker calls, image builds, pulls, or dependency installation.  The
trusted test wrapper probes the same container that runs the real evaluator.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

from lean_compression.runner import execute_engine


ROOT = Path(__file__).resolve().parents[1]

# This is trusted acceptance-test instrumentation, not a candidate proof.  The
# production evaluator is copied byte-for-byte and receives an Init-only task.
WRAPPER = r'''import Lean

def main (args : List String) : IO UInt32 := do
  if args.length != 2 then
    IO.eprintln "acceptance wrapper expects request and output paths"
    return 2
  let probe ← IO.Process.output {
    cmd := "/usr/bin/python3"
    args := #["/engine/runtime_probe.py", "collect", "/job/runtime_probe.json"]
  }
  if probe.exitCode != 0 then
    IO.eprint probe.stderr
    return probe.exitCode
  let checked ← IO.Process.output {
    cmd := "lean"
    args := #["--run", "/engine/ProductionEvaluator.lean", args[0]!, args[1]!]
  }
  IO.print checked.stdout
  IO.eprint checked.stderr
  if checked.exitCode != 0 then
    return checked.exitCode
  let attached ← IO.Process.output {
    cmd := "/usr/bin/python3"
    args := #["/engine/runtime_probe.py", "attach", args[1]!, "/job/runtime_probe.json"]
  }
  IO.eprint attached.stderr
  return attached.exitCode
'''


PROBE = r'''import errno
import json
import os
from pathlib import Path
import socket
import sys

def first_text(paths):
    for name in paths:
        p = Path(name)
        if p.is_file():
            return {"path": str(p), "value": p.read_text().strip()}
    return None

def write_probe(folder):
    path = Path(folder) / (".lean-compression-acceptance-" + str(os.getpid()))
    try:
        with path.open("x") as out:
            out.write("temporary acceptance probe\n")
    except OSError as exc:
        return {"writable": False, "errno": exc.errno, "message": str(exc)}
    else:
        path.unlink()
        return {"writable": True, "errno": None}

def collect():
    root_mount = None
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        fields = line.split()
        if fields[4] == '/':
            root_mount = {"options": fields[5].split(','), "line": line}
            break
    interfaces = sorted(p.name for p in Path('/sys/class/net').iterdir())
    routes = Path('/proc/net/route').read_text().splitlines()[1:]
    default_routes = [line for line in routes if line.split()[1] == '00000000']
    # UDP connect performs a local route lookup; it sends no datagram and does
    # not depend on DNS or on an external server accepting connections.
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(1)
    try:
        sock.connect(('203.0.113.1', 9))
        route_lookup = {"succeeded": True, "errno": None}
    except OSError as exc:
        route_lookup = {"succeeded": False, "errno": exc.errno}
    finally:
        sock.close()
    cpu = first_text(['/sys/fs/cgroup/cpu.max'])
    if cpu:
        quota, period = cpu['value'].split()
        cpu.update(version=2, quota=None if quota == 'max' else int(quota), period=int(period))
    else:
        quota = first_text(['/sys/fs/cgroup/cpu/cpu.cfs_quota_us', '/sys/fs/cgroup/cpu,cpuacct/cpu.cfs_quota_us'])
        period = first_text(['/sys/fs/cgroup/cpu/cpu.cfs_period_us', '/sys/fs/cgroup/cpu,cpuacct/cpu.cfs_period_us'])
        cpu = None if not quota or not period else {
            "version": 1, "quota": int(quota['value']), "period": int(period['value']),
            "quota_path": quota['path'], "period_path": period['path']}
    memory = first_text(['/sys/fs/cgroup/memory.max', '/sys/fs/cgroup/memory/memory.limit_in_bytes'])
    swap = first_text(['/sys/fs/cgroup/memory.swap.max'])
    pids = first_text(['/sys/fs/cgroup/pids.max', '/sys/fs/cgroup/pids/pids.max'])
    status = {}
    for line in Path('/proc/self/status').read_text().splitlines():
        if ':' in line:
            key, value = line.split(':', 1)
            if key in ('CapEff', 'NoNewPrivs'):
                status[key] = value.strip()
    package = Path('/opt/eval/package')
    lean_sources, git_entries, traversal_errors = [], [], []
    compiled_count = 0
    seen = set()
    for directory, dirs, files in os.walk(package, followlinks=True,
                                          onerror=lambda e: traversal_errors.append(str(e))):
        st = os.stat(directory)
        identity = (st.st_dev, st.st_ino)
        if identity in seen:
            dirs[:] = []
            continue
        seen.add(identity)
        for name in [*dirs, *files]:
            p = Path(directory) / name
            if name == '.git':
                git_entries.append(str(p))
            if name.endswith('.lean'):
                lean_sources.append(str(p))
            if name.endswith('.olean'):
                compiled_count += 1
    return {
        "root_mount": root_mount,
        "writes": {p: write_probe(p) for p in ['/opt/eval/package', '/engine', '/job']},
        "network_interfaces": interfaces, "default_routes": default_routes,
        "external_route_lookup": route_lookup,
        "cpu": cpu, "memory": memory, "swap": swap, "pids": pids,
        "process_status": status,
        "package_audit": {"root": str(package), "exists": package.is_dir(),
                          "lean_source_count": len(lean_sources), "git_entry_count": len(git_entries),
                          "lean_source_examples": lean_sources[:20], "git_entry_examples": git_entries[:20],
                          "compiled_olean_count": compiled_count, "traversal_errors": traversal_errors},
    }

if sys.argv[1] == 'collect':
    Path(sys.argv[2]).write_text(json.dumps(collect(), indent=2) + '\n')
elif sys.argv[1] == 'attach':
    output, probe = Path(sys.argv[2]), Path(sys.argv[3])
    result = json.loads(output.read_text())
    result['runtime_acceptance_probe'] = json.loads(probe.read_text())
    output.write_text(json.dumps(result) + '\n')
else:
    raise SystemExit('expected collect or attach')
'''


class RuntimeWrapperSyntaxTests(unittest.TestCase):
    def test_embedded_probe_is_valid_python(self):
        compile(PROBE, "runtime_probe.py", "exec")

    def test_run_lean_is_valid_posix_shell(self):
        shell = shutil.which("sh")
        if not shell:
            self.skipTest("POSIX sh is unavailable")
        checked = subprocess.run([shell, "-n", str(ROOT / "runtime/run-lean")],
                                 capture_output=True, text=True, timeout=10)
        self.assertEqual(checked.returncode, 0, checked.stderr)


class DockerRuntimeAcceptanceTests(unittest.TestCase):
    """One actual evaluator run; all assertions examine observed container state."""

    MEMORY_MIB = 1024
    CPU_QUOTA = 0.5

    @classmethod
    def setUpClass(cls):
        configured = os.environ.get("LEAN_COMPRESSION_DOCKER_ENV")
        if not configured:
            raise unittest.SkipTest("set LEAN_COMPRESSION_DOCKER_ENV to an existing Docker manifest to opt in")
        manifest = Path(configured).expanduser()
        if not manifest.is_file():
            raise unittest.SkipTest(f"Docker environment manifest does not exist: {manifest}")
        docker = shutil.which("docker")
        if not docker:
            raise unittest.SkipTest("Docker CLI is unavailable; acceptance test never installs it")
        try:
            daemon = subprocess.run([docker, "info", "--format", "{{.ServerVersion}}"],
                                    capture_output=True, text=True, timeout=10)
        except subprocess.TimeoutExpired:
            raise unittest.SkipTest("Docker daemon availability check timed out")
        if daemon.returncode:
            raise unittest.SkipTest("Docker daemon is unavailable")
        cls.environment = json.loads(manifest.read_text())
        image = cls.environment.get("docker_image", "")
        if cls.environment.get("runtime_backend") != "docker":
            raise ValueError("the supplied manifest must describe runtime_backend=docker")
        if not re.search(r"(?:^|@)sha256:[0-9a-f]{64}$", image):
            raise ValueError("docker_image must be an immutable sha256 image ID or repository digest")
        try:
            available = subprocess.run([docker, "image", "inspect", image],
                                       capture_output=True, text=True, timeout=10)
        except subprocess.TimeoutExpired:
            raise unittest.SkipTest("local Docker image availability check timed out")
        if available.returncode:
            raise unittest.SkipTest("the manifest's immutable image is not available locally; no image is pulled")
        engine = ROOT / "lean/Evaluator.lean"
        if not engine.is_file():
            raise FileNotFoundError(engine)
        with tempfile.TemporaryDirectory(prefix="lean-runtime-acceptance-") as directory:
            fixture = Path(directory)
            (fixture / "Evaluator.lean").write_text(WRAPPER)
            (fixture / "runtime_probe.py").write_text(PROBE)
            shutil.copyfile(engine, fixture / "ProductionEvaluator.lean")
            request = {"imports": ["Init"], "target_name": "dockerAcceptanceTrue",
                       "binders": "", "proposition": "True", "proof": "True.intro",
                       "context": "", "forbidden_declarations": [],
                       "permitted_axioms": [], "max_heartbeats": 2_000_000}
            policy = {"isolation": "docker", "docker_image": image,
                      "memory_mib": cls.MEMORY_MIB, "cpu_quota": cls.CPU_QUOTA}
            cls.result = execute_engine(request, cls.environment, fixture / "Evaluator.lean",
                                        policy, mode="validate", wall_seconds=120)
        if cls.result.get("validity_status") != "success":
            raise AssertionError("real Init proof failed in Docker: " + json.dumps(cls.result, ensure_ascii=False))
        cls.probe = cls.result.get("runtime_acceptance_probe")
        if not isinstance(cls.probe, dict):
            raise AssertionError("container did not return actual runtime probe evidence")

    def test_real_init_proof_is_kernel_checked(self):
        self.assertEqual(self.result["validity_status"], "success")
        self.assertIs(self.result.get("kernel_replay"), True)
        self.assertEqual(self.result.get("axioms"), [])
        self.assertEqual(self.result.get("process_returncode"), 0)

    def test_network_and_filesystem_isolation(self):
        self.assertEqual(self.probe["network_interfaces"], ["lo"], self.probe)
        self.assertEqual(self.probe["default_routes"], [], self.probe)
        self.assertIs(self.probe["external_route_lookup"]["succeeded"], False, self.probe)
        self.assertIsNotNone(self.probe["root_mount"], self.probe)
        self.assertIn("ro", self.probe["root_mount"]["options"], self.probe)
        self.assertIs(self.probe["writes"]["/opt/eval/package"]["writable"], False, self.probe)
        self.assertIs(self.probe["writes"]["/engine"]["writable"], False, self.probe)
        self.assertIs(self.probe["writes"]["/job"]["writable"], True, self.probe)

    def test_actual_cgroup_limits_and_process_hardening(self):
        cpu = self.probe["cpu"]
        self.assertIsNotNone(cpu, "container exposes no readable CPU controller limit")
        self.assertIsNotNone(cpu["quota"], cpu)
        self.assertGreater(cpu["quota"], 0, cpu)
        self.assertAlmostEqual(cpu["quota"] / cpu["period"], self.CPU_QUOTA, places=6)
        memory = self.probe["memory"]
        self.assertIsNotNone(memory, "container exposes no readable memory controller limit")
        self.assertEqual(int(memory["value"]), self.MEMORY_MIB * 1024 * 1024)
        if self.probe["swap"] is not None:
            self.assertEqual(int(self.probe["swap"]["value"]), 0)
        pids = self.probe["pids"]
        self.assertIsNotNone(pids, "container exposes no readable PID controller limit")
        self.assertEqual(int(pids["value"]), 64)
        self.assertEqual(int(self.probe["process_status"]["CapEff"], 16), 0)
        self.assertEqual(self.probe["process_status"]["NoNewPrivs"], "1")

    def test_image_excludes_package_source_and_git(self):
        audit = self.probe["package_audit"]
        self.assertIs(audit["exists"], True, audit)
        self.assertEqual(audit["traversal_errors"], [], audit)
        self.assertGreater(audit["compiled_olean_count"], 0, audit)
        self.assertEqual(audit["lean_source_count"], 0, audit)
        self.assertEqual(audit["git_entry_count"], 0, audit)


if __name__ == "__main__":
    unittest.main()
