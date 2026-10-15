"""Archive explicitly selected operational sources; never archive credentials/results."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import tarfile

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
WORKSPACE = REPO.parents[1]
RELATIVE_WORK = Path("work/unlimited-eval-100-20261014")
WORK = WORKSPACE / RELATIVE_WORK
DEST = HERE / "workspace" / RELATIVE_WORK
SOURCE_DIRECTORIES = ("orchestration", "preparation", "reporting", "cloud", "recovery")
FIXED_FILES = (
    "verify_runtime_v2.py", "backup_local_generation_evidence.py", "runtime-amendment-v2.json",
    "preparation/launch_entries.json", "preparation/freeze-validation.json",
    "verification_v2/environment.json", "verification_v2/runtime-build.json",
    "provider-limits/protocol-note.md",
    "reporting/report-assets.tar.gz", "reporting/report-assets.json",
    "reporting/source-pins.json", "reporting/audit-pins.json",
    "reporting/parallel-report-amendment.json", "reporting/parallel-full-equivalence.json",
    "reporting/parallel-equivalence.json", "reporting/parallel-report-activation.json",
    "reporting/incidents/hardlink-fixture-ctime-v1/incident.json",
)
SECRET_PATTERNS = (
    re.compile(rb"sk-(?:ant|proj)-[A-Za-z0-9_-]{20,}"),
    re.compile(rb"-----BEGIN (?:OPENSSH |RSA |EC )?PRIVATE KEY-----"),
    re.compile(rb"(?:sig|client_secret)=[A-Za-z0-9%+/_-]{20,}"),
)


def main():
    files = {WORK / name for name in FIXED_FILES}
    for directory in SOURCE_DIRECTORIES:
        # Deliberately nonrecursive: raw evidence, proposals and live job state
        # remain local until separately reviewed for archival.
        files.update((WORK / directory).glob("*.py"))
    entries = []
    for source in sorted(files):
        if source.is_symlink() or not source.is_file():
            raise ValueError(f"Missing or symlinked allowlisted source: {source.name}")
        data = source.read_bytes()
        if any(pattern.search(data) for pattern in SECRET_PATTERNS):
            raise ValueError(f"Possible secret in allowlisted source: {source.name}")
        if source.name == "report-assets.tar.gz":
            with tarfile.open(source, "r:gz") as bundle:
                for member in bundle.getmembers():
                    name = Path(member.name)
                    if (not member.isfile() or name.is_absolute() or ".." in name.parts
                            or name.suffix not in {".py", ".js", ".css", ".html", ".json"}):
                        raise ValueError("Unexpected report source bundle member")
                    content = bundle.extractfile(member).read()
                    if any(pattern.search(content) for pattern in SECRET_PATTERNS):
                        raise ValueError("Possible secret in report source bundle")
        relative = source.relative_to(WORK)
        target = DEST / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        entries.append({"path": str(RELATIVE_WORK / relative), "bytes": len(data),
                        "sha256": hashlib.sha256(data).hexdigest()})
    manifest = {
        "experiment": "openai_math_100_provider_max_v2_20261014",
        "purpose": "Operational source archive; not a completion certificate",
        "source_core_unchanged": True,
        "result_files_included": False,
        "credentials_included": False,
        "files": entries,
    }
    (HERE / "source-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({"files": len(entries), "bytes": sum(x["bytes"] for x in entries)}))


if __name__ == "__main__":
    main()
