#!/usr/bin/env python3
"""Copy only reviewed preparation source into this archive; never run it.

The archive is a byte-for-byte source snapshot, not an executable evaluation
bundle. A refresh is explicit and must precede any source release/commit.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil

HERE = Path(__file__).resolve().parent
PREFIX = Path('work/full-eval-100')
ALLOWLIST = (
    'preparation/extract_scoped_tasks.py',
    'preparation/test_extract_scoped_tasks.py',
    'preparation/README.md',
    'materialize.py',
    'repair_materialization.py',
    'diagnose_signature.py',
    'finish_signature_edges.py',
    'calibrate_reference_090.py',
    'assemble_materialized.py',
    'check_official_references.py',
    'audit_final_inputs.py',
    'freeze_full.py',
    'run_full_evaluation.py',
    'test_controller.py',
    'config.preparation.json',
    'config.reference-final.json',
    'premises/PremiseExporter.lean',
    'premises/export_premises.py',
    'premises/audit_exports.py',
    'premises/test_export_premises.py',
    'premises/test_audit_exports.py',
    'premises/probe_layout.py',
    'premises/repair_let_layout.py',
    'premises/finalize_let_layout.py',
    'premises/README.md',
    'runtime/Dockerfile.build',
    'runtime/Dockerfile.final',
    'runtime/prepare_context.py',
    'runtime/finish-runtime.sh',
    'runtime/verify_builder.py',
    'runtime/verify_runtime.py',
    'runtime/verify-final.sh',
    'runtime/Readiness.lean',
    'runtime/context/closure-manifest.json',
    'runtime/context/package/lean-toolchain',
    'runtime/context/package/lakefile.lean',
    'runtime/context/package/RequiredImports.lean',
)
SECRET_PATTERN = re.compile(rb'-----BEGIN [A-Z ]*PRIVATE KEY-----|\bsk-(?:ant-|proj-)[A-Za-z0-9_-]{20,}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    entries = []
    expected = set()
    for relative in ALLOWLIST:
        source_relative = PREFIX / relative
        source = workspace / source_relative
        archive_relative = Path('workspace') / source_relative
        target = HERE / archive_relative
        if source.is_symlink() or not source.is_file():
            raise ValueError(f'Missing regular allowlisted source: {source_relative}')
        content = source.read_bytes()
        if len(content) > 2_000_000 or SECRET_PATTERN.search(content):
            raise ValueError(f'Size or credential-content check failed: {source_relative}')
        digest = hashlib.sha256(content).hexdigest()
        if args.verify_only:
            if not target.is_file() or target.read_bytes() != content:
                raise ValueError(f'Snapshot differs from allowlisted source: {source_relative}')
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            shutil.copymode(source, target)
        if source.read_bytes() != content:
            raise ValueError(f'Source changed while snapshotting: {source_relative}')
        expected.add(target.resolve())
        entries.append({'path': archive_relative.as_posix(),
                        'source_path': source_relative.as_posix(),
                        'sha256': digest, 'bytes': len(content), 'copy_mode': 'exact_bytes'})
    actual = {p.resolve() for p in (HERE/'workspace').rglob('*') if p.is_file()}
    if actual != expected:
        raise ValueError('Archive workspace contains unexpected files; inspect explicitly, do not publish')
    metadata = []
    for name in ('README.md', 'pins.json', 'refresh_snapshot.py', 'package_prepared.py'):
        content = (HERE/name).read_bytes()
        if SECRET_PATTERN.search(content):
            raise ValueError(f'Credential-content check failed: {name}')
        metadata.append({'path': name, 'sha256': hashlib.sha256(content).hexdigest(), 'bytes': len(content)})
    if args.verify_only:
        recorded = json.loads((HERE/'source-manifest.json').read_text())
        if recorded['files'] != entries or recorded.get('archive_metadata') != metadata:
            raise ValueError('Source manifest differs from current verified archive')
    else:
        manifest = {'schema_version': 1, 'snapshot_at_utc': datetime.now(timezone.utc).isoformat(),
            'kind': 'source_only_reproduction_archive', 'files': entries,
            'archive_metadata': metadata,
            'file_count': len(entries), 'model_generation_enabled': False,
            'exclusions': ['API credentials and credential carrier', 'SSH keys/config/access metadata',
                'Azure provisioning/status/event logs', 'provider responses and evaluation records',
                'private task/reference/compiler-context artifacts', 'Docker images/tars and library sources'],
            'state_note': 'Source snapshot only. The separate launch marker and frozen run manifests govern execution.'}
        (HERE/'source-manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    print(json.dumps({'files': len(entries), 'bytes': sum(x['bytes'] for x in entries),
                      'verified_only': args.verify_only, 'credential_content_detected': False}))


if __name__ == '__main__':
    main()
