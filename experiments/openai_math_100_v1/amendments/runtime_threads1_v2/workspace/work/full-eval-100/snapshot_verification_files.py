"""Hash a quiescent verification revision for exact cloud/local backup checks.

This records file identity, not scientific validity. It intentionally excludes
temporary files and claims; callers must confirm the verifier has stopped first.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re

MODELS = ["gpt-6.1-sol", "gpt-6-sol", "gpt-6-luna", "gpt-6-astra", "claude-haiku-5-5",
          "claude-sonnet-5-5", "claude-opus-5-5", "claude-fable-5-1"]


def snapshot(root, version):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", version):
        raise ValueError("Unsafe version")
    root = Path(root)
    rows = {}
    for model in MODELS:
        directory = root / f"om100-{model}-high" / "evaluations" / version
        if not (directory / "policy.json").is_file():
            raise ValueError(f"Missing revision: {model}")
        for path in sorted(directory.rglob("*")):
            if not path.is_file() or path.suffix not in {".json", ".jsonl"} or any(
                    part.startswith(".") for part in path.relative_to(directory).parts):
                continue
            if path.is_symlink():
                raise ValueError("Verification evidence must not be a symlink")
            before = path.stat()
            sha = hashlib.sha256()
            with path.open("rb") as stream:
                while block := stream.read(4 * 1024 * 1024):
                    sha.update(block)
            after = path.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise ValueError("Verification evidence changed during snapshot")
            rows[str(path.relative_to(root))] = {"bytes": after.st_size, "sha256": sha.hexdigest()}
    return {"schema_version": 1, "version": version, "file_count": len(rows),
            "bytes": sum(row["bytes"] for row in rows.values()), "files": rows}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs-root", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    value = snapshot(args.runs_root, args.version)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")
    print(json.dumps({k: v for k, v in value.items() if k != "files"}))
