"""Resolve the Mathlib-declared Lean version and record immutable environment evidence."""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from .io import digest, file_hash, now, read_json, write_json

# Schema 2 hashes every library file Lean may load, including the .ir/.ir.sig IR of each import.
SCHEMA_VERSIONS = (1, 2)
# Lake build traces/hashes, language-server-only .ilean indexes and static link archives:
# `lean --run` never opens them, so they would only make the identity rebuild-sensitive.
UNREAD_SUFFIXES = (".trace", ".hash", ".ilean", ".a")
REQUIRED_FIELDS = ("schema_version", "hash_mode", "lean_toolchain", "mathlib_commit", "lean_binary",
                   "lean_binary_hash", "toolchain_root", "lean_search_paths", "packages", "compiled_files")


def command(args: list[str], cwd: Path | None = None) -> str:
    return subprocess.check_output(args, cwd=cwd, text=True, stderr=subprocess.PIPE).strip()


def library_files(root: Path, label: str) -> list[dict]:
    """Hash every regular file below a library root except artifacts Lean never reads."""
    files = []
    for file in sorted(root.rglob("*")):
        if file.is_symlink() and file.is_dir():
            raise ValueError(f"Linked directory in a Lean library root cannot be hashed: {file}")
        if file.is_file() and not file.name.endswith(UNREAD_SUFFIXES):
            files.append({"path": f"{label}/{file.relative_to(root)}", "sha256": file_hash(file)})
    return files


def prepare_environment(package_root: Path, output: Path, *, full_hash: bool = True) -> dict:
    package_root = package_root.resolve()
    lock_path = package_root / "lake-manifest.json"
    lock = read_json(lock_path)
    packages_dir = package_root / lock.get("packagesDir", ".lake/packages")
    mathlib = packages_dir / "mathlib"
    toolchain = (mathlib / "lean-toolchain").read_text().strip()
    package_toolchain = (package_root / "lean-toolchain").read_text().strip()
    if toolchain != package_toolchain:
        raise ValueError("Package Lean toolchain differs from Mathlib's declared toolchain")
    commit = command(["git", "rev-parse", "HEAD"], mathlib)
    mathlib_pin = next(p for p in lock["packages"] if p["name"] == "mathlib")
    if commit != mathlib_pin["rev"]:
        raise ValueError("Mathlib checkout does not match the dependency lockfile")
    lean = subprocess.check_output(["elan", "which", "lean"], text=True,
                                   env={**os.environ, "ELAN_TOOLCHAIN": toolchain}).strip()
    lean_path = Path(lean).resolve()
    prefix = lean_path.parent.parent
    search_paths = []
    package_evidence = []
    compiled_files = []
    source_files = []
    for item in lock["packages"]:
        location = packages_dir / item["name"]
        if not location.is_dir():
            raise ValueError(f"Missing locked package: {item['name']}")
        if item.get("type") == "git":
            actual = command(["git", "rev-parse", "HEAD"], location)
            dirty = command(["git", "status", "--porcelain", "--untracked-files=no"], location)
            if actual != item["rev"] or dirty:
                raise ValueError(f"Package {item['name']} is dirty or differs from its locked revision")
        library = location / ".lake/build/lib/lean"
        if library.exists():
            search_paths.append(str(library.resolve()))
        package_evidence.append({"name": item["name"], "rev": item.get("rev"),
                                 "path": str(location), "library_path": str(library)})
        if full_hash:
            for file in sorted(location.rglob("*.lean")):
                if ".lake" not in file.relative_to(location).parts:
                    source_files.append({"path": f"{item['name']}/{file.relative_to(location)}",
                                         "sha256": file_hash(file)})
            if library.exists():
                compiled_files.extend(library_files(library, item["name"]))
    # A task-specific library (such as the pinned OAI corpus) can belong to the
    # root Lake package rather than a dependency. Its compiled modules are just
    # as executable as dependency modules and must be bound into the evidence.
    root_library = package_root / ".lake/build/lib/lean"
    if root_library.exists():
        search_paths.append(str(root_library.resolve()))
        package_evidence.append({"name": "__root__", "rev": None,
                                 "path": str(package_root), "library_path": str(root_library)})
        if full_hash:
            compiled_files.extend(library_files(root_library, "__root__"))
    if full_hash:
        for file in sorted(package_root.rglob("*.lean")):
            if ".lake" not in file.relative_to(package_root).parts:
                source_files.append({"path": f"__root__/{file.relative_to(package_root)}",
                                     "sha256": file_hash(file)})
    if full_hash:
        compiled_files.extend(library_files(prefix / "lib/lean", "toolchain"))
    manifest = {
        "schema_version": 2, "lean_toolchain": toolchain, "mathlib_commit": commit,
        "dependency_lockfile": lock, "lockfile_hash": file_hash(lock_path),
        "lean_binary": str(lean_path), "lean_binary_hash": file_hash(lean_path),
        "toolchain_root": str(prefix), "lean_search_paths": search_paths,
        "packages": package_evidence, "source_files": source_files,
        "compiled_files": compiled_files,
        "source_environment_hash": digest(source_files) if full_hash else None,
        "compiled_environment_hash": digest(compiled_files) if full_hash else None,
        "compiled_excluded_suffixes": list(UNREAD_SUFFIXES) if full_hash else None,
        "hash_mode": "full" if full_hash else "development_metadata_only",
        "hardware": {"system": platform.system(), "machine": platform.machine(),
                     "cpu_count": os.cpu_count()},
    }
    manifest["environment_hash"] = digest(manifest)
    write_json(output, manifest)
    return manifest


def verify_environment(env: dict) -> dict:
    """Reject edited or stale records; for native runs, re-check what Lean will execute."""
    if not isinstance(env, dict):
        raise ValueError(f"Environment record must be a JSON object, not {type(env).__name__}")
    unhashed = {k: v for k, v in env.items() if k != "environment_hash"}
    if env.get("environment_hash") != digest(unhashed):
        raise ValueError("Environment record does not match its environment_hash; regenerate it instead of editing it")
    missing = [key for key in REQUIRED_FIELDS if key not in env]
    if missing:
        raise ValueError(f"Environment record lacks {', '.join(missing)}")
    if env["schema_version"] not in SCHEMA_VERSIONS:
        raise ValueError(f"Unsupported environment record schema_version: {env['schema_version']}")
    if env.get("runtime_backend") == "docker":
        return env  # The content-addressed image fixes the toolchain and libraries.
    core = Path(env["toolchain_root"]) / "lib/lean"
    runtime = [(Path(env["lean_binary"]), env["lean_binary_hash"])]
    runtime += [(core / item["path"].removeprefix("toolchain/"), item["sha256"]) for item in env["compiled_files"]
                if item["path"].startswith("toolchain/") and item["path"].endswith((".so", ".dylib"))]
    for path, expected in runtime:
        if not path.is_file() or file_hash(path) != expected:
            raise ValueError(f"Lean runtime file differs from the environment record: {path}")
    for path in env["lean_search_paths"]:
        if not Path(path).is_dir():
            raise ValueError(f"Lean library directory from the environment record is missing: {path}")
    for item in env["packages"]:
        if item.get("rev"):
            try:
                head = command(["git", "rev-parse", "HEAD"], Path(item["path"]))
            except (OSError, subprocess.CalledProcessError) as error:
                raise ValueError(f"Cannot read the git revision of package {item['name']} at {item['path']}") from error
            if head != item["rev"]:
                raise ValueError(f"Package {item['name']} no longer matches its recorded revision: {item['path']}")
    return env


def load_environment(path: Path) -> dict:
    """Read an environment record and verify it once, before any Lean process runs."""
    env = read_json(path)
    if not isinstance(env, dict):
        raise ValueError(f"Environment record {path} must be a JSON object, not {type(env).__name__}")
    verify_environment(env)
    if env["schema_version"] == 1 and env["hash_mode"] == "full" and env.get("runtime_backend") != "docker":
        print(f"Note: {path} is a schema 1 record whose compiled hash omits Lean IR (.ir/.ir.sig); "
              "rerun prepare-environment for complete coverage.", file=sys.stderr)
    return env


def doctor() -> dict:
    docker = shutil.which("docker")
    docker_live = False
    if docker:
        from .runner import docker_cli_env
        try:
            docker_live = subprocess.run([docker, "info", "--format", "{{.ServerVersion}}"], env=docker_cli_env(),
                                         capture_output=True, timeout=5).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            pass
    return {
        "python": platform.python_version(), "system": platform.system(),
        "elan": shutil.which("elan"), "lean": shutil.which("lean"),
        "docker_installed": bool(docker), "docker_running": docker_live,
        "macos_sandbox_available": Path("/usr/bin/sandbox-exec").exists(),
        "openai_api_key_configured": bool(os.environ.get("OPENAI_API_KEY")),
        "anthropic_api_key_configured": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "note": "API credentials are never printed or inherited by Lean subprocesses.",
    }
