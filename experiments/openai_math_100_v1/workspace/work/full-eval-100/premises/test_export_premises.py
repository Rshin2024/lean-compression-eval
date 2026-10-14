"""Offline tests. Lean fixtures use only Init and do not load OAI references."""
import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import export_premises as exporter


LEAN = Path('/Users/ryanshin/.elan/toolchains/leanprover--lean4---v4.34.1/bin/lean')
HERE = Path(__file__).resolve().parent
VALIDATION = {'max_heartbeats': 2_000_000, 'permitted_axioms': ['propext', 'Quot.sound', 'Classical.choice']}


def task():
    return {'task_id': 'synthetic', 'imports': ['Init'], 'context': '',
            'target_name': '_root_.CompressionEval.synthetic', 'binders': '(n : Nat)',
            'proposition': '(OAI.Test.boxed n).val = n', 'forbidden_declarations': []}


def run_lean(request, support=None):
    with tempfile.TemporaryDirectory(prefix='synthetic-', dir=HERE) as tmp:
        root = Path(tmp)
        (root/'request.json').write_text(json.dumps(request))
        env = {**os.environ, 'PATH': str(LEAN.parent)+':/usr/bin:/bin'}
        if support is not None:
            (root/'OAI.lean').write_text(support)
            compiled = subprocess.run([str(LEAN), '-o', str(root/'OAI.olean'),
                                       str(root/'OAI.lean')], text=True, capture_output=True,
                                      timeout=30, env=env)
            if compiled.returncode:
                raise AssertionError(compiled.stdout+'\n'+compiled.stderr)
            env['LEAN_PATH'] = str(root)
        result = subprocess.run([str(LEAN), '--run', str(exporter.ENGINE),
                                 str(root/'request.json'), str(root/'result.json')],
                                text=True, capture_output=True, timeout=45, env=env)
        if result.returncode:
            raise AssertionError(result.stdout+'\n'+result.stderr)
        return json.loads((root/'result.json').read_text())


class PremiseRequestTests(unittest.TestCase):
    def test_source_scopes_are_preserved_and_target_wrapper_lifted(self):
        source = task()
        source['context'] = 'namespace OAI\nvariable {V : Type} [Finite V]\nomit [Finite V] in'
        request, changes = exporter.request_for(source, 'by rfl', VALIDATION)
        self.assertIn('omit [Finite V]\n', request['context'])
        self.assertTrue(request['context'].endswith('set_option autoImplicit false\n'))
        self.assertEqual(len(changes), 1)
        self.assertEqual(request['max_context_bytes'], 64000)
        self.assertEqual(source['context'].splitlines()[-1], 'omit [Finite V] in')

    def test_unknown_wrapper_and_invalid_limits_are_rejected(self):
        source = task()
        source['context'] = 'set_option maxHeartbeats 0 in'
        with self.assertRaisesRegex(ValueError, 'unsupported_target_local'):
            exporter.request_for(source, 'by rfl', VALIDATION)
        for limit in (0, 100, 64001):
            with self.assertRaises(ValueError):
                exporter.request_for(task(), 'by rfl', VALIDATION, max_context_bytes=limit)

    def test_non_success_is_not_published_as_context(self):
        self.assertIsNone(exporter.accepted_export({'validity_status':'elaboration_failure'}, task()))

    def test_merge_preserves_materialized_signature_and_resets_premise_audit(self):
        source = task()
        source['audit'] = {'reference_verified':True, 'explicit_signature_frozen':True, 'answer_access_audited':True}
        data = {'premise_context':'theorem Prior.helper : True', 'premise_context_sha256':'fixture',
                'source_dep_names':['Prior.helper'], 'omitted_count':0}
        merged = exporter.merge_task(source, data, 'export-identity')
        self.assertEqual(merged['binders'], source['binders'])
        self.assertEqual(merged['proposition'], source['proposition'])
        self.assertEqual(merged['context'], source['context'])
        self.assertTrue(merged['audit']['reference_verified'])
        self.assertTrue(merged['audit']['explicit_signature_frozen'])
        self.assertFalse(merged['audit']['answer_access_audited'])
        self.assertFalse(merged['model_generation_enabled'])
        self.assertNotIn('premise_context', source)


@unittest.skipUnless(LEAN.exists(), 'Pinned host Lean 4.34.1 is absent')
class PremiseLeanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = task()
        cls.source['context'] = '''namespace OAI.Test
structure Box where
  val : Nat
  valid : val = val
def boxed (n : Nat) : Box := ⟨n, rfl⟩
theorem boxed_val (n : Nat) : (boxed n).val = n := rfl
end OAI.Test
'''
        cls.request, _ = exporter.request_for(cls.source, 'by exact OAI.Test.boxed_val n', VALIDATION,
                                              max_context_bytes=8000, max_constants=200)
        cls.result = run_lean(cls.request)

    def test_compiler_exports_proof_free_typed_interfaces_and_structures(self):
        result = self.result
        self.assertEqual(result['validity_status'], 'success')
        self.assertFalse(result['kernel_replay'])
        self.assertFalse(result['proof_bodies_exported'])
        data = result['export']
        text = data['premise_context']
        self.assertIn('def OAI.Test.boxed : Nat → OAI.Test.Box', text)
        self.assertIn('valid := ⋯', text)
        self.assertIn('theorem OAI.Test.boxed_val : ∀ (n : Nat)', text)
        self.assertIn('constructor OAI.Test.Box.mk', text)
        self.assertNotIn(':= rfl', text)
        self.assertNotIn('by exact', text)
        self.assertNotIn('CompressionEval.synthetic', text)
        self.assertEqual(data['interface_error_count'], 0)
        self.assertTrue(all(e['interface']['present_before_target'] for e in data['interfaces']))
        for entry in data['interfaces']:
            info = entry['interface']
            if info['owner_module'] != 'EvaluatorCandidate' and not info['owner_module'].startswith('OAI'):
                self.assertFalse(info['definition_body_included'])
                self.assertNotEqual(entry['origin'], 'interface_dependency')
        self.assertEqual(data['external_declarations'], 'direct_signatures_only')
        self.assertEqual(data['transitive_expansion'], 'OAI_and_source_local_only')
        validated = exporter.accepted_export(result, self.source, max_context_bytes=8000)
        self.assertEqual(validated['context_bytes'], len(text.encode()))

    def test_unqualified_target_receives_compiler_resolved_namespace_and_opens(self):
        request = {**self.request, 'context':self.source['context'].removesuffix('end OAI.Test\n') + '''open Nat
namespace ScopeDemo
scoped notation "ScopedNat" => Nat
end ScopeDemo
open scoped ScopeDemo
''',
                   'proposition':'(boxed n).val = n', 'proof':'by exact boxed_val n'}
        result = run_lean(request)
        self.assertEqual(result['validity_status'], 'success')
        self.assertEqual(result['source_scope_snapshot']['current_namespace'], 'OAI.Test')
        self.assertIn('open Nat', result['source_scope_snapshot']['resolved_open_declarations'])
        self.assertIn('OAI.Test.ScopeDemo', result['source_scope_snapshot']['active_scoped_namespaces'])
        self.assertIn('Active source namespace for the written target: OAI.Test', result['export']['premise_context'])

    def test_result_validator_rejects_target_new_declarations_and_proof_claims(self):
        for mode in ('target', 'new', 'proof', 'replay', 'cap'):
            result = copy.deepcopy(self.result)
            if mode == 'target':
                result['export']['source_dep_names'][0] = 'CompressionEval.synthetic'
                result['export']['interfaces'][0]['interface']['name'] = 'CompressionEval.synthetic'
            elif mode == 'new':
                result['export']['interfaces'][0]['interface']['present_before_target'] = False
            elif mode == 'proof':
                result['proof_bodies_exported'] = True
            elif mode == 'replay':
                result['kernel_replay'] = True
            else:
                result['export']['context_bytes'] += 1
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                exporter.accepted_export(result, self.source, max_context_bytes=8000)

    def test_context_cap_omits_whole_interfaces_deterministically(self):
        request = {**self.request, 'max_context_bytes': 512}
        first = run_lean(request)
        second = run_lean(request)
        self.assertEqual(first, second)
        self.assertLessEqual(first['export']['context_bytes'], 512)
        self.assertGreater(first['export']['omitted_count'], 0)
        self.assertEqual(first['export']['context_bytes'], len(first['export']['premise_context'].encode()))

    def test_new_reference_auxiliaries_are_not_model_premises(self):
        request = {**self.request, 'context':'', 'proposition':'n = n',
                   'proof': '''by
  let rec referenceOnlyAux (k : Nat) : k = k := by
    cases k with
    | zero => rfl
    | succ j => exact congrArg Nat.succ (referenceOnlyAux j)
  exact referenceOnlyAux n'''}
        result = run_lean(request)
        self.assertEqual(result['validity_status'], 'success')
        self.assertGreater(result['excluded_post_target_auxiliary_visits'], 0)
        self.assertNotIn('referenceOnlyAux', result['export']['premise_context'])
        self.assertTrue(all('referenceOnlyAux' not in n and 'CompressionEval.synthetic' not in n
                            for n in result['export']['source_dep_names']))

    def test_large_nonproof_definition_body_is_bounded_before_rendering(self):
        values = ', '.join(str(i) for i in range(300))
        request = {**self.request, 'context':f'namespace OAI.Test\ndef longList : List Nat := [{values}]\n',
                   'proposition':'longList = longList', 'proof':'by rfl'}
        result = run_lean(request)
        self.assertEqual(result['validity_status'], 'success')
        item = next(e['interface'] for e in result['export']['interfaces']
                    if e['interface']['name'] == 'OAI.Test.longList')
        self.assertFalse(item['definition_body_included'])
        self.assertEqual(item['body_omission_reason'], 'definition_body_exceeds_1000_expression_nodes')
        self.assertIn('def OAI.Test.longList : List.{0} Nat', result['export']['premise_context'])

    def test_admitted_reference_is_rejected(self):
        result = run_lean({**self.request, 'proof':'by sorry'})
        self.assertEqual(result['validity_status'], 'disallowed_axiom')
        self.assertNotIn('export', result)

    def test_imported_private_aliases_are_not_advertised_as_available_names(self):
        support = '''namespace OAI.Imported
private def hidden (n : Nat) : Nat := n
def wrapper (n : Nat) : Nat := hidden n
end OAI.Imported
'''
        request = {**self.request, 'imports':['OAI'], 'context':'',
                   'proposition':'OAI.Imported.wrapper n = n', 'proof':'by rfl'}
        result = run_lean(request, support)
        self.assertEqual(result['validity_status'], 'success')
        self.assertNotIn('OAI.Imported.hidden', result['export']['premise_context'])
        self.assertTrue(all(not n.startswith('_private') for n in result['export']['source_dep_names']))
        self.assertGreater(result['export']['omitted_imported_private_count'], 0)


if __name__ == '__main__':
    unittest.main()
