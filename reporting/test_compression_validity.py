"""Offline failure-inclusive validity and descriptive-correlation contracts."""
from pathlib import Path
import subprocess
import unittest

ROOT=Path(__file__).resolve().parent
REAL=ROOT.parent/'runs/om100-suite-high-20261013/comparison-data.json'
SETUP=r'''
const assert=require('node:assert/strict'),P=require(process.argv[1]),C=require(process.argv[2]);
function build(models,tasks,spec){
 const attempts=[];
 for(const model of models)for(const task of tasks){const s=spec[model][task];
  for(let sample=1;sample<=4;sample++){
   const valid=sample<=s.valid,wb=(s.wbs||[])[sample-1]??s.wb??2,t0=(s.t0s||[])[sample-1]??s.t0;
   attempts.push({model,task,sample,condition:'mathematical_compression',attemptId:`${model}/${task}/${sample}`,
    status:valid?'success':'elaboration_failure',metricStatus:valid?'available':'unavailable',
    metrics:valid?{T0:{numerator_decimal:String(t0),denominator_decimal:'1'},
      I0:{numerator_decimal:String(wb),denominator_decimal:'1'},W_B:wb,W_S:1}:{}});
  }
 }
 return {models:models.map(id=>({id})),tasks,conditions:['mathematical_compression'],attempts,samplesPerQuestion:4};
}
const close=(a,b)=>assert(Math.abs(a-b)<1e-10,`${a} != ${b}`);
function run(d,o={},v={}){return C.compute(d,P.compute(d,o),o,v);}
function freeze(o){if(o&&typeof o==='object'){Object.freeze(o);Object.values(o).forEach(freeze);}return o;}
'''


class CompressionValidityTests(unittest.TestCase):
    def js(self,checks):
        result=subprocess.run(['node','-e',SETUP+checks,str(ROOT/'paper_rankings.js'),str(ROOT/'compression_validity.js'),str(REAL)],
                              capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_failures_and_over_length_successes_remain_in_denominator(self):
        self.js(r'''
const d=build(['a','b','c'],['q'],{a:{q:{valid:2,t0:2,wbs:[2,100]}},b:{q:{valid:3,t0:4}},c:{q:{valid:4,t0:8}}});
const before=JSON.stringify(d);freeze(d);
const r=run(d,{maxProofTokens:10},{level:'model',xMode:'magnitude'}),a=r.points.find(p=>p.model==='a');
assert.equal(a.valid,2);assert.equal(a.scheduled,4);assert.equal(a.y,.5);assert.equal(a.metricSamples,1);
assert.equal(a.attemptIds.length,4);assert.equal(a.metricAttemptIds.length,1);close(a.x,1);
close(r.pearson.value,1);close(r.spearman.value,1);
assert.equal(JSON.stringify(d),before);assert.equal(r.metadata.proofLengthFilterNarrowsValidityDenominator,false);
''')

    def test_valid_unmeasurable_outcome_still_counts_as_valid_and_x_identities_bind(self):
        self.js(r'''
const d=build(['a','b','c'],['q'],{a:{q:{valid:2,t0:2}},b:{q:{valid:3,t0:4}},c:{q:{valid:4,t0:8}}});
d.attempts[1].metricStatus='unavailable';d.attempts[1].metrics={};
const p=P.compute(d,{}),r=C.compute(d,p,{}),a=r.points.find(x=>x.model==='a');
assert.equal(a.valid,2);assert.equal(a.metricSamples,1);assert.equal(a.y,.5);
const altered=JSON.parse(JSON.stringify(p));altered.rows.find(x=>x.model==='a').questions[0].sampleIndices=[4];
assert.throws(()=>C.compute(d,altered,{}),/failed, unavailable or unselected/);
''')

    def test_magnitude_spearman_ignores_aggregate_rank_tie_places(self):
        self.js(r'''
const d=build(['a','b','c'],['q1','q2'],{
 a:{q1:{valid:3,t0:16},q2:{valid:3,t0:1}},
 b:{q1:{valid:2,t0:3},q2:{valid:2,t0:3}},
 c:{q1:{valid:1,t0:1},q2:{valid:1,t0:4}}});
const p=P.compute(d,{aggregation:'rank'});assert(p.rows.every(x=>x.place===1));
const r=C.compute(d,p,{aggregation:'rank'},{level:'model',xMode:'magnitude'});
close(r.spearman.value,1);
''')

    def test_failure_metrics_never_supply_compression_x(self):
        self.js(r'''
const d=build(['a','b','c'],['q'],{a:{q:{valid:1,t0:2}},b:{q:{valid:2,t0:4}},c:{q:{valid:3,t0:8}}});
for(const row of d.attempts)if(row.status!=='success'){
 row.metricStatus='available';row.metrics={T0:{numerator_decimal:'999999999',denominator_decimal:'1'},I0:{numerator_decimal:'1000',denominator_decimal:'1'},W_B:1000};
}
const r=run(d,{}, {level:'question',xMode:'magnitude'});
assert.deepEqual(r.points.map(p=>p.x),[1,2,3]);assert.deepEqual(r.points.map(p=>p.y),[.25,.5,.75]);
''')

    def test_missing_duplicate_and_unresolved_schedule_rejected(self):
        self.js(r'''
function source(){return build(['a','b','c'],['q'],{a:{q:{valid:1,t0:2}},b:{q:{valid:2,t0:4}},c:{q:{valid:3,t0:8}}});}
let d=source(),p=P.compute(d,{});d=JSON.parse(JSON.stringify(d));d.attempts.pop();assert.throws(()=>C.compute(d,p,{}),/Incomplete/);
d=source();p=P.compute(d,{});d=JSON.parse(JSON.stringify(d));d.attempts.push(d.attempts[0]);assert.throws(()=>C.compute(d,p,{}),/Duplicate/);
d=source();p=P.compute(d,{});d=JSON.parse(JSON.stringify(d));d.attempts[3].status='not_generated';assert.throws(()=>C.compute(d,p,{}),/Unresolved/);
''')

    def test_fixed_sample_and_minimum_four_force_constant_validity(self):
        self.js(r'''
const d=build(['a','b','c'],['q'],{a:{q:{valid:1,t0:2}},b:{q:{valid:2,t0:4}},c:{q:{valid:4,t0:8}}});
const fixed=run(d,{sample:1},{level:'question',xMode:'magnitude'});
assert(fixed.points.every(p=>p.scheduled===1&&p.y===1));assert.equal(fixed.pearson.value,null);assert.equal(fixed.pearson.reason,'constant_y');assert.equal(fixed.spearman.value,null);
assert.equal(fixed.metadata.selectionForcesValidityOne,true);
const full=build(['a','b','c'],['q'],{a:{q:{valid:4,t0:2}},b:{q:{valid:4,t0:4}},c:{q:{valid:4,t0:8}}});
const r=run(full,{minSamples:4},{level:'question',xMode:'rank'});assert(r.points.every(p=>p.scheduled===4&&p.y===1));assert.equal(r.pearson.reason,'constant_y');
''')

    def test_spearman_uses_midrank_ties(self):
        self.js(r'''
const d=build(['a','b','c'],['q'],{a:{q:{valid:1,t0:2}},b:{q:{valid:2,t0:2}},c:{q:{valid:3,t0:8}}});
const r=run(d,{}, {level:'model',xMode:'magnitude'});close(r.spearman.value,Math.sqrt(3)/2);
''')

    def test_exact_magnitude_spearman_distinguishes_float_collapsed_values(self):
        self.js(r'''
const huge=10n**95n,d=build(['a','b','c'],['q'],{a:{q:{valid:1,t0:huge}},b:{q:{valid:2,t0:huge+1n}},c:{q:{valid:3,t0:huge+2n}}});
for(const level of ['model','question']){
 const r=run(d,{aggregation:'rank'},{level,xMode:'magnitude'});
 assert.equal(new Set(r.points.map(p=>p.x)).size,1);assert.equal(r.pearson.value,null);assert.equal(r.pearson.reason,'constant_x');close(r.spearman.value,1);
}
''')

    def test_wb_magnitude_sign_means_longer_but_rank_sign_means_shorter(self):
        self.js(r'''
const d=build(['a','b','c'],['q'],{a:{q:{valid:1,t0:2,wb:2}},b:{q:{valid:2,t0:2,wb:4}},c:{q:{valid:3,t0:2,wb:8}}});
const raw=run(d,{metric:'W_B',aggregation:'rank'},{level:'model',xMode:'magnitude'});
close(raw.pearson.value,1);close(raw.spearman.value,1);assert.equal(raw.metadata.xDirection,'increasing_means_longer_proof_source');
const preferred=run(d,{metric:'W_B'},{level:'model',xMode:'rank'});close(preferred.pearson.value,-1);close(preferred.spearman.value,-1);
''')

    def test_within_question_demeaning_removes_between_question_confounding(self):
        self.js(r'''
const d=build(['a','b','c'],['hard','easy'],{
 a:{hard:{valid:3,t0:2},easy:{valid:4,t0:2048}},
 b:{hard:{valid:2,t0:4},easy:{valid:3,t0:4096}},
 c:{hard:{valid:1,t0:8},easy:{valid:2,t0:8192}}});
const r=run(d,{}, {level:'question',xMode:'magnitude'});
assert(r.pearson.value>0);close(r.withinQuestionPearson.value,-1);assert.equal(r.points.length,6);
// Cross-question Spearman must order raw magnitudes, not reuse within-question ranks.
assert(r.spearman.value>0);
''')

    def test_j0_inherits_descending_anchor_tolerance_groups(self):
        self.js(r'''
const d=build(['a','b','c'],['q'],{a:{q:{valid:1,t0:10000000000000n}},b:{q:{valid:2,t0:9999999999993n}},c:{q:{valid:3,t0:9999999999986n}}});
const r=run(d,{metric:'J0',beta:1},{level:'question',xMode:'magnitude'});
// Descending anchor ties the top two values; rho is the negative tied-rank value.
close(r.spearman.value,-Math.sqrt(3)/2);
''')

    def test_less_than_three_points_and_option_mismatch_are_explicit(self):
        self.js(r'''
const d=build(['a','b'],['q'],{a:{q:{valid:1,t0:2}},b:{q:{valid:2,t0:4}}});
const r=run(d,{}, {level:'model',xMode:'rank'});assert.equal(r.pearson.value,null);assert.equal(r.pearson.reason,'fewer_than_three_points');
assert.equal(r.withinQuestionPearson.reason,'requires_question_level');
const p=P.compute(d,{sample:1});assert.throws(()=>C.compute(d,p,{sample:'all'}),/mismatch/);
''')

    @unittest.skipUnless(REAL.exists(),'Completed report is optional local validation data')
    def test_real_denominator_oracle_and_immutability(self):
        self.js(r'''
const fs=require('node:fs'),d=JSON.parse(fs.readFileSync(process.argv[3],'utf8')),before=JSON.stringify(d),o={minCoverage:25},p=P.compute(d,o);
for(const level of ['model','question'])for(const xMode of ['rank','magnitude']){
 const r=C.compute(d,p,o,{level,xMode});assert.equal(r.points.length,level==='model'?5:110);
 for(const point of r.points){const raw=d.attempts.filter(a=>a.model===point.model&&a.condition==='mathematical_compression'&&p.tasks.includes(a.task)&&(point.task===null||a.task===point.task));
  assert.equal(point.valid,raw.filter(a=>a.status==='success').length);assert.equal(point.scheduled,raw.length);close(point.y,point.valid/point.scheduled);}
 if(level==='model'&&xMode==='rank')close(r.spearman.value,-.7);JSON.stringify(r);
}
assert.equal(JSON.stringify(d),before);
''')


if __name__=='__main__':unittest.main()
