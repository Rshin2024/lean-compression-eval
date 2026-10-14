"""Offline paper-metric view contracts; never run models or Lean."""
from pathlib import Path
import json
import subprocess
import unittest

MODULE = Path(__file__).with_name('paper_rankings.js').resolve()
REAL_DATA = Path(__file__).resolve().parents[1] / 'runs/om100-suite-high-20261013/comparison-data.json'
SETUP = r'''
const assert=require('node:assert/strict'),P=require(process.argv[1]);
function attempt(model,task,sample,t0,wb=2,ws=1,extra={}) {
 return {model,task,sample,condition:'mathematical_compression',attemptId:`${model}/${task}/${sample}`,
 status:'success',metricStatus:'available',metrics:{T0:{numerator_decimal:String(t0),denominator_decimal:'1'},
 I0:{numerator_decimal:String(wb),denominator_decimal:String(ws)},W_B:wb,W_S:ws},...extra};
}
function data(models,tasks,attempts){return {models:models.map(id=>({id})),tasks,conditions:['mathematical_compression'],attempts,samplesPerQuestion:4};}
function freeze(o){if(o&&typeof o==='object'){Object.freeze(o);Object.values(o).forEach(freeze);}return o;}
const close=(a,b)=>assert(Math.abs(a-b)<1e-10,`${a} != ${b}`);
'''


class PaperRankingTests(unittest.TestCase):
    def js(self, checks, *, real=False):
        script=SETUP+checks
        result=subprocess.run(['node','-e',script,str(MODULE),str(REAL_DATA)],text=True,capture_output=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        return result.stdout

    def test_exact_sub_float_order_and_ties(self):
        self.js(r'''
const huge=10n**95n;
const d=data(['a','b','c'],['q'],[attempt('a','q',1,huge),attempt('b','q',1,huge+1n),attempt('c','q',1,huge),attempt('c','q',2,huge)]);
const before=JSON.stringify(d);freeze(d);
for(const aggregation of ['rank','magnitude']){
 const r=P.compute(d,{aggregation});assert.deepEqual(r.rows.map(x=>[x.model,x.place,x.averageRank]),[['b',1,1],['a',2,2.5],['c',2,2.5]]);
 assert.equal(r.rows[1].questions[0].exact.T0.numerator_product_decimal,huge.toString());
 assert.equal(r.rows[2].questions[0].exact.T0.root_degree,'2');JSON.stringify(r);
}
assert.equal(JSON.stringify(d),before);
''')

    def test_equal_question_geometry_not_pooled_samples(self):
        self.js(r'''
const rows=[attempt('a','q1',1,16),attempt('b','q1',1,3),attempt('b','q2',1,3)];
for(let s=1;s<=4;s++)rows.push(attempt('a','q2',s,1));
const r=P.compute(data(['a','b'],['q1','q2'],rows),{aggregation:'magnitude'});
assert.equal(r.rows[0].model,'a');close(r.rows[0].value,4);close(r.rows[1].value,3);
assert.equal(r.rows[0].qualifyingSamples,5);assert.equal(r.rows[0].averageRank,1.5);
// Both have identical mean ranks although their equal-question magnitudes differ.
const ranked=P.compute(data(['a','b'],['q1','q2'],rows),{});
assert.deepEqual(ranked.rows.map(x=>x.place),[1,1]);
''')

    def test_i0_higher_means_longer_and_wb_ranks_lower_first(self):
        self.js(r'''
const d=data(['short','long'],['q'],[attempt('short','q',1,2,2,2),attempt('long','q',1,2,8,2)]);
for(const aggregation of ['rank','magnitude']){
 const interest=P.compute(d,{metric:'I0',aggregation});assert.equal(interest.rows[0].model,'long');close(interest.rows[0].value,4);
 const length=P.compute(d,{metric:'W_B',aggregation});assert.equal(length.rows[0].model,'short');close(length.rows[0].value,2);
}
assert.equal(d.attempts[0].metrics.log2_I0,undefined);
''')

    def test_j0_max_normalization_beta_and_arithmetic_magnitude(self):
        self.js(r'''
const d=data(['a','b'],['q'],[attempt('a','q',1,4,2),attempt('b','q',1,2,4)]);
const mid=P.compute(d,{metric:'J0',beta:.5});assert.deepEqual(mid.rows.map(x=>[x.value,x.place,x.averageRank]),[[.75,1,1.5],[.75,1,1.5]]);
assert.equal(P.compute(d,{metric:'J0',beta:1}).rows[0].model,'a');
assert.equal(P.compute(d,{metric:'J0',beta:0}).rows[0].model,'b');
const multi=data(['a','b'],['q1','q2'],[attempt('a','q1',1,4),attempt('b','q1',1,1),attempt('a','q2',1,1),attempt('b','q2',1,4)]);
const r=P.compute(multi,{metric:'J0',beta:1,aggregation:'magnitude'});
assert.deepEqual(r.rows.map(x=>[x.value,x.place]),[[.625,1],[.625,1]]);
assert.equal(r.metadata.j0TieTolerance,1e-12);JSON.stringify(r);
''')

    def test_j0_tolerance_groups_do_not_chain_nontransitively(self):
        self.js(r'''
const d=data(['a','b','c'],['q'],[attempt('a','q',1,10000000000000n),attempt('b','q',1,9999999999993n),attempt('c','q',1,9999999999986n)]);
const r=P.compute(d,{metric:'J0',beta:1});
assert.deepEqual(r.rows.map(x=>[x.model,x.questions[0].rank]),[['a',1.5],['b',1.5],['c',3]]);
''')

    def test_filters_precede_global_coverage_and_requested_intersection(self):
        self.js(r'''
const d=data(['a','b','sparse'],['q1','q2'],[
 attempt('a','q1',1,4,2),attempt('a','q1',2,9,10),attempt('a','q2',1,3,2),
 attempt('b','q1',1,2,2),attempt('b','q1',2,2,2),attempt('b','q2',1,5,2),attempt('sparse','q1',1,7,2)]);
let r=P.compute(d,{minCoverage:2,questionIds:['q1']});
assert.deepEqual(r.cohort,['a','b']);assert.deepEqual(r.tasks,['q1']);assert.deepEqual(r.coverage,{a:2,b:2,sparse:1});
r=P.compute(d,{modelIds:['a','b'],sample:1,maxProofTokens:2});assert.equal(r.rows[0].qualifyingSamples,2);
assert(r.rows.every(x=>x.questions.every(q=>q.samples===1&&q.sampleIndices[0]===1&&q.attemptIds[0].endsWith('/1'))));
r=P.compute(d,{modelIds:['a','b'],minSamples:2,maxProofTokens:2});assert.deepEqual(r.rows,[]);
r=P.compute(d,{modelIds:['a','b'],sample:1,minSamples:2});assert.deepEqual(r.rows,[]);
r=P.compute(d,{questionIds:[]});assert.deepEqual(r.rows,[]);assert.deepEqual(r.coverage,{a:2,b:2,sparse:1});
''')

    def test_failure_unavailable_and_missing_required_metrics_never_become_zero(self):
        self.js(r'''
const bad=attempt('a','q',2,999,2,1,{status:'parse_failure'}),unknown=attempt('b','q',2,999,2,1,{metricStatus:'unavailable'});
const incomplete=attempt('a','q',3,999);delete incomplete.metrics.I0;
const d=data(['a','b'],['q'],[attempt('a','q',1,2),attempt('b','q',1,4),bad,unknown,incomplete]);
let r=P.compute(d,{metric:'I0'});assert(r.rows.every(x=>x.qualifyingSamples===1));assert.equal(r.metadata.missingRequiredMetricAttempts,1);
r=P.compute(d,{metric:'T0'});assert.equal(r.rows.find(x=>x.model==='a').qualifyingSamples,2);
assert.equal(r.rows.find(x=>x.model==='a').i0Log2,null);
assert.deepEqual(P.compute(d,{modelIds:['a']}).rows,[]);assert.deepEqual(P.compute(d,{modelIds:[]}).rows,[]);
''')

    def test_invalid_controls_and_duplicate_identity_rejected(self):
        self.js(r'''
const d=data(['a','b'],['q'],[attempt('a','q',1,2),attempt('b','q',1,3)]);
for(const options of [{beta:-1},{beta:NaN},{beta:2},{metric:'PageRank'},{minSamples:0},{minSamples:5},{sample:0},{sample:'1'},
 {questionIds:['missing']},{modelIds:['a','a']},{maxProofTokens:Infinity},{aggregation:'best_sample'}])assert.throws(()=>P.compute(d,options));
assert.throws(()=>P.compute(data(['a','b'],['q'],[...d.attempts,d.attempts[0]])),/Duplicate scheduled/);
''')

    def test_huge_values_keep_exact_evidence_and_safe_json(self):
        self.js(r'''
const d=data(['a','b'],['q'],[attempt('a','q',1,10n**1000n),attempt('b','q',1,10n**999n)]);
const r=P.compute(d,{aggregation:'magnitude'});assert.equal(r.rows[0].model,'a');assert.equal(r.rows[0].value,null);
assert(Number.isFinite(r.rows[0].metricLog2));assert(r.rows[0].exact.T0.numerator_product_decimal.length===1001);
assert(!JSON.stringify(r).includes('Infinity'));
''')

    @unittest.skipUnless(REAL_DATA.exists(),'Real local report is optional; synthetic tests cover the portable source')
    def test_optional_real_default_rank_oracle_and_all_filters(self):
        self.js(r'''
const fs=require('node:fs'),d=JSON.parse(fs.readFileSync(process.argv[2],'utf8')),before=JSON.stringify(d);
const r=P.compute(d,{minCoverage:25});assert.equal(r.cohort.length,5);assert.equal(r.tasks.length,22);
const expected=[['claude-opus-5-5',24/11],['claude-fable-5-1',49/22],['gpt-6-astra',57/22],['gpt-6.1-sol',75/22],['gpt-6-sol',101/22]];
assert.deepEqual(r.rows.map(x=>x.model),expected.map(x=>x[0]));r.rows.forEach((row,i)=>close(row.averageRank,expected[i][1]));
for(const metric of ['T0','I0','J0','W_B'])for(const aggregation of ['rank','magnitude'])for(const sample of ['all',1,4]){
 const result=P.compute(d,{metric,aggregation,sample,minCoverage:0,modelIds:['gpt-6.1-sol','gpt-6-astra'],minSamples:1,maxProofTokens:500});
 assert(result.rows.every(row=>row.questions.length===result.tasks.length));JSON.stringify(result);
}
assert.equal(JSON.stringify(d),before);
''',real=True)


if __name__=='__main__':unittest.main()
