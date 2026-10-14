"""Offline lexical and fixture checks; no Lean builds or model calls."""
import json
from pathlib import Path
import re
import unittest

import extract_scoped_tasks as extraction


class ScopedExtractionTests(unittest.TestCase):
    def test_comments_strings_and_quoted_names_do_not_contribute_delimiters(self):
        text = 'lemma t (s : String := ":= )") /- outer /- : -/ := -/ : True := by trivial'
        parts = extraction.split_reference(text)
        self.assertEqual(parts["binders"], '(s : String := ":= )") /- outer /- : -/ := -/')
        self.assertEqual(parts["proposition"], 'True')
        self.assertEqual(parts["proof"], 'by trivial')
        self.assertEqual(len(extraction.mask_lean(text)), len(text))
        self.assertEqual(extraction.top_tokens(extraction.mask_lean('«has:colon» : P'), ':'), [12])

    def test_implicit_and_nested_binder_assignments(self):
        text = 'theorem t ⦃α : Type⦄ (h : ∀ x : α, True) (n : Nat := 0) : True := by trivial'
        parts = extraction.split_reference(text)
        self.assertEqual(parts['proposition'], 'True')
        self.assertIn('n : Nat := 0', parts['binders'])

    def test_hash_bound_statement_can_contain_top_level_let_assignment(self):
        statement = 'theorem t : let n := 1; n = 1'
        parts = extraction.split_reference(statement + ' := by rfl', statement)
        self.assertEqual(parts['proposition'], 'let n := 1; n = 1')
        self.assertEqual(parts['proof'], 'by rfl')

    def test_raw_strings_nested_comments_and_characters_are_masked(self):
        source = 'r#"not a ) := theorem"# /- first /- second -/ last -/\n\'x\' : True'
        masked = extraction.mask_lean(source)
        self.assertNotIn('theorem', masked)
        self.assertEqual(masked.count('\n'), source.count('\n'))
        self.assertEqual(len(extraction.top_tokens(masked, ':')), 1)

    def test_malformed_lexical_constructs_fail(self):
        for source in ('/- open', '"open', '«open', 'r#"open'):
            with self.subTest(source=source), self.assertRaises(ValueError):
                extraction.mask_lean(source)

    def test_imports_come_from_source_and_keep_flags_and_order(self):
        source = 'module\npublic meta import all A.B\nimport C.D E.F -- note\nnamespace OAI\nvariable {R : Type}\n'
        specs, context, notes = extraction.imports_and_context(source)
        self.assertEqual([s['module'] for s in specs], ['A.B', 'C.D', 'E.F'])
        self.assertEqual(specs[0], {'module': 'A.B', 'is_exported': True, 'is_meta': True, 'import_all': True})
        self.assertNotIn('import ', extraction.mask_lean(context))
        self.assertIn('namespace OAI', context)
        self.assertIn('variable {R : Type}', context)

    def test_target_attributes_documentation_and_local_wrapper(self):
        prefix = 'import Mathlib\nnamespace OAI\nlemma previous : True := by trivial\nomit [Finite V] in\n/-- target docs -/\n@[simp]\n'
        specs, context, notes = extraction.imports_and_context(prefix)
        self.assertTrue(context.endswith('omit [Finite V] in'))
        self.assertIn('lemma previous : True := by trivial', context)
        self.assertNotIn('target docs', context)
        self.assertNotIn('@[simp]', context)
        self.assertEqual(notes, ['target_attribute_removed'])

    def test_no_preceding_proofs_even_with_indentation_and_attributes(self):
        source = '''namespace OAI
variable {R : Type} [Finite R]
@[simp] theorem a : True := by trivial
omit [Finite R] in
 theorem b : True := by
  exact a
def value : Nat := 2
def hidden : Nat := by exact 2
open scoped BigOperators in
'''
        premise, info = extraction.public_interfaces(source, 'theorem t : True')
        self.assertIn('theorem a : True', premise)
        self.assertIn('theorem b : True', premise)
        self.assertIn('def value : Nat := 2', premise)
        self.assertIn('def hidden : Nat', premise)
        self.assertNotRegex(premise, r'\b(?:by|trivial|exact)\b')
        self.assertEqual(info['target_local_wrappers'], ['open scoped BigOperators in'])

    def test_definition_where_and_equation_bodies_do_not_leak_embedded_proofs(self):
        source = '''namespace OAI
def functor : Nat →+ Nat where
  toFun n := n
  map_zero' := by rfl
  map_add' := by intros; rfl
def thing : Fin 3 → Fin 3
  | _ => ⟨0, by decide⟩
theorem typed : (⟨0, by decide⟩ : Fin 3) = 0 := by rfl
'''
        premise, info = extraction.public_interfaces(source, 'theorem t : True')
        self.assertIn('def functor : Nat →+ Nat', premise)
        self.assertNotIn('toFun', premise)
        self.assertNotIn('theorem typed', premise)
        self.assertNotRegex(premise, r'\b(?:by|decide|rfl)\b')

    def test_scope_parameters_survive_for_interfaces_from_closed_scopes(self):
        source = '''namespace Outer
section Inner
variable (M : Nat)
def z : Nat := M
end Inner
theorem a : True := by trivial
'''
        premise, info = extraction.public_interfaces(source, 'theorem t : True')
        self.assertIn('-- Source parameters: variable (M : Nat)', premise)
        self.assertEqual(info['source_namespace'], 'Outer')

    def test_byte_bound_preserves_whole_interfaces(self):
        source = 'namespace OAI\n' + '\n'.join(f'theorem t{i} : True := by trivial' for i in range(30))
        premise, info = extraction.public_interfaces(source, 'theorem goal : True', max_bytes=600)
        self.assertLessEqual(len(premise.encode()), 600)
        self.assertGreater(info['omitted_local_interface_count'], 0)
        self.assertNotIn('trivial', premise)

    def test_all_attached_candidates_extract_with_no_target_or_public_proof_body(self):
        bundle = extraction.DEFAULT_BUNDLE
        if not bundle.exists():
            self.skipTest('Adopted source bundle not present')
        rows = extraction.read_jsonl(bundle / 'selection.jsonl')
        refs = {r['task_id']: r for r in extraction.read_jsonl(bundle / 'private/references.jsonl')}
        self.assertEqual(len(rows), 100)
        for row in rows:
            with self.subTest(task=row['task_id']):
                task, reference, diagnostic = extraction.extract_one(row, refs[row['task_id']], bundle)
                self.assertTrue(task['context'].startswith('set_option autoImplicit false\n'))
                self.assertFalse(task['model_generation_enabled'])
                self.assertFalse(any(task['audit'].values()))
                self.assertEqual(task['split'], 'main')
                self.assertEqual(task['domain'], row['selection_group'])
                self.assertNotIn(refs[row['task_id']]['reference_declaration'], task['context'])
                self.assertNotRegex(extraction.mask_lean(task['premise_context']), r'\b(?:by|sorry|admit)\b')
                self.assertLessEqual(diagnostic['premise_bytes'], 24000)
                self.assertEqual(extraction.sha(reference['proof']), reference['proof_sha256'])


if __name__ == '__main__':
    unittest.main()
