"""Reporting derivations must preserve frozen text and never invent proof lengths."""
import copy
import hashlib
import json
import unittest

from source_lengths import measure_report, prompts, digest, json_text, VERSION


def serialized_sha(value):
    return hashlib.sha256(json_text(value).encode('utf-8')).hexdigest()


def fixture(raw='by\n  exact h', *, response_status='completed', status='success', metric_status=None):
    model = 'fixture-model'
    settings = {'model_snapshot': model, 'response_adapter': 'single_outer_wrapper_v2'}
    config = {'generation': settings}
    messages = [{'role': 'system', 'content': 'test'}, {'role': 'user', 'content': 'prove True'}]
    slot = {'attempt_id': 'fixture-attempt', 'model_snapshot': model, 'config_hash': digest(config),
            'messages': messages, 'prompt_hash': digest(messages), 'task_id': 'task-1',
            'condition': 'standard', 'sample_index': 0, 'schedule_index': 0}
    manifest = {'config': config, 'config_hash': digest(config), 'model_snapshot': model,
                'slots': [slot], 'scheduled_attempts': 1}
    manifest['run_hash'] = digest(manifest)
    generation = {'attempt_id': slot['attempt_id'], 'prompt_hash': slot['prompt_hash'],
                  'raw_response': raw, 'raw_response_hash': digest(raw), 'generation_status': response_status,
                  'returned_model': model, 'generation_settings': settings, 'created_at': 'fixture'}
    metric_status = metric_status or ('available' if status == 'success' else 'unavailable')
    verification = {'validity_status': status, 'metric_status': metric_status}
    if response_status == 'completed':
        adapted = prompts.adapt_response(raw, settings['response_adapter'])
        verification.update(adapted_response=adapted, source_hash=digest(adapted))
    row = {'attempt_id': slot['attempt_id'], 'record_kind': 'phase_separated_verified_attempt',
           'generation': generation, 'generation_hash': digest(generation),
           'generation_file_sha256': serialized_sha(generation),
           'generation_identity': {k: v for k, v in slot.items() if k != 'messages'},
           'source_run_hash': manifest['run_hash'], 'source_manifest_sha256': serialized_sha(manifest),
           'verification_result': verification, 'checker_executed': response_status == 'completed',
           'validity_status': status, 'metric_status': metric_status}
    return [row], manifest, model


def resign_generation(row):
    row['generation_hash'] = digest(row['generation'])
    row['generation_file_sha256'] = serialized_sha(row['generation'])


class SourceLengthTests(unittest.TestCase):
    def measure(self, *args, **kwargs):
        rows, manifest, model = fixture(*args, **kwargs)
        result = measure_report(rows, manifest, model)
        return result['byAttempt']['fixture-attempt'], result['policy']

    def test_non_bmp_unicode_whitespace_and_utf8_are_distinct(self):
        value, policy = self.measure('𝕜\u00a0x\n')
        self.assertEqual(value['rawCharacters'], 4)  # Not JavaScript's UTF-16 length5.
        self.assertEqual(value['sourceCharacters'], 4)
        self.assertEqual(value['sourceNonWhitespaceCharacters'], 2)
        self.assertEqual(value['sourceUtf8Bytes'], 8)
        self.assertEqual(value['sourceLines'], 1)
        self.assertEqual(policy['version'], VERSION)
        self.assertTrue(policy['unicodeVersion'])

    def test_comments_and_failed_prose_are_retained(self):
        text = 'This is not Lean. -- comment\n'
        value, _ = self.measure(text, status='parse_failure')
        self.assertEqual(value['sourceCharacters'], len(text))
        self.assertEqual(value['sourceTextSha256'], hashlib.sha256(text.encode()).hexdigest())
        self.assertEqual(value['sourceKind'], 'submitted_text')
        self.assertEqual(value['status'], 'parse_failure')
        self.assertTrue(value['available'])

    def test_outer_wrapper_only_is_removed(self):
        raw = ' \n```lean\nby\n  exact h\n```\n'
        value, _ = self.measure(raw)
        expected = 'by\n  exact h'
        self.assertTrue(value['wrapperRemoved'])
        self.assertEqual(value['sourceCharacters'], len(expected))
        self.assertEqual(value['rawCharacters'], len(raw))
        self.assertEqual(value['sourceLines'], 2)

    def test_prose_around_fence_is_not_heuristically_extracted(self):
        raw = 'Here is the proof:\n```lean\nby exact h\n```'
        value, _ = self.measure(raw, status='parse_failure')
        self.assertFalse(value['wrapperRemoved'])
        self.assertEqual(value['sourceCharacters'], len(raw))

    def test_truncated_nonempty_text_is_partial_and_not_reverified(self):
        raw = '```lean\nby\n  exact'
        value, _ = self.measure(raw, response_status='truncated_output', status='truncated_output')
        self.assertEqual(value['sourceKind'], 'partial_saved_text')
        self.assertTrue(value['truncated'])
        self.assertTrue(value['available'])
        self.assertFalse(value['wrapperRemoved'])
        self.assertEqual(value['sourceCharacters'], len(raw))

    def test_empty_and_whitespace_source_are_unavailable_not_zero(self):
        for raw in ['', ' \t\n\u00a0']:
            with self.subTest(raw_empty=not raw):
                value, _ = self.measure(raw, response_status='truncated_output', status='truncated_output')
                self.assertFalse(value['available'])
                self.assertEqual(value['rawCharacters'], len(raw))
                for field in ('sourceCharacters', 'sourceNonWhitespaceCharacters', 'sourceUtf8Bytes', 'sourceLines'):
                    self.assertIsNone(value[field])
                self.assertEqual(value['unavailableReason'], 'empty_or_whitespace_only_text')

    def test_empty_completed_input_is_not_a_zero_length_proof(self):
        value, _ = self.measure('', status='parse_failure')
        self.assertFalse(value['available'])
        self.assertEqual(value['sourceKind'], 'submitted_text')
        self.assertIsNone(value['sourceCharacters'])

    def test_all_completed_failure_types_can_be_measured(self):
        for status in ['parse_failure', 'elaboration_failure', 'verification_timeout', 'disallowed_axiom',
                       'output_format_failure', 'verification_resource_limit', 'target_mismatch']:
            with self.subTest(status=status):
                value, _ = self.measure('by exact h', status=status)
                self.assertTrue(value['available'])
                self.assertEqual(value['status'], status)

    def test_valid_proof_without_expansion_metrics_still_has_source_length(self):
        value, _ = self.measure(metric_status='unavailable')
        self.assertTrue(value['available'])
        self.assertEqual(value['sourceKind'], 'submitted_text')

    def test_no_raw_text_or_input_mutation(self):
        rows, manifest, model = fixture('by exact PRIVATE_SENTINEL_SOURCE')
        before = copy.deepcopy((rows, manifest))
        result = measure_report(rows, manifest, model)
        self.assertNotIn('PRIVATE_SENTINEL_SOURCE', json.dumps(result))
        self.assertEqual((rows, manifest), before)

    def test_missing_duplicate_and_foreign_slots_rejected(self):
        rows, manifest, model = fixture()
        for changed in [[], rows + rows, [{**rows[0], 'attempt_id': 'foreign'}]]:
            with self.assertRaises(ValueError):
                measure_report(changed, manifest, model)

    def test_manifest_and_model_bindings_rejected(self):
        rows, manifest, model = fixture()
        with self.assertRaisesRegex(ValueError, 'model identity'):
            measure_report(rows, manifest, 'different-model')
        manifest['scheduled_attempts'] = 2
        with self.assertRaisesRegex(ValueError, 'run_hash'):
            measure_report(rows, manifest, model)

    def test_generation_canonical_hash_and_file_hash_both_checked(self):
        for field in ['generation_hash', 'generation_file_sha256', 'source_manifest_sha256']:
            rows, manifest, model = fixture()
            rows[0][field] = '0' * 64
            with self.subTest(field=field), self.assertRaises(ValueError):
                measure_report(rows, manifest, model)

    def test_prompt_identity_and_raw_hash_are_checked(self):
        for change in ['prompt', 'identity', 'raw_hash']:
            rows, manifest, model = fixture(); row = rows[0]
            if change == 'prompt':
                row['generation']['prompt_hash'] = '0' * 64; resign_generation(row)
            elif change == 'identity':
                row['generation_identity']['sample_index'] = 9
            else:
                row['generation']['raw_response_hash'] = hashlib.sha256(row['generation']['raw_response'].encode()).hexdigest()
                resign_generation(row)
            with self.subTest(change=change), self.assertRaises(ValueError):
                measure_report(rows, manifest, model)

    def test_saved_adapted_input_and_source_hash_must_match(self):
        for change in ['missing', 'changed', 'hash']:
            rows, manifest, model = fixture(); verification = rows[0]['verification_result']
            if change == 'missing':
                verification.pop('adapted_response')
            elif change == 'changed':
                verification['adapted_response'] = 'by trivial'
            else:
                verification['source_hash'] = '0' * 64
            with self.subTest(change=change), self.assertRaises(ValueError):
                measure_report(rows, manifest, model)

    def test_truncated_cannot_claim_checker_execution(self):
        rows, manifest, model = fixture('by exact', response_status='truncated_output', status='truncated_output')
        rows[0]['checker_executed'] = True
        with self.assertRaisesRegex(ValueError, 'Truncated response'):
            measure_report(rows, manifest, model)

    def test_mismatched_saved_partial_adapter_rejected(self):
        rows, manifest, model = fixture('by exact', response_status='truncated_output', status='truncated_output')
        rows[0]['verification_result']['adapted_response'] = 'by rfl'
        with self.assertRaisesRegex(ValueError, 'partial-text adapter'):
            measure_report(rows, manifest, model)

    def test_refusal_or_unknown_status_never_becomes_short_source(self):
        for status in ['refusal', 'malformed_response', 'provider_failure']:
            rows, manifest, model = fixture('I cannot comply.', response_status=status, status=status)
            with self.subTest(status=status), self.assertRaisesRegex(ValueError, 'Unsupported response status'):
                measure_report(rows, manifest, model)


if __name__ == '__main__':
    unittest.main()
