"""Resolve the Mathlib-declared Lean version and record immutable environment evidence."""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path

from .io import digest, file_hash, now, read_json, write_json


def command(args: list[str], cwd: Path | None = None) -> str:
    return subprocess.check_output(args, cwd=cwd, text=True, stderr=subprocess.PIPE).strip()


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
                for file in sorted(library.rglob("*")):
                    if file.is_file() and any(str(file).endswith(ext) for ext in
                                             (".olean", ".olean.private", ".olean.server", ".so", ".dylib")):
                        compiled_files.append({"path": f"{item['name']}/{file.relative_to(library)}",
                                               "sha256": file_hash(file)})
    if full_hash:
        core = prefix / "lib/lean"
        for file in sorted(core.rglob("*")):
            if file.is_file() and any(str(file).endswith(ext) for ext in
                                     (".olean", ".olean.private", ".olean.server", ".so", ".dylib")):
                compiled_files.append({"path": f"toolchain/{file.relative_to(core)}",
                                       "sha256": file_hash(file)})
    manifest = {
        "schema_version": 1, "lean_toolchain": toolchain, "mathlib_commit": commit,
        "dependency_lockfile": lock, "lockfile_hash": file_hash(lock_path),
        "lean_binary": str(lean_path), "lean_binary_hash": file_hash(lean_path),
        "toolchain_root": str(prefix), "lean_search_paths": search_paths,
        "packages": package_evidence, "source_files": source_files,
        "compiled_files": compiled_files,
        "source_environment_hash": digest(source_files) if full_hash else None,
        "compiled_environment_hash": digest(compiled_files) if full_hash else None,
        "hash_mode": "full" if full_hash else "development_metadata_only",
        "hardware": {"system": platform.system(), "machine": platform.machine(),
                     "cpu_count": os.cpu_count()},
    }
    manifest["environment_hash"] = digest(manifest)
    write_json(output, manifest)
    return manifest


def doctor() -> dict:
    docker = shutil.which("docker")
    docker_live = False
    if docker:
        try:
            docker_live = subprocess.run([docker, "info", "--format", "{{.ServerVersion}}"],
                                         capture_output=True, timeout=5).returncode == 0
        except subprocess.TimeoutExpired:
            pass
    return {
        "python": platform.python_version(), "system": platform.system(),
        "elan": shutil.which("elan"), "lean": shutil.which("lean"),
        "docker_installed": bool(docker), "docker_running": docker_live,
        "macos_sandbox_available": Path("/usr/bin/sandbox-exec").exists(),
        "openai_api_key_configured": bool(os.environ.get("OPENAI_API_KEY")),
        "note": "API credentials are never printed or inherited by Lean subprocesses.",
    }
