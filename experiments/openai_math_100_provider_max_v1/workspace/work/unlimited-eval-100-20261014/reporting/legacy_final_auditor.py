#!/usr/bin/env python3
"""Read frozen local evidence; write only a new audit directory below this one.

Extends audits/audit_live_protocol_costs.py with capture binding, deterministic
schedule reconstruction, documented recovery validation and final-completion
gates. No model/Lean/network calls, marker locks, run edits or cloud cleanup.
"""
from __future__ import annotations

import argparse
import base64
from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
from decimal import Decimal, localcontext
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
WORK = HERE.parent
REPO = WORK.parents[1] / 'outputs/lean-compression-eval'
SUITE = REPO / 'runs/om100-suite-high-20261013'
sys.path.insert(0, str(REPO / 'src'))
from lean_compression.io import digest, evaluator_hash, file_hash
from lean_compression.store import schedule
from lean_compression.integrity import verified_generation
from lean_compression.providers import response_payload, parse_body
from lean_compression.costs import estimate_cost
from lean_compression.cost_reporting import build_cost_report, _attempt_csv_rows, _summary_csv_rows, _csv_value
from lean_compression.outcomes import GENERATION_OUTCOMES

MODELS = {'claude-fable-5-1', 'claude-haiku-5-5', 'claude-opus-5-5', 'claude-sonnet-5-5',
          'gpt-6-astra', 'gpt-6-luna', 'gpt-6-sol', 'gpt-6.1-sol'}
CONDITIONS = ['standard', 'mathematical_compression', 'source_concision']
RECOVERY_SHA = '871565a1d0b4bbafb584351c47fdef596179587ca9f590de7cfc116d655a7d74'
HTTP520_ATTEMPT = 'b2ed1802b3e9a0f8383045d001d298d4c25293ff0971499c30b30526e5f4e165'
HEX64 = re.compile(r'[0-9a-f]{64}')


def ensure(ok, message):
    if not ok:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def read(path):
    return json.loads(path.read_bytes())


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def timestamp(value):
    result = datetime.fromisoformat(value)
    ensure(result.tzinfo is not None, 'Missing timestamp timezone')
    return result


def rows(raw):
    return [json.loads(line) for line in raw.splitlines() if line.strip()]


def names(path, suffix='.json'):
    found = {p.stem: p for p in path.glob('*' + suffix)}
    ensure(all(HEX64.fullmatch(name) for name in found), f'Unexpected record file in {path}')
    return found


def write(path, value):
    # Exclusive creation makes each completed audit a retained checkpoint.
    with path.open('x') as stream:
        os.chmod(path, 0o600)
        json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write('\n')


def require_file_snapshot(expected):
    """Detect same-name replacement of already-audited immutable evidence."""
    for path, expected_sha in expected.items():
        ensure(file_hash(Path(path)) == expected_sha, f'Audited file changed during scan: {path}')


def independent_amount(record, pricing):
    """Recompute usage buckets and exact Decimal sums without the estimator."""
    usage = record['usage']
    inp, out = usage['input_tokens'], usage['output_tokens']
    if pricing['provider'] == 'openai':
        detail = usage['input_tokens_details']
        cached, written = detail['cached_tokens'], detail['cache_write_tokens']
        tokens = {'input': inp - cached - written, 'cache_read': cached,
                  'cache_write': written, 'output': out}
        total_input = inp
    else:
        cached, written = usage['cache_read_input_tokens'], usage['cache_creation_input_tokens']
        ttl = usage.get('cache_creation') or {}
        five, hour = ttl.get('ephemeral_5m_input_tokens', 0), ttl.get('ephemeral_1h_input_tokens', 0)
        ensure(five + hour == written, 'Cache TTL usage mismatch')
        tokens = {'input': inp, 'cache_read': cached, 'cache_write': five, 'output': out}
        if 'cache_write_1h' in pricing['rates_per_million'] or hour:
            tokens['cache_write_1h'] = hour
        total_input = inp + cached + written
    ensure(all(type(n) is int and n >= 0 for n in tokens.values()), 'Invalid token usage')
    long = pricing.get('long_context')
    premium = bool(long and total_input > long['threshold_input_tokens'])
    with localcontext() as ctx:
        ctx.prec = 80
        components = {}
        for kind, n in tokens.items():
            factor = Decimal(long['output_multiplier' if kind == 'output' else 'input_multiplier']) if premium else Decimal(1)
            amount = Decimal(n) * Decimal(pricing['rates_per_million'][kind]) * factor / Decimal(1000000)
            components[kind] = {'tokens': n, 'multiplier': factor, 'amount': amount}
        return sum((c['amount'] for c in components.values()), Decimal(0)), components


def audit_events(events, slots):
    by_id = defaultdict(list)
    for event in events:
        ensure(event.get('attempt_id') in slots, 'Unscheduled transport attempt')
        by_id[event['attempt_id']].append(event)
    pending = []
    for aid, sequence in by_id.items():
        ensure([timestamp(e['recorded_at']) for e in sequence] ==
               sorted(timestamp(e['recorded_at']) for e in sequence), f'Nonmonotonic transport: {aid}')
        active = None
        received = False
        for event in sequence:
            kind = event['event']
            if kind == 'request_started':
                ensure(active is None and not received, f'Request after received response or unresolved request: {aid}')
                active = event
            elif kind in {'response_received', 'http_error', 'request_not_sent'}:
                ensure(active is not None and event['transport_index'] == active['transport_index'],
                       f'Response/error has no corresponding request: {aid}')
                active = None
                received = kind == 'response_received'
            elif kind == 'outcome_unknown':
                ensure(received or active is not None, f'Unknown outcome lacks preceding request: {aid}')
            elif kind == 'no_model_outcome':
                ensure(not received and active is None, f'Non-outcome unexpectedly discards capture: {aid}')
            else:
                raise ValueError(f'Unexpected transport event {kind}: {aid}')
        if active is not None:
            pending.append(aid)
    return by_id, pending


def audit_capture(run, slot, record, events, recovery, amendments):
    aid, model = slot['attempt_id'], slot['model_snapshot']
    settings = record['generation_settings']
    path = run / 'captures' / (aid + '.jsonl')
    raw = path.read_bytes()
    captured = rows(raw)
    ensure(len(captured) == 1, f'Expected exactly one captured response: {aid}')
    cap = captured[0]
    ensure(cap['attempt_id'] == aid, f'Capture attempt mismatch: {aid}')
    body = base64.b64decode(cap['body_base64'], validate=True)
    ensure(sha(body) == cap['body_sha256'], f'Capture bytes hash mismatch: {aid}')
    binding = {'path': f'captures/{aid}.jsonl', 'body_sha256': sha(body)}
    ensure(record['response_capture'] == binding, f'Generation capture differs: {aid}')
    received = [e for e in events if e['event'] == 'response_received']
    ensure(len(received) == 1, f'Expected exactly one received provider response: {aid}')
    received = received[0]
    ensure(received['capture'] == binding and received['request_id'] == cap['request_id'] and
           received['transport_index'] == cap['transport_index'], f'Transport capture binding mismatch: {aid}')
    requests = [e for e in events if e['event'] == 'request_started']
    ensure(requests and requests[-1]['transport_index'] == cap['transport_index'], f'Request capture index mismatch: {aid}')
    moments = [timestamp(requests[-1]['recorded_at']), timestamp(cap['recorded_at']),
               timestamp(received['recorded_at']), timestamp(record['created_at'])]
    ensure(moments == sorted(moments), f'Capture/generation chronology mismatch: {aid}')
    parsed = parse_body(body, model, settings['provider'])
    if record.get('capture_reconciliation'):
        marker = record['capture_reconciliation']['marker']
        evidence = recovery.bound_evidence(run, slot, settings, marker)
        recovery.check_existing(record, slot, settings, evidence, marker)
        proof = record['capture_reconciliation']
        matches = [e for e in amendments if e.get('attempt_id') == aid and
                   {k: v for k, v in e.items() if k != 'recorded_at'} == proof]
        ensure(len(matches) == 1, f'Reconciliation amendment missing or duplicate: {aid}')
    else:
        ensure(parsed.get('generation_status') in GENERATION_OUTCOMES, f'Unresolved provider capture: {aid}')
        for key, value in parsed.items():
            ensure(record.get(key) == value, f'Capture-derived generation field differs ({key}): {aid}')
    ensure(record['provider_response_id'], f'Missing provider response identity: {aid}')
    return {'capture_file_sha256': sha(raw), 'response_body_sha256': sha(body),
            'request_id_hash': digest(cap['request_id']), 'received_at': received['recorded_at'],
            'capture_reconciliation_kind': (record.get('capture_reconciliation') or {}).get('kind')}


def check_cost_reports(model, manifest, records):
    directory = SUITE / ('report-' + model)
    path = directory / 'costs.json'
    if not path.exists():
        return {'present': False, 'verified': False}
    report_raw = path.read_bytes()
    actual = json.loads(report_raw)
    attempts = {r['attempt_id']: r for r in actual['attempts']}
    ensure(len(attempts) == len(actual['attempts']) == 1200, f'Cost report identity count: {model}')
    projected = []
    slots = {s['attempt_id']: s for s in manifest['slots']}
    ensure(set(attempts) == set(slots), f'Cost report scheduled identity set differs: {model}')
    # The phase-aware adapter sorts records; report order is not schedule order.
    for reported in actual['attempts']:
        slot = slots[reported['attempt_id']]
        projected.append({**{k: v for k, v in slot.items() if k != 'messages'},
                          'generation': records.get(slot['attempt_id']),
                          'validity_status': attempts[slot['attempt_id']]['validity_status']})
    expected = build_cost_report(projected, manifest['config']['pricing'],
                                provider=manifest['config']['generation']['provider'], model=model)
    ensure(actual == expected, f'Cost report differs from frozen generation accounting: {model}')
    evidence = {'costs.json': sha(report_raw)}
    for filename, iterator in [('cost_attempts.csv', _attempt_csv_rows), ('cost_summary.csv', _summary_csv_rows)]:
        csv_path = directory / filename
        with csv_path.open(newline='') as stream:
            actual_rows = list(csv.DictReader(stream))
        expected_rows = [{k: '' if (value := _csv_value(v)) is None else str(value) for k, v in row.items()}
                         for row in iterator(expected)]
        ensure(actual_rows == expected_rows, f'Cost CSV differs from source records: {model}/{filename}')
        evidence[filename] = file_hash(csv_path)
    return {'present': True, 'verified': True, 'file_sha256': evidence, 'summary': actual['summary'],
            'scope': 'Cost values and scheduled identities; proof verdict correctness is checked by the separate phase-aware report audit.'}


def cloud_costs(at):
    path = WORK / 'runtime/azure/provisioning.json'
    raw = path.read_bytes()
    state = json.loads(raw)
    intervals, total = [], Decimal(0)
    for row in state['allocation_intervals']:
        end = timestamp(row['end_utc']) if row.get('end_utc') else at
        seconds = Decimal(str(max(0, (end - timestamp(row['start_utc'])).total_seconds())))
        amount = seconds * Decimal(row['hourly_rate_usd']) / 3600
        total += amount
        intervals.append({**row, 'observed_through_utc': end.isoformat(),
                          'retail_estimated_usd': format(amount, 'f')})
    return {'provisioning_sha256': sha(raw), 'known_compute_retail_subtotal_usd': format(total, 'f'),
            'allocation_intervals': intervals, 'unknown_allocation_intervals': state.get('unknown_allocation_intervals', []),
            'total_cloud_cost_usd': None, 'invoice_total_usd': None,
            'basis': 'Recorded allocation wall-time proxies at frozen SKU retail rates. Running allocation continues after this snapshot.',
            'excluded': ['unknown resize billing boundaries', 'disk', 'public IP', 'network transfer', 'taxes', 'credits', 'adjustments']}


def audit(args, destination):
    started = datetime.now(timezone.utc)
    script_sha = file_hash(Path(__file__))
    plan_raw = (WORK / 'launch_ready.json').read_bytes()
    plan = json.loads(plan_raw)
    ensure(plan['ready'] is True and plan['scheduled_attempts'] == 9600, 'Launch plan not ready for exact 9600')
    ensure({e['model'] for e in plan['runs']} == MODELS and len(plan['runs']) == 8, 'Unexpected model cohort')
    ensure(evaluator_hash() == plan['source_hash'], 'Frozen core source changed')
    ensure(file_hash(REPO / 'lean/Evaluator.lean') == plan['engine_hash'], 'Frozen Lean engine changed')
    ensure(file_hash(WORK / 'recover_text_tool_stops.py') == RECOVERY_SHA, 'Recovery audit dependency changed')
    spec = importlib.util.spec_from_file_location('recovery_audit', WORK / 'recover_text_tool_stops.py')
    recovery = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(recovery)
    selected = set(args.model or MODELS)
    ensure(selected <= MODELS, 'Unknown model selection')
    ensure(not args.require_complete or selected == MODELS, 'Final completion requires all eight models')
    manifests, identities, shared_tasks, manifest_evidence = {}, set(), None, []
    # Audit all frozen schedules even when checking only completed-model responses.
    for entry in plan['runs']:
        model, run = entry['model'], REPO / 'runs' / entry['directory']
        raw = (run / 'manifest.json').read_bytes()
        m = json.loads(raw)
        ensure(sha(raw) == entry['manifest_sha256'], f'Manifest bytes changed: {model}')
        ensure(m['run_hash'] == entry['run_hash'] == digest({k: v for k, v in m.items() if k != 'run_hash'}), f'Run identity changed: {model}')
        config, settings = m['config'], m['config']['generation']
        ensure(m['model_snapshot'] == settings['model_snapshot'] == model and settings['reasoning_setting'] == 'high'
               and settings['max_output_tokens'] == 8192 and settings['snapshot_pinned'] is True
               and settings['mode'] == 'single_shot' and settings['response_adapter'] == 'single_outer_wrapper_v2', f'Generation policy changed: {model}')
        ensure(config['conditions'] == CONDITIONS and config['samples_per_goal_condition'] == 4, f'Condition/sample policy changed: {model}')
        ensure(config['cost_tracking_required'] is True and config['pricing']['model'] == model, f'Cost policy absent: {model}')
        ensure(m['evaluator_source_hash'] == plan['source_hash'] and m['engine_hash'] == plan['engine_hash'], f'Frozen engine/source differs: {model}')
        ensure(len(m['tasks']) == 100 and {t['task_id'] for t in m['tasks']} == {f'OM100-{i:03d}' for i in range(1, 101)}, f'Question cohort differs: {model}')
        current = digest(m['tasks'])
        ensure(shared_tasks in (None, current), 'Models do not share exactly the same tasks')
        shared_tasks = current
        ensure(m['slots'] == schedule(m['tasks'], config, m['environment'], model), f'Frozen schedule cannot be reconstructed: {model}')
        slots = {s['attempt_id']: s for s in m['slots']}
        ensure(len(slots) == len(m['slots']) == 1200 and not identities.intersection(slots), 'Duplicate scheduled slot identity')
        identities.update(slots)
        manifest_evidence.append({'model': model, 'manifest_sha256': sha(raw), 'run_hash': m['run_hash'], 'pricing_hash': digest(config['pricing']),
                                  'slot_id_set_hash': digest(sorted(slots)), 'scheduled': 1200})
        if model in selected:
            manifests[model] = (run, m, slots)
    ensure(len(identities) == 9600, 'Frozen global schedule does not contain exactly 9600 unique IDs')
    summaries, response_ids, grand, complete_models, http520 = [], {}, Decimal(0), [], None
    audited_files = {}
    attempt_path = destination / 'attempt-evidence.jsonl'
    with attempt_path.open('x') as evidence_stream:
        os.chmod(attempt_path, 0o600)
        for model in sorted(selected):
            run, m, slots = manifests[model]
            settings, pricing = m['config']['generation'], m['config']['pricing']
            paths = names(run / 'generations')
            ensure(set(paths) <= set(slots), f'Unscheduled generation: {model}')
            captures = names(run / 'captures', '.jsonl')
            ensure(set(captures) <= set(slots), f'Unscheduled capture: {model}')
            v2 = {p.stem for p in (run / 'evaluations/v2_threads1').glob('*.json') if HEX64.fullmatch(p.stem)}
            ensure(v2 <= set(slots), f'Unscheduled v2 evaluation: {model}')
            if args.require_complete:
                ensure(set(paths) == set(slots) == v2 and set(captures) == set(slots), f'{model}: all 1200 generations/captures/v2 records are not ready')
            transport_raw = (run / 'transport.jsonl').read_bytes()
            events = rows(transport_raw)
            by_id, pending_requests = audit_events(events, slots)
            amendment_path = run / 'amendments.jsonl'
            amendment_raw = amendment_path.read_bytes() if amendment_path.exists() else b''
            amendments = rows(amendment_raw)
            for aid, sequence in by_id.items():
                expected = digest(response_payload(slots[aid], settings))
                ensure(all(e['request_hash'] == expected for e in sequence if e['event'] == 'request_started'), f'Request differs from frozen payload: {aid}')
            records, subtotal, priced, statuses, recoveries = {}, Decimal(0), 0, Counter(), Counter()
            for aid, path in sorted(paths.items()):
                raw = path.read_bytes()
                record = verified_generation(json.loads(raw), slots[aid])
                ensure(path.stem == record['attempt_id'], f'Generation filename differs: {aid}')
                ensure(record['generation_settings'] == settings and record['returned_model'] == model, f'Generation settings/model differ: {aid}')
                ensure(record['generation_status'] in GENERATION_OUTCOMES, f'Incomplete generation outcome: {aid}')
                ensure(all(timestamp(e['recorded_at']) <= timestamp(record['created_at']) for e in by_id[aid]
                           if e['event'] == 'request_started'), f'New request after recorded completion: {aid}')
                binding = audit_capture(run, slots[aid], record, by_id[aid], recovery, amendments)
                audited_files[str(path)] = sha(raw)
                audited_files[str(run / 'captures' / (aid + '.jsonl'))] = binding['capture_file_sha256']
                rid = record['provider_response_id']
                ensure(rid not in response_ids, f'Provider response reused across slots: {aid}')
                response_ids[rid] = aid
                fresh = estimate_cost(record, pricing, provider=settings['provider'], model=model)
                ensure(fresh == record['cost_estimate'], f'Stored cost differs from frozen estimator: {aid}')
                ensure(fresh['status'] == 'estimated', f'Reported usage cannot be priced: {aid}')
                amount, components = independent_amount(record, pricing)
                ensure(amount == Decimal(fresh['total_usd']), f'Independent token cost does not reconcile: {aid}')
                ensure(set(components) == set(fresh['components']), f'Cost components differ: {aid}')
                for kind, values in components.items():
                    actual = fresh['components'][kind]
                    ensure(values['tokens'] == actual['tokens'] and values['multiplier'] == Decimal(actual['multiplier'])
                           and values['amount'] == Decimal(actual['cost_usd']), f'Cost component differs ({kind}): {aid}')
                subtotal += amount
                priced += 1
                statuses[record['generation_status']] += 1
                if binding['capture_reconciliation_kind']:
                    recoveries[binding['capture_reconciliation_kind']] += 1
                evidence_stream.write(json.dumps({'model': model, 'attempt_id': aid, 'generation_sha256': sha(raw),
                    'provider_response_id_hash': digest(rid), 'request_hash': digest(response_payload(slots[aid], settings)),
                    'raw_response_hash': record['raw_response_hash'], 'generation_status': record['generation_status'],
                    'cost_usd': fresh['total_usd'], 'usage_hash': fresh['usage_hash'], 'pricing_hash': fresh['pricing_hash'], **binding}) + '\n')
                records[aid] = record
            markers = names(run / 'inflight')
            ensure(set(markers) <= set(slots), f'Unscheduled in-flight marker: {model}')
            settled = len(records) == 1200 and len(v2) == 1200 and not markers and not pending_requests
            stable = (sha((run / 'transport.jsonl').read_bytes()) == sha(transport_raw) and
                      set(names(run / 'generations')) == set(paths) and
                      (amendment_path.read_bytes() if amendment_path.exists() else b'') == amendment_raw)
            if args.require_complete:
                ensure(settled and stable, f'{model}: snapshot still changing or unresolved markers remain')
            cost_report = check_cost_reports(model, m, records) if settled else {'present': False, 'verified': False, 'reason': 'model_not_settled'}
            if args.require_complete:
                ensure(cost_report['verified'], f'{model}: completed cost reports are not ready')
            if settled and stable and cost_report['verified']:
                complete_models.append(model)
            exceptional = [{k: e.get(k) for k in ('attempt_id', 'event', 'recorded_at', 'http_status', 'type') if k in e}
                           for e in events if e['event'] in {'http_error', 'request_not_sent', 'no_model_outcome', 'outcome_unknown'}]
            if HTTP520_ATTEMPT in by_id:
                seq = by_id[HTTP520_ATTEMPT]
                errors = [e for e in seq if e['event'] == 'http_error' and e['http_status'] == 520]
                if errors:
                    ensure(len(errors) == 1 and errors[0]['body'] == 'error code: 520\n', 'HTTP 520 recovery history differs')
                    endings = [e for e in seq if e['event'] == 'no_model_outcome']
                    ensure(len(endings) == 1 and endings[0]['capture'] is None and endings[0]['provider_response'] is None
                           and endings[0]['prompt_rejected'] is False, 'HTTP 520 non-outcome binding differs')
                    http520 = {'model': model, 'attempt_id': HTTP520_ATTEMPT, 'transport_events': [e['event'] for e in seq],
                        'recorded_generation_present': HTTP520_ATTEMPT in records, 'captured_response_count': sum(e['event'] == 'response_received' for e in seq),
                        'failed_request_cost_usd': None, 'reason': 'HTTP 520 supplied no model response or token usage. Any failed-request charge is unknown and excluded, not zero. Only the later recorded response is priced.'}
            summaries.append({'model': model, 'scheduled': 1200, 'generations_audited': len(records), 'v2_record_files': len(v2),
                'priced_generations': priced, 'observed_api_token_estimate_usd': format(subtotal, 'f'),
                'generation_status_counts': dict(statuses), 'capture_reconciliations': dict(recoveries),
                'transport_sha256': sha(transport_raw), 'transport_events': dict(Counter(e['event'] for e in events)),
                'amendments_sha256': sha(amendment_raw), 'exceptional_transport_events': exceptional,
                'pending_request_ids': pending_requests, 'inflight_marker_ids': sorted(markers),
                'snapshot_stable': stable, 'model_settled': settled, 'cost_report': cost_report, 'pricing_schedule': pricing})
            grand += subtotal
            print(json.dumps({'model': model, 'generations_audited': len(records), 'settled': settled,
                              'snapshot_stable': stable, 'cost_report_verified': cost_report['verified']}), flush=True)
    cohort_complete = len(complete_models) == 8 and sum(s['generations_audited'] for s in summaries) == 9600
    if args.require_complete:
        ensure(cohort_complete, 'All 9600 responses and cost reports have not completed')
    # Filename sets alone do not detect concurrent same-name replacement.
    # Rehash every audited generation and capture once at the end of the scan.
    require_file_snapshot(audited_files)
    ensure(file_hash(Path(__file__)) == script_sha, 'Audit source changed while running; repeat with stable audit source')
    write(destination / 'file-snapshot.json', audited_files)
    result = {'schema_version': 1, 'status': 'pass_complete' if cohort_complete else 'pass_partial_snapshot',
        'cohort_complete': cohort_complete, 'requested_complete_gate': args.require_complete,
        'audit_started_utc': started.isoformat(), 'audit_finished_utc': utcnow(), 'audit_script_sha256': script_sha,
        'based_on_prior_audit_sha256': file_hash(WORK / 'audits/audit_live_protocol_costs.py'),
        'recovery_validator_sha256': RECOVERY_SHA, 'launch_plan_sha256': sha(plan_raw),
        'frozen_source_hash': plan['source_hash'], 'frozen_engine_hash': plan['engine_hash'],
        'frozen_schedule': manifest_evidence, 'unique_scheduled_slot_identities': len(identities), 'common_tasks_hash': shared_tasks,
        'models_selected_for_response_audit': sorted(selected), 'complete_models': complete_models,
        'generations_audited': sum(s['generations_audited'] for s in summaries), 'unique_provider_response_ids': len(response_ids),
        'priced_generations': sum(s['priced_generations'] for s in summaries), 'observed_api_token_estimate_usd': format(grand, 'f'),
        'http_520_recovery': http520, 'per_model': summaries, 'cloud_compute': cloud_costs(started),
        'attempt_evidence_sha256': file_hash(attempt_path), 'anomalies': [],
        'file_snapshot_sha256': file_hash(destination / 'file-snapshot.json'),
        'generation_capture_files_rehashed_at_end': len(audited_files),
        'v2_verification_scope': 'Filename-count-only in this audit. Proof, graph and phase provenance approval requires the separately validated per-model reports and root final cloud/local immutable-file hash audit.',
        'scope': 'Frozen slot identities, request/capture/generation binding, unique captured provider outcomes, no requests after captured or recorded responses, exact token-price accounting and cost exports. V2 records are counted only; final proof/graph/provenance and cloud/local hash validation are separate audits.',
        'cost_limitations': ['Reported-token estimates at frozen rates are not invoices.',
            'Failed, refused and truncated generations are priced when usage is reported.',
            'Reasoning/thinking token subsets are not charged a second time.',
            'Unreported failed-request/retry charges, including HTTP 520, are unknown and excluded, not zero.',
            'Cloud allocation keeps accruing; disk, IP, transfers, billing transitions, taxes, credits and adjustments are not a complete invoice.'],
        'model_calls_by_auditor': 0, 'lean_calls_by_auditor': 0, 'network_calls_by_auditor': 0,
        'run_records_modified': False, 'cloud_resources_modified': False}
    write(destination / 'summary.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', action='append', choices=sorted(MODELS), help='Optional snapshot subset; all schedules are still checked.')
    parser.add_argument('--require-complete', action='store_true', help='Fail unless all 9600 responses, v2 files and cost reports are settled.')
    args = parser.parse_args()
    destination = HERE / ('audit-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ'))
    destination.mkdir(mode=0o700)
    try:
        result = audit(args, destination)
    except Exception as exc:
        write(destination / 'failure.json', {'status': 'fail', 'error_type': type(exc).__name__, 'error': str(exc),
                                           'recorded_at': utcnow(), 'run_files_modified': False})
        print(json.dumps({'status': 'fail', 'evidence': str(destination), 'error': str(exc)}), flush=True)
        return 1
    print(json.dumps({'status': result['status'], 'evidence': str(destination), 'generations_audited': result['generations_audited'],
                      'cohort_complete': result['cohort_complete'], 'observed_api_token_estimate_usd': result['observed_api_token_estimate_usd']}), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
