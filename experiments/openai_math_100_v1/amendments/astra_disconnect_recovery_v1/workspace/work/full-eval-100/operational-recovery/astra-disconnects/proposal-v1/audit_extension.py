#!/usr/bin/env python3
"""INACTIVE PROPOSAL: read-only, phase-labeled audit extension after approval.

The frozen auditor is imported unchanged. Its event state machine gets only an
exact-six, approval/evidence-validated exception for original unknown-call
prefixes. The full original events are returned for every subsequent binding
check and preserved in hashes/disclosures. All captured-response protections,
cost arithmetic, end rehashes and 9600 completion gates remain unchanged.
"""
import argparse
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
from common import HERE, WORK, RUN, load, events, file_hash
from contract import ensure, exception_projection


def run(args):
    proposal, proposal_sha, manifest, approval = load(args.approval)
    ensure(approval is not None, 'Actual approval record is required')
    approval_sha = file_hash(args.approval)
    adapter_sha = file_hash(Path(__file__))
    original = WORK / 'final-audit/audit_final_cohort.py'
    expected = proposal['source_pins']['files']['final-audit/audit_final_cohort.py']
    ensure(file_hash(original) == expected, 'Frozen original auditor changed')
    spec = importlib.util.spec_from_file_location('frozen_original_final_audit', original)
    base = importlib.util.module_from_spec(spec); spec.loader.exec_module(base)
    original_events, original_write = base.audit_events, base.write
    disclosures = []

    def with_approved_exception(log, slots):
        result, evidence = exception_projection(log, slots, proposal, approval,
            events(RUN / 'amendments.jsonl'), original_events)
        disclosures.extend(evidence)
        return result

    def with_phase_disclosure(path, value):
        if path.name == 'summary.json':
            ensure(file_hash(args.approval) == approval_sha and file_hash(HERE / 'proposal.json') == proposal_sha,
                   'Approval or proposal changed during audit')
            ensure(file_hash(Path(__file__)) == adapter_sha, 'Audit extension changed during audit')
            value.update(audit_version='final_cohort_astra_disconnect_recovery_v2',
                         frozen_original_auditor_sha256=expected, audit_extension_sha256=adapter_sha,
                         recovery_proposal_sha256=proposal_sha, approval_evidence_sha256=approval_sha,
                         approved_unknown_outcome_recoveries=disclosures,
                         original_disconnected_request_cost_usd=None)
            value['cost_limitations'].append('Six original Astra calls ended with unknown outcomes after connection resets. '
                'User-authorized replacement responses are separately bound to the same frozen slots; '
                'any original-call charges are unknown and excluded, never zero.')
        original_write(path, value)

    base.audit_events, base.write = with_approved_exception, with_phase_disclosure
    destination = HERE / 'audit-results' / ('audit-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ'))
    destination.mkdir(parents=True, mode=0o700)
    result = base.audit(args, destination)
    return {'status': result['status'], 'evidence': str(destination), 'cohort_complete': result['cohort_complete'],
            'audit_version': result['audit_version'], 'approved_unknown_outcome_recoveries': disclosures}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--approval', type=Path, required=True)
    p.add_argument('--require-complete', action='store_true')
    p.add_argument('--model', action='append')
    print(json.dumps(run(p.parse_args()), indent=2))
