"""Synthetic result-audit tests; no Lean builds, OAI references, or APIs."""
import json
from pathlib import Path
import tempfile
import unittest

import audit_exports
import export_premises as exporter
from lean_compression.io import digest, evaluator_hash, file_hash


class ExportAuditTests(unittest.TestCase):
    def fixture(self, root):
        tasks, refs, candidates = [], [], []
        config = {'validation': {'max_heartbeats': 10000, 'permitted_axioms': []},
                  'generation': {'context_byte_cap': 64000},
                  'conditions': ['standard', 'mathematical_compression', 'source_concision'],
                  'environment': {'lean_toolchain': 'leanprover/lean4:v4.34.1'}}
        for ident in audit_exports.EXPECTED:
            proof = 'synthetic private proof body for ' + ident
            tasks.append({'task_id': ident, 'target_name': '_root_.CompressionEval.'+ident.replace('-', '_'),
                          'imports':['Init'], 'context':'', 'binders':'', 'proposition':'True',
                          'forbidden_declarations':['Original.'+ident.replace('-', '_')],
                          'audit': {'reference_verified':True, 'explicit_signature_frozen':True},
                          'provenance':{'fixture':ident}})
            refs.append({'task_id':ident, 'proof':proof, 'proof_sha256':exporter.file_text_hash(proof)})
            candidates.append({'id':ident, 'proof_summary':'A private curator derivation that is never shown for '+ident})
        paths = {k:root/(k+'.jsonl') for k in ('tasks','references','catalogue')}
        for key, rows in [('tasks',tasks),('references',refs),('catalogue',candidates)]:
            paths[key].write_text(''.join(json.dumps(r)+'\n' for r in rows))
        paths['config'] = root/'config.json'
        paths['config'].write_text(json.dumps(config))
        paths['exports'] = root/'exports'
        paths['exports'].mkdir()
        (paths['exports']/'records').mkdir()
        policy = {'version':exporter.POLICY, 'engine_sha256':file_hash(exporter.ENGINE),
                  'evaluator_source_hash':evaluator_hash(),
                  'wrapper_sha256':file_hash(Path(exporter.__file__)), 'config_hash':digest(config),
                  'tasks_file_sha256':file_hash(paths['tasks']), 'references_file_sha256':file_hash(paths['references']),
                  'selected_task_ids':audit_exports.EXPECTED, 'max_context_bytes':56000, 'max_constants':4096}
        (paths['exports']/'policy.json').write_text(json.dumps(policy))
        for task, ref in zip(tasks, refs):
            request, _ = exporter.request_for(task, ref['proof'], config['validation'], max_context_bytes=56000)
            text = 'theorem Fixture.helper : True'
            result = {'validity_status':'success', 'premise_export_status':'exported', 'policy':exporter.POLICY,
                      'proof_bodies_exported':False, 'kernel_replay':False,
                      'target':task['target_name'].removeprefix('_root_.'),
                      'source_scope_snapshot':{'source':'trusted_pre_target_command_state',
                                               'current_namespace':'_root_', 'resolved_open_declarations':[],
                                               'active_scoped_namespaces':[]},
                      'export':{'premise_context':text, 'context_bytes':len(text.encode()),
                                'source_dep_names':['Fixture.helper'], 'included_count':1, 'omitted_count':0,
                                'interface_error_count':0, 'omitted_imported_private_count':0,
                                'interfaces':[{'interface':{'name':'Fixture.helper','kind':'theorem',
                                                            'present_before_target':True, 'definition_body_included':False,
                                                            'owner_module':'Fixture'}}]}}
            record = {'task_id':task['task_id'], 'source_request_hash':digest(request),
                      'identity':digest({'policy_hash':digest(policy),'request':request,
                                         'task_source_provenance':task['provenance']}), 'result':result}
            (paths['exports']/'records'/(task['task_id']+'.json')).write_text(json.dumps(record))
        return paths

    def run_audit(self, paths, output):
        return audit_exports.audit(paths['tasks'],paths['references'],paths['exports'],paths['config'],paths['catalogue'],output)

    def test_complete_audit_preserves_all_ids_and_keeps_generation_disabled(self):
        with tempfile.TemporaryDirectory(dir=exporter.HERE) as tmp:
            root = Path(tmp)
            paths = self.fixture(root)
            report = self.run_audit(paths, root/'audited')
            self.assertEqual(report['passed_count'], 100)
            self.assertEqual(report['catalogue_input_sha256'], file_hash(paths['catalogue']))
            self.assertEqual(report['catalogue_summary_count'], 100)
            merged = exporter.jsonl(root/'audited/final_tasks_candidate.jsonl')
            self.assertEqual([t['task_id'] for t in merged], audit_exports.EXPECTED)
            self.assertTrue(all(not t['model_generation_enabled'] and not t['audit']['answer_access_audited'] for t in merged))
            self.assertTrue(all(t['audit']['reference_verified'] for t in merged))

    def test_cross_question_reference_copy_and_missing_record_are_retained_failures(self):
        with tempfile.TemporaryDirectory(dir=exporter.HERE) as tmp:
            root = Path(tmp)
            paths = self.fixture(root)
            path = paths['exports']/'records/OM100-001.json'
            record = json.loads(path.read_text())
            record['result']['export']['premise_context'] += '\nsynthetic private proof body for OM100-002'
            record['result']['export']['context_bytes'] = len(record['result']['export']['premise_context'].encode())
            path.write_text(json.dumps(record))
            (paths['exports']/'records/OM100-100.json').unlink()
            report = self.run_audit(paths, root/'audited')
            self.assertEqual(report['accounted_count'], 100)
            self.assertEqual(report['passed_count'], 98)
            self.assertIn('selected_reference_text_present:OM100-002', report['per_task'][0]['findings'])
            self.assertEqual(report['per_task'][-1]['findings'], ['missing_export_record'])

    def test_incomplete_duplicate_or_empty_summary_catalogue_is_rejected(self):
        for alteration in ('missing', 'duplicate', 'empty'):
            with self.subTest(alteration=alteration), tempfile.TemporaryDirectory(dir=exporter.HERE) as tmp:
                root = Path(tmp)
                paths = self.fixture(root)
                rows = exporter.jsonl(paths['catalogue'])
                if alteration == 'missing':
                    rows.pop()
                elif alteration == 'duplicate':
                    rows[-1] = rows[0]
                else:
                    rows[-1]['proof_summary'] = ''
                paths['catalogue'].write_text(''.join(json.dumps(r)+'\n' for r in rows))
                with self.assertRaisesRegex(ValueError, 'Catalogue must contain exactly'):
                    self.run_audit(paths, root/'audited')


if __name__ == '__main__':
    unittest.main()
