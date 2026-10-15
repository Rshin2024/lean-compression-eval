import copy,importlib.util,json,math,os,tempfile,unittest
from fractions import Fraction
from pathlib import Path
SPEC=importlib.util.spec_from_file_location('compression_queries',Path(__file__).with_name('prepare_queries.py'))
q=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(q)
PACKAGE=Path(os.environ.get('EXPLORER_PACKAGE_ROOT',q.BASE)).resolve()

def row(model='a',sample=1,t0=4,wb=1,valid=True,available=None):
    available=valid if available is None else available
    result={'attemptId':model+'-'+str(sample),'model':model,'task':'q1','condition':'c','sample':sample,'status':'success' if valid else 'parse_failure','metricStatus':'available' if available else 'unavailable','sourceLength':{'sourceNonWhitespaceCharacters':18}}
    if available:
        ws=1;total=t0*(wb+ws)
        result['metrics']={'W_B':wb,'W_S':ws,'U_S_decimal':'1','U_B_decimal':str(total-1),'T0':{'numerator_decimal':str(total),'denominator_decimal':str(wb+ws)},'I0':{'numerator_decimal':str(wb),'denominator_decimal':'1'},'log2_T0':math.log2(t0),'log2_I0':math.log2(wb)}
    return result

def cell(model,values,weights=None):
    weights=weights or [1]*len(values)
    return q.make_cell([q.project_attempt(row(model,i+1,values[i] if i<len(values) else 4,weights[i] if i<len(values) else 1,valid=i<len(values))) for i in range(4)])

class ProjectionTests(unittest.TestCase):
    def test_all_failed_metrics_null_not_zero(self):
        attempt=q.project_attempt(row(valid=False))
        self.assertFalse(attempt['valid']);self.assertFalse(attempt['measurable'])
        self.assertTrue(all(attempt[k] is None for k in q.METRIC_FIELDS))
        result=cell('a',[]);self.assertEqual(result['scheduledCount'],4);self.assertEqual(result['rootCount'],0)
        self.assertTrue(all(result[k] is None for k in q.CELL_FIELDS));self.assertEqual(result['attemptIds'],[])
    def test_valid_but_unmeasurable_stays_valid_and_unranked(self):
        attempts=[q.project_attempt(row(sample=i,valid=i==1,available=False)) for i in range(1,5)]
        result=q.make_cell(attempts);self.assertEqual(result['validCount'],1);self.assertEqual(result['measurableCount'],0)
        with self.assertRaises(ValueError):q.compare_cells(result,cell('b',[4]))
    def test_exact_formula_rejects_corrupt_measurement(self):
        a=row();a['metrics']['U_B_decimal']='999'
        with self.assertRaisesRegex(ValueError,'T0 formula'):q.project_attempt(a)
        a=row(valid=False,available=True)
        with self.assertRaisesRegex(ValueError,'Unaccepted'):q.project_attempt(a)
    def test_complete_schedule_required(self):
        with self.assertRaises(ValueError):q.make_cell([q.project_attempt(row(sample=i)) for i in [1,2,3]])
        with self.assertRaises(ValueError):q.make_cell([q.project_attempt(row(sample=i)) for i in [1,2,3,3]])
    def test_unequal_valid_counts_exact_geomean_tie(self):
        a=cell('a',[4,9],[1,4]);b=cell('b',[6],[2])
        self.assertEqual(a['rootCount'],2);self.assertEqual(b['rootCount'],1)
        for metric in ('T0','I0','W_B'):self.assertEqual(q.compare_cells(a,b,metric),0)
        self.assertEqual(a['geomeanT0Scientific'],'6.00000e+0');self.assertEqual(a['geomeanWB'],2.)
    def test_float_collapsed_values_still_rank_exactly(self):
        a=cell('a',[2**200+1]);b=cell('b',[2**200])
        self.assertEqual(a['meanLog2T0'],b['meanLog2T0']);self.assertEqual(a['geomeanT0Scientific'],b['geomeanT0Scientific'])
        self.assertLess(q.compare_cells(a,b),0)
    def test_direction_and_midrank_points(self):
        a=cell('a',[4,9]);b=cell('b',[6]);dead=cell('d',[])
        no_shared=q.ranking([a,b,dead],['a','b','d'],['q1'],'c',0,'T0');self.assertEqual(no_shared['rows'],[])
        rank=q.ranking([a,b,dead],['a','b','d'],['q1'],'c',1,'T0')
        self.assertEqual(rank['models'],['a','b']);self.assertTrue(all(r['meanRank']==1.5 and r['rankPoints']==50 for r in rank['rows']))
        self.assertLess(q.compare_cells(cell('x',[4],[1]),cell('y',[4],[3]),'W_B'),0)
        self.assertGreater(q.compare_cells(cell('x',[4],[1]),cell('y',[4],[3]),'I0'),0)
    def test_range_uses_exact_values_and_deterministic_sample_ties(self):
        a=cell('a',[9,4,6,4]);self.assertEqual(a['minT0Sample'],2);self.assertEqual(a['maxT0Sample'],1)
        self.assertEqual(Fraction(int(a['minT0Numerator']),int(a['minT0Denominator'])),4)
    def test_source_integers_stay_safe_and_display_is_rounded(self):
        a=row(wb=2**53)
        with self.assertRaisesRegex(ValueError,'Unsafe'):q.project_attempt(a)
        self.assertEqual(q.scientific(Fraction(1,3)),'3.33333e-1')
    def test_real_projection_and_independent_default_oracle(self):
        app=json.loads((PACKAGE/'analysis-report/src/data.json').read_bytes());addon={**app['compressionExplorer'],'queries':app['queries']};queries=addon['queries']
        self.assertEqual([len(queries[k]['rows']) for k in ('compression_attempts','compression_cells','compression_coverage','compression_questions')],[9600,2400,24,100])
        actual=q.ranking(queries['compression_cells']['rows'],addon['modelIds'],[x['task'] for x in queries['compression_questions']['rows']],'mathematical_compression',25,'T0')
        expected=json.loads((q.HERE/'fixtures/expected-default.json').read_bytes())['default']
        self.assertEqual(set(actual['models']),set(expected['cohort']));self.assertEqual(actual['sharedQuestionIds'],expected['shared_question_ids'])
        by_model={x['model']:x for x in actual['rows']};by_pair={(x['model'],x['task']):x['midrank'] for x in actual['questionRanks']}
        for r in expected['ranking']:
            self.assertEqual(by_model[r['model']]['exactMeanRank'],r['average_rank_exact']);self.assertEqual(by_model[r['model']]['exactRankPoints'],r['rank_points_exact'])
            for task,rank in r['per_question_midranks'].items():self.assertEqual(by_pair[r['model'],task],rank)
        for r in queries['compression_attempts']['rows']:
            if not r['measurable']:self.assertTrue(all(r[k] is None for k in q.METRIC_FIELDS))
        allowed={'compression-podium-1', 'compression-question-ranking', 'compression-model-ranking', 'compression-methods', 'compression-podium-2', 'compression-model-coverage', 'compression-output-detail', 'compression-podium-3', 'compression-explorer-controls', 'compression-output-table', 'compression-explorer-intro'}
        self.assertTrue(all(set(x['source']['metricDefinitions'][0]['componentIds'])==allowed for key,x in queries.items() if key.startswith('compression_')))

    def test_new_output_directory_guard_rejects_existing_and_protected_paths(self):
        with tempfile.TemporaryDirectory(prefix='portable-query-test-',dir=q.HERE) as tmp:
            base=Path(tmp);package=base/'package';package.mkdir()
            for name in ('inputs','tables','analysis-report','reproducibility'):(package/name).mkdir()
            existing=base/'already-there';existing.mkdir();sentinel=existing/'sentinel.txt';sentinel.write_text('preserve')
            with self.assertRaisesRegex(ValueError,'already exists'):q.validate_output_directory(package,existing)
            self.assertEqual(sentinel.read_text(),'preserve')
            for name in ('inputs','tables','analysis-report','reproducibility'):
                with self.assertRaisesRegex(ValueError,'protected'):q.validate_output_directory(package,package/name/'new-output')
            output=base/'new-output';self.assertEqual(q.validate_output_directory(package,output),output)
            self.assertFalse(output.exists())

    def test_published_query_cartesian_completeness(self):
        app=json.loads((PACKAGE/'analysis-report/src/data.json').read_bytes());meta=app['compressionExplorer'];queries=app['queries']
        models=meta['modelIds'];conditions=meta['conditionIds'];tasks=[r['task'] for r in queries['compression_questions']['rows']]
        expected={(m,t,c) for m in models for t in tasks for c in conditions}
        cells=queries['compression_cells']['rows']
        def complete(rows):
            keys=[(r['model'],r['task'],r['condition']) for r in rows]
            return len(keys)==len(set(keys))==2400 and set(keys)==expected
        self.assertTrue(complete(cells));self.assertFalse(complete(cells[:-1]))
        attempts=queries['compression_attempts']['rows'];keys=[(r['model'],r['task'],r['condition'],r['sample']) for r in attempts]
        self.assertEqual(len(keys),9600);self.assertEqual(len(set(keys)),9600)
        self.assertEqual(set(keys),{(*k,s) for k in expected for s in range(1,5)})

if __name__=='__main__':unittest.main()
