"""Hash private local generation evidence for a fully drained model; no network."""
from pathlib import Path
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os

WORK = Path(__file__).resolve().parent
REPO = WORK.parents[1] / "outputs/lean-compression-eval"
PLAN_SHA256 = "241c35772e32ea8780d2df307300ba7c2bb7ae38ca12537f6cf06481c7b19d53"


def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for data in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(data)
    return digest.hexdigest()


def identity(path):
    value = path.stat()
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns


def preserve(model):
    plan_path = WORK / "launch_ready.json"
    assert sha(plan_path) == PLAN_SHA256, "Launch changed"
    entries = [entry for entry in json.loads(plan_path.read_text())["runs"] if entry["model"] == model]
    assert len(entries) == 1, "Model is not uniquely scheduled"
    entry = entries[0]
    run = REPO / "runs" / entry["directory"]
    assert sha(run / "manifest.json") == entry["manifest_sha256"], "Manifest changed"
    manifest = json.loads((run / "manifest.json").read_text())
    expected = {slot["attempt_id"] for slot in manifest["slots"]}
    assert len(expected) == 1200
    assert {path.stem for path in (run / "generations").glob("*.json")} == expected, "Incomplete generation cohort"
    assert not list((run / "inflight").glob("*.json")), "In-flight marker remains"
    status = json.loads((WORK / "generation-status.json").read_text())
    assert status["active_per_model"][model] == 0, "Generation workers remain active"
    files = {run / "manifest.json"}
    for name in ("transport.jsonl", "stream-transport.jsonl", "amendments.jsonl"):
        if (run / name).exists():
            files.add(run / name)
    files.update((run / "generations").glob("*.json"))
    files.update((run / "captures").glob("*.jsonl"))
    files.update((run / "stream-captures").rglob("*.sse"))
    rows = []
    for path in sorted(files):
        assert not path.is_symlink() and path.resolve().is_relative_to(run.resolve())
        before = identity(path)
        digest = sha(path)
        assert identity(path) == before, "Evidence changed while hashing"
        rows.append({"path": str(path.relative_to(run)), "bytes": before[2], "sha256": digest})
    assert not list((run / "inflight").glob("*.json")), "New request started during preservation"
    receipt = {
        "state": "local_generation_evidence_hashed", "model": model,
        "at_utc": datetime.now(timezone.utc).isoformat(),
        "launch_sha256": PLAN_SHA256, "manifest_sha256": entry["manifest_sha256"],
        "run_hash": entry["run_hash"], "generation_count": len(expected),
        "file_count": len(rows), "total_bytes": sum(row["bytes"] for row in rows),
        "files": rows, "new_model_calls": 0, "credentials_read": False,
        "scope": "Private local response, capture, stream and transport files; full cloud proof graphs have separate receipts.",
    }
    dest = WORK / "final-backups" / f"{model}-local-generation-evidence.json"
    dest.parent.mkdir(exist_ok=True)
    assert not dest.exists(), "Preserve the existing receipt rather than overwrite it"
    with dest.open("x") as stream:
        json.dump(receipt, stream, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    return {key: receipt[key] for key in ("state", "model", "generation_count", "file_count", "total_bytes")}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    print(json.dumps(preserve(parser.parse_args().model)))
