"""Deterministic content addressing and immutable evidence records."""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def file_hash(path: str | Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def evaluator_hash() -> str:
    source = Path(__file__).parent
    return digest({p.name: file_hash(p) for p in sorted(source.glob("*.py"))})


def task_input_hash(task: dict) -> str:
    """Audit annotations may evolve before freezing; the mathematical input must not."""
    fields = ("task_id", "imports", "target_name", "binders", "proposition", "context",
              "premise_context", "forbidden_declarations")
    value = {key: task.get(key) for key in fields}
    if task.get("import_specs"):
        value["import_specs"] = task["import_specs"]
    return digest(value)


def verification_request_hash(task: dict) -> str:
    """Hash the Lean inputs separately from the model-visible premise presentation."""
    fields = ("task_id", "imports", "target_name", "binders", "proposition", "context", "forbidden_declarations")
    value = {key: task.get(key) for key in fields}
    if task.get("import_specs"):
        value["import_specs"] = task["import_specs"]
    return digest(value)


def reference_matches_task(record: dict, task: dict) -> bool:
    return (record.get("verification_request_hash") == verification_request_hash(task)
            or record.get("task_input_hash") == task_input_hash(task)
            or record.get("task_hash") == digest(task))


def amend_reference_identity(record: dict, original_task: dict) -> dict:
    """Derive a narrower runtime identity from a matching saved full-input snapshot.

    This adds provenance, never claims another proof execution or changes results.
    The original record remains available and is linked by its complete SHA-256.
    """
    if not (record.get("task_hash") == digest(original_task)
            or record.get("task_input_hash") == task_input_hash(original_task)):
        raise ValueError("Saved task snapshot does not match the original proof-check identity")
    return {**record, "verification_request_hash": verification_request_hash(original_task),
            "identity_amendment": {"kind": "derive_runtime_identity_from_matching_input_snapshot",
                                   "original_record_hash": digest(record), "original_task_hash": digest(original_task),
                                   "new_proof_execution": False}}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def read_jsonl(path: str | Path) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines()
            if line.strip()]


def write_json(path: str | Path, value: Any, *, immutable: bool = True) -> None:
    """Publish a complete file atomically; never overwrite differing frozen evidence."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True,
                      allow_nan=False) + "\n"
    if immutable and path.exists():
        if canonical(read_json(path)) != canonical(value):
            raise FileExistsError(f"Refusing to overwrite immutable evidence: {path}")
        return
    import tempfile
    fd, temp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        if immutable:
            try:
                os.link(temp, path)
            except FileExistsError:
                if canonical(read_json(path)) != canonical(value):
                    raise
        else:
            os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def append_event(path: str | Path, event: dict) -> None:
    """Append one event under a process lock, including transport retries/amendments."""
    import fcntl
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as out:
        fcntl.flock(out.fileno(), fcntl.LOCK_EX)
        out.write(canonical({"recorded_at": now(), **event}).decode() + "\n")
        out.flush()
        os.fsync(out.fileno())
        fcntl.flock(out.fileno(), fcntl.LOCK_UN)
