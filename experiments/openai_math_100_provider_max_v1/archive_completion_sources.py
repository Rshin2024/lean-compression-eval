"""Byte-copy explicit completion sources and small audit receipts; no run execution."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from refresh_snapshot import SECRET_PATTERNS
HERE = Path(__file__).resolve().parent
WORKSPACE = HERE.parents[3]
WORK = WORKSPACE / "work/unlimited-eval-100-20261014"
VALIDATION = "reporting/final-validation-v1/validation-20261015T044217.374393Z"
ALLOWLIST = {
    "final_validation_v1": [
        "reporting/final_validation_v1.py",
        "reporting/final-validation-v1/test_final_validation_v1.py",
        "reporting/final-validation-v1/CHECKLIST.md",
        "reporting/final-validation-v1/preparation-manifest.json",
        "reporting/final-validation-v1/root-review.json",
        "reporting/final-validation-v1/baseline-inputs.json",
        "reporting/final-validation-v1/launch-plan.snapshot.json",
        *[f"{VALIDATION}/{name}" for name in (
            "summary.json", "input-file-hashes.json", "original-suite-build-receipt.json",
            "finalizer-status.json", "stream-audit-summary.json")],
        "reporting/final-browser-qa.json",
    ],
    "viewer-amendment-v1": [
        *[f"reporting/viewer-amendment-v1/{name}" for name in (
            "manifest.json", "validation.json", "root-review.json",
            "live-file-preservation.json", "viewer.patch", "README.txt")],
        *[f"reporting/viewer-amendment-v1/{version}/{name}"
          for version in ("baseline", "current") for name in (
            "reporting/serve_comparison.py", "reporting/build_comparison.py",
            "reporting/source_lengths.py", "reporting/test_report_server.py",
            "src/lean_compression/__init__.py", "src/lean_compression/io.py",
            "src/lean_compression/outcomes.py", "src/lean_compression/prompts.py")],
    ],
    "operational-archive-v1": [
        *[f"cloud/operational-archive-v1/{name}" for name in (
            "archive_host.py", "remote_archive.py", "test_operational_archive.py",
            "root-review.json", "centralus-root-review.json", "plan.json",
            "all-six-hosts-receipt.json", "five-hosts-receipt.json")],
        "cloud/cleanup-plan/deletion-20261015T045306.669860Z/receipt.json",
    ],
    "closed-cost-amendment-v1": [
        *[f"reporting/closed-cost-amendment-v1/{name}" for name in (
            "close_cost_display.py", "test_close_cost_display.py", "README.md",
            "preparation-manifest-v2.json", "root-review-v2.json",
            "independent-review-v2.json", "post-application-verification.json",
            "review-history/initial-helper.py",
            "review-history/initial-preparation-manifest.json",
            "application-v1/receipt.json")],
    ],
}

# Reviewed literal detector markers and temporary-fixture key only. Exact source hashes bind exemptions.
SCAN_LITERAL_EXCEPTIONS = {'cloud/operational-archive-v1/remote_archive.py': {'sha256': '54b9b8708f809b2527f2c46c06fd3aeec539690e2b1bc4d7f81cc9d38a6fdccf', 'literal_detector_or_synthetic_fixture_lines': [25, 26]}, 'cloud/operational-archive-v1/test_operational_archive.py': {'sha256': '407662d5380c5b7c8f4396b101def6e7076283b15d83a1c19e4f0f94efd437d9', 'literal_detector_or_synthetic_fixture_lines': [60]}}

def scan_data(relative, data):
    exception = SCAN_LITERAL_EXCEPTIONS.get(relative)
    if exception:
        if hashlib.sha256(data).hexdigest() != exception["sha256"]:
            raise ValueError(f"Secret-scan exemption source changed: {relative}")
        lines = data.splitlines(keepends=True)
        for number in exception["literal_detector_or_synthetic_fixture_lines"]:
            lines[number - 1] = b"<reviewed literal detector or synthetic fixture>\n"
        return b"".join(lines)
    return data

def main():
    result = {}
    for amendment, paths in ALLOWLIST.items():
        base = HERE / "amendments" / amendment
        entries = []
        for relative in paths:
            source = WORK / relative
            if source.is_symlink() or not source.is_file():
                raise ValueError(f"Missing or symlinked source: {relative}")
            data = source.read_bytes()
            if any(pattern.search(scan_data(relative, data)) for pattern in SECRET_PATTERNS):
                raise ValueError(f"Potential secret: {relative}")
            target = base / "workspace/work/unlimited-eval-100-20261014" / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() and target.read_bytes() != data:
                raise ValueError(f"Previously archived bytes differ: {relative}")
            if not target.exists():
                target.write_bytes(data)
            if target.stat().st_ino == source.stat().st_ino:
                raise ValueError(f"Archive must not share live inode: {relative}")
            entries.append({"archive_path": str(target.relative_to(base)),
                            "workspace_relative_path": "work/unlimited-eval-100-20261014/" + relative,
                            "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)})
        manifest = {"schema_version": 1, "kind": "explicit_completion_source_and_receipt_archive",
                    "byte_copies_only": True, "files": entries,
                    "exclusions": ["model responses", "proofs and proof graphs", "private archive payloads",
                                   "before/after report or ledger snapshots", "logs and SSE", "keys and access files"],
                    "note": "Receipts bind omitted private evidence by hash; copied sources are historical artifacts, not activated by archiving."}
        dest = base / "source-hash-manifest.json"
        content = (json.dumps(manifest, indent=2) + "\n").encode()
        if dest.exists() and dest.read_bytes() != content:
            raise ValueError(f"Existing manifest differs: {amendment}")
        dest.write_bytes(content)
        result[amendment] = {"files": len(entries), "bytes": sum(x["bytes"] for x in entries),
                             "manifest_sha256": hashlib.sha256(content).hexdigest()}
    print(json.dumps(result, indent=2))

if __name__ == "__main__":
    main()
