"""Locate the pinned Lean runtime for the real-Lean fixtures.

LEAN_COMPRESSION_ENVIRONMENT names an environment manifest written by
`prepare-environment`; relative paths resolve from the repository root, and the
default is the committed configs/environment.local.json.  The fixtures skip when
that manifest or its Lean binary is absent; REQUIRE_LEAN=1 makes this a failure.
"""
import json
import os
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = "configs/environment.local.json"
PREPARE = ("python3 -m lean_compression prepare-environment --package-root <locked Lean project> "
           "--output configs/environment.new.json")


def manifest_path() -> Path:
    return ROOT / Path(os.environ.get("LEAN_COMPRESSION_ENVIRONMENT") or DEFAULT_MANIFEST).expanduser()


def lean_required() -> bool:
    value = os.environ.get("REQUIRE_LEAN", "")
    if value not in ("", "0", "1"):
        raise ValueError("REQUIRE_LEAN must be 0 or 1")
    return value == "1"


def pinned_runtime(manifest: Path | None = None) -> tuple[dict, str]:
    """Return the manifest and its Lean binary; skip, or fail under REQUIRE_LEAN=1, if absent.

    Without a manifest argument, LEAN_COMPRESSION_ENVIRONMENT is read now, not at import.
    """
    required = lean_required()
    manifest = manifest or manifest_path()
    if not manifest.is_file():
        problem = f"Lean environment manifest {manifest} does not exist"
    else:
        environment = json.loads(manifest.read_text(encoding="utf-8"))
        if Path(environment["lean_binary"]).is_file():
            return environment, environment["lean_binary"]
        problem = f"Lean binary {environment['lean_binary']} named by {manifest} is unavailable"
    message = f"{problem}; run `{PREPARE}`"
    if not os.environ.get("LEAN_COMPRESSION_ENVIRONMENT"):
        message += " and set LEAN_COMPRESSION_ENVIRONMENT=configs/environment.new.json"
    if required:
        raise RuntimeError(f"REQUIRE_LEAN=1: {message}")
    raise unittest.SkipTest(f"{message} (REQUIRE_LEAN=1 makes this a failure)")
