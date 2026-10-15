"""Offline all-attempt length/validity analysis, with explicit missingness."""
from pathlib import Path
import json
import subprocess
import unittest

MODULE=Path(__file__).with_name('source_length_analysis.js').resolve()
REAL=MODULE.parents[1]/'runs/om100-suite-high-20261013/comparison-data.json'
SETUP=r'''
const assert=require('node:assert/strict'),A=require(process.argv[1]);
function row(model,task,condition,sample,length,status='success',extra={}){
 return {model,task,condition,sample,attemptId:`${model}/${task}/${condition}/${sample}`,status,
 sourceLength:{available:length!==null,sourceCharacters:length,sourceNonWhitespaceCharacters:length,sourceUtf8Bytes:length,
 sourceLines:1,rawCharacters:length,rawUtf8Bytes:length,truncated:status==='truncated_output',sourceKind:'fixture',...extra}};
}
function data(rows){return {models:[...new Set(rows.map(r=>r.model))].map(id=>({id})),tasks:[...new Set(rows.map(r=>r.task))],
 conditions:[...new Set(rows.map(r=>r.condition))],attempts:rows,samplesPerQuestion:4,sourceMethods:{version:'fixture_v1'}};}
const close=(a,b)=>assert(Math.abs(a-b)<1e-10,`${a} != ${b}`);
function freeze(o){if(o&&typeof o==='object'){Object.freeze(o);Object.values(o).forEach(freeze);}return o;}
function reconcile(r){const c=r.coverage;assert.equal(c.selectedAttempts,c.excludedMissingText+c.excludedByTruncation+c.excludedFormat+c.includedAttempts);
 assert.equal(c.includedAttempts,c.accepted+c.rejected);assert.equal(r.bins.reduce((s,b)=>s+b.n,0),r.points.length);
 assert.equal(r.bins.reduce((s,b)=>s+b.accepted,0),c.accepted);assert.equal(r.bins.reduce((s,b)=>s+b.rejected,0),c.rejected);}
'''


class SourceLengthAnalysisTests(unittest.TestCase):
    def js(self,checks):
        result=subprocess.run(['node','-e',SETUP+checks,str(MODULE),str(REAL)],text=True,capture_output=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_all_attempts_including_textual_parse_failures_without_success_intersection(self):
        self.js(r'''
const d=data([row('good','q1','standard',1,10),row('bad','q2','standard',1,20,'parse_failure'),
 row('bad','q2','standard',2,30,'elaboration_failure'),row('bad','q2','standard',3,40,'output_format_failure')]);
const r=A.compute(d);assert.equal(r.points.length,4);assert.equal(r.coverage.accepted,1);assert.equal(r.coverage.rejected,3);
assert.equal(r.perModel.find(m=>m.model==='bad').n,3);assert.equal(r.metadata.sourceMethods.version,'fixture_v1');reconcile(r);
''')

    def test_missing_empty_and_zero_are_never_imputed(self):
        self.js(r'''
const d=data([row('a','q','standard',1,null,'truncated_output'),row('a','q','standard',2,0,'parse_failure'),
 row('a','q','standard',3,10),row('a','q','standard',4,20,'elaboration_failure',{available:false})]);
const r=A.compute(d);assert.equal(r.coverage.selectedAttempts,4);assert.equal(r.coverage.missingText,3);assert.equal(r.points.length,1);
assert.equal(r.points[0].length,10);assert.equal(r.pearson.value,null);assert.equal(r.summaries.rejected.median,null);reconcile(r);
''')

    def test_truncation_policies_and_format_exclusions_are_disjoint(self):
        self.js(r'''
const d=data([row('a','q1','standard',1,10),row('a','q1','standard',2,20,'parse_failure'),
 row('a','q1','standard',3,30,'output_format_failure'),row('a','q1','standard',4,40,'elaboration_failure'),
 row('a','q2','standard',1,50,'truncated_output'),row('a','q2','standard',2,null,'truncated_output')]);
const excluded=A.compute(d);assert.equal(excluded.coverage.selectedAttempts,6);assert.equal(excluded.coverage.truncatedAttempts,2);
assert.equal(excluded.coverage.excludedMissingText,1);assert.equal(excluded.coverage.excludedByTruncation,1);assert.equal(excluded.points.length,4);
const included=A.compute(d,{truncation:'include',failures:'exclude_format'});assert.equal(included.coverage.excludedFormat,2);assert.equal(included.points.length,3);
assert.equal(included.coverage.rejected,2);assert.equal(included.coverage.includedTruncated,1);
const only=A.compute(d,{truncation:'only'});assert.equal(only.coverage.excludedNonTruncated,4);assert.equal(only.points.length,1);assert.equal(only.coverage.accepted,0);
assert.equal(excluded.selectedTruncatedSummary.n,1);assert.equal(excluded.summaries.truncated.n,0);
[excluded,included,only].forEach(reconcile);
''')

    def test_bin_boundaries_and_rates_reconcile(self):
        self.js(r'''
const d=data([row('a','q1','standard',1,1),row('a','q1','standard',2,63,'parse_failure'),
 row('a','q1','standard',3,64),row('a','q1','standard',4,127),row('a','q2','standard',1,128,'elaboration_failure'),
 row('b','q2','standard',1,2048,'truncated_output')]);
const r=A.compute(d,{modelIds:['a']});assert.deepEqual(r.bins.slice(0,3).map(b=>[b.lower,b.upper,b.n,b.accepted,b.rate]),
 [[1,64,2,1,.5],[64,128,2,2,1],[128,256,1,0,0]]);
assert(r.bins.some(b=>b.n===0&&b.rate===null));assert.equal(r.bins.at(-1).upper,4096);reconcile(r);
const subset=A.compute(d,{modelIds:['a'],taskIds:['q1']});assert.deepEqual(subset.bins.map(b=>[b.lower,b.upper]),r.bins.map(b=>[b.lower,b.upper]));
''')

    def test_quantiles_raw_units_and_scale_invariant_exact_tied_spearman(self):
        self.js(r'''
const d=data([row('a','q','standard',1,1),row('a','q','standard',2,1,'parse_failure'),
 row('a','q','standard',3,4,'parse_failure'),row('a','q','standard',4,8,'parse_failure')]);
const log=A.compute(d),linear=A.compute(d,{xScale:'linear'});
assert.equal(log.spearman.value,linear.spearman.value);close(log.spearman.value,-2/Math.sqrt(13.5));
assert.equal(log.summaries.rejected.median,4);assert.equal(log.summaries.rejected.q25,2.5);assert.equal(log.summaries.rejected.q75,6);
assert.deepEqual(log.points.map(p=>p.x),[0,0,2,3]);
''')

    def test_filters_and_units_preserve_original_denominators(self):
        self.js(r'''
const d=data([row('a','q1','standard',1,10),row('a','q1','compression',1,20,'parse_failure'),
 row('b','q2','standard',1,30),row('b','q2','compression',1,40,'elaboration_failure')]);
d.attempts[1].sourceLength.sourceUtf8Bytes=60;
const r=A.compute(d,{modelIds:['a'],condition:'compression',taskIds:['q1'],unit:'sourceUtf8Bytes',xScale:'linear'});
assert.equal(r.coverage.selectedAttempts,1);assert.equal(r.points[0].length,60);assert.equal(r.coverage.accepted,0);reconcile(r);
assert.equal(A.compute(d,{taskIds:[]}).coverage.selectedAttempts,0);
''')

    def test_within_cell_controls_question_confounding_and_discards_singletons(self):
        self.js(r'''
const rows=[];
// Hard cell: low lengths and one success; easy cell: high lengths and three successes.
for(let s=1;s<=4;s++)rows.push(row('a','hard','standard',s,s,s===1?'success':'elaboration_failure'));
for(let s=1;s<=4;s++)rows.push(row('a','easy','standard',s,100+s,s<=3?'success':'elaboration_failure'));
rows.push(row('a','single','standard',1,50,'parse_failure'));
const r=A.compute(data(rows),{xScale:'linear'});assert(r.pearson.value>0);assert(r.withinCellPearson.value<0);
assert.equal(r.withinCellPearson.n,8);assert.equal(r.withinCellPearson.usedCells,2);assert.equal(r.withinCellPearson.singletonCellsExcluded,1);
assert.equal(r.withinCellPearson.singletonAttemptsExcluded,1);assert.equal(r.withinCellPearson.mixedOutcomeCells,2);assert.equal(r.withinCellPearson.mixedOutcomeAttempts,8);
''')

    def test_within_cell_separates_prompt_and_reports_constant_outcomes(self):
        self.js(r'''
const rows=[row('a','q','standard',1,1),row('a','q','standard',2,2),
 row('a','q','compression',1,20,'parse_failure'),row('a','q','compression',2,30,'parse_failure')];
const r=A.compute(data(rows));assert.equal(r.withinCellPearson.usedCells,2);assert.equal(r.withinCellPearson.constantOutcomeCells,2);
assert.equal(r.withinCellPearson.value,null);assert.equal(r.withinCellPearson.reason,'constant_y');
''')

    def test_no_mutation_no_zero_empty_summaries_and_fail_closed_controls(self):
        self.js(r'''
const d=data([row('a','q','standard',1,10),row('a','q','standard',2,20),row('a','q','standard',3,30)]);
const before=JSON.stringify(d);freeze(d);const r=A.compute(d);assert.equal(r.pearson.reason,'constant_y');assert.equal(r.summaries.rejected.n,0);
assert.equal(r.summaries.rejected.q25,null);JSON.stringify(r);assert.equal(JSON.stringify(d),before);
for(const o of [{modelIds:['unknown']},{taskIds:['none']},{unit:'W_B'},{truncation:'pretend_zero'},{failures:'exclude_all'},{xScale:'sqrt'}])assert.throws(()=>A.compute(d,o));
const dup=data([d.attempts[0],d.attempts[0]]);assert.throws(()=>A.compute(dup),/Duplicate/);
const unfinished=data([row('a','q','standard',1,10,'not_evaluated')]);assert.throws(()=>A.compute(unfinished),/Unresolved/);
''')

    def test_measurement_metadata_uses_actual_builder_field_and_is_detached(self):
        self.js(r'''
const d=data([row('a','q','standard',1,10)]);d.sourceLengthMeasurement={version:'bound_measurement_v2',unit:'codepoints'};
const r=A.compute(d);assert.equal(r.metadata.sourceMethods.version,'bound_measurement_v2');
r.metadata.sourceMethods.version='changed_output';assert.equal(d.sourceLengthMeasurement.version,'bound_measurement_v2');
''')

    @unittest.skipUnless(REAL.exists(),'Real report is optional')
    def test_optional_real_measured_data_counts(self):
        payload=json.loads(REAL.read_bytes())
        if not payload.get('attempts') or not all('sourceLength' in r for r in payload['attempts']):
            self.skipTest('Completed source-length measurement is not attached yet')
        self.js(r'''
const fs=require('node:fs'),d=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));
if(d.attempts.every(r=>r.sourceLength)){
 const r=A.compute(d),nonempty=d.attempts.filter(x=>x.sourceLength.available&&Number.isSafeInteger(x.sourceLength.sourceNonWhitespaceCharacters)&&x.sourceLength.sourceNonWhitespaceCharacters>0);
 assert.equal(r.coverage.selectedAttempts,9600);assert.equal(r.coverage.truncatedAttempts,4967);
 assert.equal(r.coverage.nonempty,nonempty.length);assert.equal(r.points.length,nonempty.filter(x=>!x.sourceLength.truncated&&x.status!=='truncated_output').length);
 assert.equal(r.coverage.accepted,r.points.filter(p=>p.valid===1).length);reconcile(r);
}
''')


if __name__=='__main__':unittest.main()
