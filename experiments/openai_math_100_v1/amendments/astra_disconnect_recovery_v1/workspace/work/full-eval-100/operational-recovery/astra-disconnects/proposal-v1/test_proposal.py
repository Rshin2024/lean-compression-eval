"""Offline fixtures only. Never operate on the real held markers or controls."""
import copy
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from contract import EXACT_IDS, authorize, inspect_sequence, exception_projection, digest, reason_for
import recovery_helper as helper
from common import WORK

spec = importlib.util.spec_from_file_location('frozen_audit_for_tests', WORK / 'final-audit/audit_final_cohort.py')
base = importlib.util.module_from_spec(spec); spec.loader.exec_module(base)


def fixture():
    attempts = []
    slots = {}
    for i, aid in enumerate(sorted(EXACT_IDS)):
        slot = {'attempt_id': aid, 'model_snapshot': 'gpt-6-astra'}; slots[aid] = slot
        marker = {'attempt_id': aid, 'started_at': '2026-10-14T09:00:00+00:00'}
        original = [{'attempt_id': aid, 'event': 'request_started', 'transport_index': 0,
                     'request_hash': 'request-hash', 'recorded_at': '2026-10-14T09:00:01+00:00'},
                    {'attempt_id': aid, 'event': 'outcome_unknown', 'transport_index': 0,
                     'type': 'ConnectionResetError', 'message': '[Errno 54] Connection reset by peer',
                     'recorded_at': '2026-10-14T09:01:00+00:00'}]
        attempts.append({'attempt_id': aid, 'marker': marker, 'slot_hash': digest(slot), 'request_hash': 'request-hash',
                         'original_transport_events': original, 'original_transport_events_hash': digest(original)})
    proposal = {'created_at_utc': '2026-10-14T09:02:00+00:00', 'authorization_not_before_utc': '2026-10-14T09:02:00+00:00', 'attempts': attempts}
    approval = {'status': 'approved_by_user', 'proposal_sha256': 'proposal-sha',
                'scope': 'release_exact_six_unknown_astra_outcomes_once_and_resume_original_shards',
                'attempt_ids': sorted(EXACT_IDS), 'acknowledges_possible_original_charges': True,
                'unknown_outcome_release_limit_per_attempt': 1, 'approved_at_utc': '2026-10-14T09:03:00+00:00',
                'trusted_user_evidence': {'source': 'human_user_message', 'message_reference': 'offline-fixture-only', 'exact_answer': 'offline fixture approval'}}
    return proposal, approval, slots


def released(binding):
    aid = binding['attempt_id']; reason = reason_for(binding)
    amendment = {'kind': 'in_flight_reconciliation', 'attempt_id': aid, 'resolution': 'released_without_model_outcome',
                 'marker': binding['marker'], 'reason': reason, 'capture': None, 'recorded_at': '2026-10-14T09:04:00+00:00'}
    release = {'attempt_id': aid, 'event': 'no_model_outcome', 'capture': None, 'last_error': None,
               'provider_response': None, 'prompt_rejected': False, 'reconciliation': {'marker': binding['marker'], 'reason': reason},
               'recorded_at': '2026-10-14T09:04:01+00:00'}
    retry = {'attempt_id': aid, 'event': 'request_started', 'request_hash': 'request-hash', 'transport_index': 0,
             'recorded_at': '2026-10-14T09:05:00+00:00'}
    received = {'attempt_id': aid, 'event': 'response_received', 'transport_index': 0, 'recorded_at': '2026-10-14T09:06:00+00:00'}
    return binding['original_transport_events'] + [release, retry, received], amendment


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.proposal, self.approval, self.slots = fixture(); self.binding = self.proposal['attempts'][0]

    def test_pending_is_not_approval(self):
        with self.assertRaisesRegex(ValueError, 'Actual user approval'):
            authorize(self.proposal, 'proposal-sha', {'status': 'pending_user_approval'})

    def test_exact_scope_authorized(self):
        authorize(self.proposal, 'proposal-sha', self.approval)

    def test_wrong_proposal_or_ids_or_billing_rejected(self):
        for key, value in [('proposal_sha256', 'wrong'), ('attempt_ids', []), ('acknowledges_possible_original_charges', False)]:
            altered = {**self.approval, key: value}
            with self.subTest(key=key), self.assertRaises(ValueError):
                authorize(self.proposal, 'proposal-sha', altered)

    def test_held_and_amendment_only_states(self):
        _, amendment = released(self.binding)
        self.assertEqual(inspect_sequence(self.binding, self.binding['original_transport_events'], [], self.approval), 'held')
        self.assertEqual(inspect_sequence(self.binding, self.binding['original_transport_events'], [amendment], self.approval), 'amendment_only')

    def test_exact_stock_release_accepted(self):
        sequence, amendment = released(self.binding)
        self.assertEqual(inspect_sequence(self.binding, sequence, [amendment], self.approval), 'released')

    def test_prefix_or_marker_tampering_rejected(self):
        sequence, amendment = released(self.binding)
        sequence[0] = {**sequence[0], 'request_hash': 'changed'}
        with self.assertRaisesRegex(ValueError, 'prefix changed'):
            inspect_sequence(self.binding, sequence, [amendment], self.approval)
        sequence, amendment = released(self.binding)
        amendment['marker'] = {'wrong': True}
        with self.assertRaisesRegex(ValueError, 'amendment differs'):
            inspect_sequence(self.binding, sequence, [amendment], self.approval)

    def test_missing_or_duplicate_release_amendment_rejected(self):
        sequence, amendment = released(self.binding)
        for values in ([], [amendment, amendment]):
            with self.assertRaises(ValueError):
                inspect_sequence(self.binding, sequence, values, self.approval)

    def test_release_before_user_approval_rejected(self):
        sequence, amendment = released(self.binding)
        amendment['recorded_at'] = '2026-10-14T09:02:30+00:00'
        with self.assertRaisesRegex(ValueError, 'precedes user approval'):
            inspect_sequence(self.binding, sequence, [amendment], self.approval)

    def test_replacement_unknown_stays_held(self):
        sequence, amendment = released(self.binding)
        sequence[-1] = {**sequence[-1], 'event': 'outcome_unknown'}
        with self.assertRaisesRegex(ValueError, 'Replacement outcome is unknown'):
            inspect_sequence(self.binding, sequence, [amendment], self.approval)

    def test_projection_retains_full_original_events_for_downstream_checks(self):
        events, amendments = [], []
        for b in self.proposal['attempts']:
            sequence, amendment = released(b); events.extend(sequence); amendments.append(amendment)
        (by_id, pending), disclosures = exception_projection(events, self.slots, self.proposal, self.approval, amendments, base.audit_events)
        self.assertFalse(pending); self.assertEqual(len(disclosures), 6)
        self.assertEqual(by_id[self.binding['attempt_id']], events[:5])

    def test_request_after_received_response_still_rejected(self):
        events, amendments = [], []
        for b in self.proposal['attempts']:
            sequence, amendment = released(b); events.extend(sequence); amendments.append(amendment)
        events.append({**events[3], 'recorded_at': '2026-10-14T09:07:00+00:00'})
        with self.assertRaisesRegex(ValueError, 'Request after received'):
            exception_projection(events, self.slots, self.proposal, self.approval, amendments, base.audit_events)

    def test_unlisted_unknown_retry_still_rejected(self):
        slot = {'outsider': {'model_snapshot': 'gpt-6-astra'}}
        seq = [{**x, 'attempt_id': 'outsider'} for x in self.binding['original_transport_events']]
        seq.append({**seq[0], 'recorded_at': '2026-10-14T09:07:00+00:00'})
        with self.assertRaisesRegex(ValueError, 'unresolved request'):
            exception_projection(seq, slot, self.proposal, self.approval, [], base.audit_events)

    def test_apply_without_approval_does_not_write(self):
        with patch.object(helper, 'load') as mocked:
            with self.assertRaisesRegex(ValueError, 'approval file'):
                helper.apply(None)
            mocked.assert_not_called()

    def test_approval_can_arrive_during_implementation_after_disclosure(self):
        self.proposal['created_at_utc'] = '2026-10-14T09:10:00+00:00'
        authorize(self.proposal, 'proposal-sha', self.approval)

    def test_new_capture_blocks_release_before_stock_call(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory); (run / 'inflight').mkdir(); (run / 'captures').mkdir()
            aid = self.binding['attempt_id']; marker = run / 'inflight' / (aid + '.json')
            marker.write_text(json.dumps(self.binding['marker'])); self.binding['marker_sha256'] = helper.file_hash(marker)
            (run / 'transport.jsonl').write_text(''.join(json.dumps(e) + '\n' for e in self.binding['original_transport_events']))
            (run / 'captures' / (aid + '.jsonl')).write_text('new capture evidence')
            with patch.object(helper, 'RUN', run), patch.object(helper, '_reconcile') as stock:
                with self.assertRaisesRegex(ValueError, 'Saved capture appeared'):
                    helper.release_locked(self.binding, {'slots': []}, self.approval)
                stock.assert_not_called()

    def test_changed_marker_blocks_release_before_stock_call(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory); (run / 'inflight').mkdir()
            aid = self.binding['attempt_id']; marker = run / 'inflight' / (aid + '.json')
            marker.write_text(json.dumps(self.binding['marker'])); self.binding['marker_sha256'] = helper.file_hash(marker)
            marker.write_text(json.dumps({**self.binding['marker'], 'started_at': '2026-10-14T09:30:00+00:00'}))
            (run / 'transport.jsonl').write_text(''.join(json.dumps(e) + '\n' for e in self.binding['original_transport_events']))
            with patch.object(helper, 'RUN', run), patch.object(helper, '_reconcile') as stock:
                with self.assertRaisesRegex(ValueError, 'Original marker'):
                    helper.release_locked(self.binding, {'slots': []}, self.approval)
                stock.assert_not_called()

    def test_stock_reconcile_release_is_idempotent_under_real_lock_on_temp_fixture(self):
        # Only temporary files. Actual frozen stock code is exercised, no network.
        from lean_compression.io import _abandoned, file_hash
        from lean_compression.providers import _reconcile, _transport_evidence
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory); (run / 'inflight').mkdir()
            aid = self.binding['attempt_id']; marker = run / 'inflight' / (aid + '.json')
            slot = {'attempt_id': aid, 'model_snapshot': 'gpt-6-astra', 'messages': []}
            settings = {'max_output_tokens': 8192}
            marker.write_text(json.dumps(self.binding['marker']))
            (run / 'transport.jsonl').write_text(''.join(json.dumps(e) + '\n' for e in self.binding['original_transport_events']))
            lock = _abandoned(marker, protocol=False); self.assertIsNotNone(lock)
            try:
                result = _reconcile(run, marker, slot, settings, _transport_evidence(run, {aid})[aid], apply=True, release_unreadable=True)
                self.assertEqual(result['action'], 'release')
            finally:
                os.close(lock)
            amendments = helper.events(run / 'amendments.jsonl'); seq = helper.events(run / 'transport.jsonl')
            self.assertEqual(len(amendments), 1); self.assertEqual(len(seq), 3); self.assertFalse(marker.exists())
            # Crash after log append but before unlink: stock completes only unlink.
            marker.write_text(json.dumps(self.binding['marker'])); lock = _abandoned(marker, protocol=False)
            before = file_hash(run / 'amendments.jsonl'), file_hash(run / 'transport.jsonl')
            try:
                result = _reconcile(run, marker, slot, settings, _transport_evidence(run, {aid})[aid], apply=True, release_unreadable=True)
                self.assertEqual(result['action'], 'remove_marker')
            finally:
                os.close(lock)
            self.assertEqual(before, (file_hash(run / 'amendments.jsonl'), file_hash(run / 'transport.jsonl')))


if __name__ == '__main__':
    unittest.main()
