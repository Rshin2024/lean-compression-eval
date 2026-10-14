"""Offline guards for final audit; no provider/run mutations."""
import importlib.util
from pathlib import Path
import unittest
from decimal import Decimal
from tempfile import TemporaryDirectory

spec = importlib.util.spec_from_file_location('final_audit', Path(__file__).with_name('audit_final_cohort.py'))
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


class AuditGuards(unittest.TestCase):
    def event(self, kind, second, index=0):
        return {'attempt_id': 'slot', 'event': kind, 'transport_index': index,
                'recorded_at': f'2026-10-14T08:00:{second:02d}+00:00'}

    def test_single_response(self):
        sequence = [self.event('request_started', 1), self.event('response_received', 2)]
        self.assertEqual(audit.audit_events(sequence, {'slot': {}})[1], [])

    def test_explicit_http_nonoutcome_allows_logged_retry(self):
        sequence = [self.event('request_started', 1), self.event('http_error', 2),
                    self.event('no_model_outcome', 3), self.event('request_started', 4),
                    self.event('response_received', 5)]
        self.assertEqual(audit.audit_events(sequence, {'slot': {}})[1], [])

    def test_request_after_capture_rejected(self):
        sequence = [self.event('request_started', 1), self.event('response_received', 2),
                    self.event('request_started', 3)]
        with self.assertRaisesRegex(ValueError, 'Request after received'):
            audit.audit_events(sequence, {'slot': {}})

    def test_unknown_call_cannot_be_silently_retried(self):
        sequence = [self.event('request_started', 1), self.event('outcome_unknown', 2),
                    self.event('request_started', 3)]
        with self.assertRaisesRegex(ValueError, 'unresolved request'):
            audit.audit_events(sequence, {'slot': {}})

    def test_pending_call_remains_pending(self):
        self.assertEqual(audit.audit_events([self.event('request_started', 1)], {'slot': {}})[1], ['slot'])

    def test_unknown_attempt_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Unscheduled'):
            audit.audit_events([self.event('request_started', 1)], {})

    def test_duplicate_response_rejected(self):
        sequence = [self.event('request_started', 1), self.event('response_received', 2), self.event('response_received', 3)]
        with self.assertRaisesRegex(ValueError, 'no corresponding request'):
            audit.audit_events(sequence, {'slot': {}})

    def test_openai_cache_subsets_and_reasoning_not_double_counted(self):
        record = {'usage': {'input_tokens': 1000, 'output_tokens': 100,
                  'input_tokens_details': {'cached_tokens': 200, 'cache_write_tokens': 100},
                  'output_tokens_details': {'reasoning_tokens': 70}}}
        pricing = {'provider': 'openai', 'rates_per_million': {'input': '2', 'cache_read': '.1', 'cache_write': '2.5', 'output': '10'}}
        amount, parts = audit.independent_amount(record, pricing)
        self.assertEqual(parts['input']['tokens'], 700)
        self.assertEqual(parts['output']['tokens'], 100)
        self.assertEqual(amount, Decimal('.00267'))

    def test_anthropic_separate_buckets_and_long_context(self):
        record = {'usage': {'input_tokens': 1000, 'output_tokens': 100, 'cache_read_input_tokens': 200,
                  'cache_creation_input_tokens': 100, 'cache_creation': {'ephemeral_5m_input_tokens': 60, 'ephemeral_1h_input_tokens': 40},
                  'output_tokens_details': {'thinking_tokens': 70}}}
        pricing = {'provider': 'anthropic', 'rates_per_million': {'input': '2', 'cache_read': '.1', 'cache_write': '2.5', 'cache_write_1h': '4', 'output': '10'},
                   'long_context': {'threshold_input_tokens': 1200, 'input_multiplier': '2', 'output_multiplier': '3'}}
        amount, parts = audit.independent_amount(record, pricing)
        self.assertEqual(parts['input']['tokens'], 1000)
        self.assertEqual(parts['output']['tokens'], 100)
        self.assertEqual(amount, Decimal('.00766'))

    def test_invalid_cache_counts_rejected(self):
        record = {'usage': {'input_tokens': 100, 'output_tokens': 10,
                  'input_tokens_details': {'cached_tokens': 200, 'cache_write_tokens': 0}}}
        pricing = {'provider': 'openai', 'rates_per_million': {}}
        with self.assertRaisesRegex(ValueError, 'Invalid token'):
            audit.independent_amount(record, pricing)

    def test_unchanged_generation_capture_snapshot_passes(self):
        with TemporaryDirectory() as directory:
            paths = [Path(directory) / 'generation.json', Path(directory) / 'capture.jsonl']
            for path in paths:
                path.write_text('{"data": "original"}\n')
            expected = {str(p): audit.file_hash(p) for p in paths}
            audit.require_file_snapshot(expected)

    def test_same_name_generation_or_capture_replacement_rejected(self):
        for name in ('generation.json', 'capture.jsonl'):
            with self.subTest(name=name), TemporaryDirectory() as directory:
                path = Path(directory) / name
                path.write_text('{"data": "original"}\n')
                expected = {str(path): audit.file_hash(path)}
                replacement = Path(directory) / 'replacement.tmp'
                replacement.write_text('{"data": "replacement"}\n')
                replacement.replace(path)
                with self.assertRaisesRegex(ValueError, 'Audited file changed'):
                    audit.require_file_snapshot(expected)


if __name__ == '__main__':
    unittest.main()
