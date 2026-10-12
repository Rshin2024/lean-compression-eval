"""Deterministic content addressing and immutable evidence records."""
from __future__ import annotations

import hashlib
import json
import os
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


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


# Settings that can change a Lean verdict or a measurement; generation and analysis settings cannot.
MEASUREMENT_FIELDS = ("validation", "extraction", "environment", "metric_profile")


def measurement_hash(config: dict) -> str:
    return digest({key: config.get(key) for key in MEASUREMENT_FIELDS})


def same_measurement(policy: dict, run: dict) -> bool:
    """Compare measurement settings; a policy or manifest written before that digest compares whole configs."""
    if policy.get("measurement_hash") and run.get("measurement_hash"):
        return policy["measurement_hash"] == run["measurement_hash"]
    return policy.get("config_hash") == run.get("config_hash")


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
    """Records end at "\\n" only (read_text turns "\\r\\n" and "\\r" into it); JSON allows raw
    U+2028/U+2029/U+0085 inside strings."""
    path = Path(path)
    if path.is_dir():
        raise ValueError(f"{path}: expected a JSONL file, found a directory")
    rows = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").split("\n"), 1):
        if line.strip():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as error:
                raise ValueError(f"{path}:{line_no}: {error}") from error
    return rows


def json_text(value: Any) -> str:
    """The text write_json stores: indented, key-sorted, strict JSON."""
    return json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n"


def write_json(path: str | Path, value: Any, *, immutable: bool = True) -> None:
    """Publish a complete file atomically; never overwrite differing frozen evidence."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json_text(value)
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


def _logged(data: bytes, event: dict) -> bool:
    """Whether an append-only log already holds this event, its recorded_at aside."""
    expected = canonical(event)
    for line in data.split(b"\n"):
        try:
            logged = json.loads(line)
            if isinstance(logged, dict) and canonical({k: v for k, v in logged.items() if k != "recorded_at"}) == expected:
                return True
        except (ValueError, RecursionError):
            continue
    return False


def append_event(path: str | Path, event: dict, *, once: bool = False) -> None:
    """Append one event, including transport retries/amendments, as one O_APPEND write under a
    process lock, so lines from concurrent workers never interleave. A line a killed writer left
    unterminated is ended first, so it can never swallow this event. With once, an event the log
    already holds, apart from its recorded_at, is not appended again."""
    import fcntl
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = canonical({"recorded_at": now(), **event}) + b"\n"
    fd = os.open(path, os.O_RDWR | os.O_APPEND | os.O_CREAT, 0o666)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        size = os.fstat(fd).st_size
        if once and _logged(os.pread(fd, size, 0), event):
            return
        if size and os.pread(fd, 1, size - 1) != b"\n":
            line = b"\n" + line
        if os.write(fd, line) != len(line):
            raise OSError(f"Incomplete event append to {path}")
        os.fsync(fd)
    finally:
        os.close(fd)


# Lock descriptors of the claims this worker holds. Checker processes inherit them (runner._run_engine),
# so a claim stays live while its check runs, even after its worker was killed.
claim_locks: ContextVar[tuple[int, ...]] = ContextVar("claim_locks", default=())


def _abandoned(marker: Path) -> int | None:
    """Lock the claim at `marker` if no process holds it; return the locked descriptor, or None.

    A worker locks its claim before publishing it and keeps the lock until it is done with it. The kernel
    drops the lock once that worker and every process that inherited the lock have exited, whatever process
    ID or host the claim names, so a claim whose lock is free is abandoned.
    """
    import fcntl
    try:
        fd = os.open(marker, os.O_RDONLY)
    except FileNotFoundError:
        return None
    locked = False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # The claim opened above may have been removed or taken over before it was locked.
        locked = os.path.samestat(os.fstat(fd), os.stat(marker))
    except (BlockingIOError, FileNotFoundError):
        pass
    finally:
        if not locked:
            os.close(fd)
    return fd if locked else None


@contextmanager
def claim(path: str | Path) -> Iterator[bool]:
    """Hold the exclusive right to produce `path` while the block runs; yield False if another worker has it.

    The claim is `path` with suffix .claim, so *.json readers skip it. It names this worker's pid, host,
    start time and command for people, and is published complete and already locked. Only its lock makes
    it live: a claim whose lock is free is taken over under that lock, so racing workers cannot both take
    it. Exiting the block removes the claim if it is still this worker's. An interrupt leaves it to its
    lock, which a checker this worker started may still hold.
    """
    import fcntl
    import socket
    import sys
    import tempfile
    marker = Path(path).with_suffix(".claim")
    marker.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=f".{marker.name}.", dir=marker.parent)
    lock, held = None, False
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump({"pid": os.getpid(), "host": socket.gethostname(), "started_at": now(), "command": sys.argv}, out)
        # Read-only is enough to lock, and a checker that inherits the lock cannot write the claim.
        lock = os.open(temp, os.O_RDONLY)
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            os.link(temp, marker)
            held = True
        except FileExistsError:
            abandoned = _abandoned(marker)
            if abandoned is not None:
                try:
                    # While this worker holds the abandoned claim's lock, no other worker can replace or remove it.
                    os.replace(temp, marker)
                    held = True
                finally:
                    os.close(abandoned)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
        if lock is not None and not held:
            os.close(lock)
    if not held:
        yield False
        return
    token, interrupted = claim_locks.set((*claim_locks.get(), lock)), False
    try:
        yield True
    except KeyboardInterrupt:
        # An interrupted worker's checker may still be running; a rerun takes the claim over once it exits.
        interrupted = True
        raise
    finally:
        claim_locks.reset(token)
        if not interrupted:
            # Unlink before unlocking, so no taker finds this claim unlocked, and only while it is still this worker's.
            with suppress(FileNotFoundError):
                if os.path.samestat(os.fstat(lock), os.stat(marker)):
                    marker.unlink()
        os.close(lock)


def remove_abandoned_claim(path: str | Path) -> None:
    """Remove the claim beside `path` if no process holds it, as a worker killed after writing `path` leaves it."""
    marker = Path(path).with_suffix(".claim")
    abandoned = _abandoned(marker)
    if abandoned is not None:
        try:
            marker.unlink()
        finally:
            os.close(abandoned)
