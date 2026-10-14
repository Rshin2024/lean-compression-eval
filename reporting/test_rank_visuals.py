"""Presentation changes preserve exact ranks and disclose the selected cohort."""
from pathlib import Path
import subprocess
import unittest


class RankVisualTests(unittest.TestCase):
    def javascript(self, setup, checks):
        html = Path(__file__).with_name('comparison.html').read_text()
        functions = 'function exactMeanCompare(' + html.split('function exactMeanCompare(', 1)[1].split('function questionRankView(', 1)[0]
        script = "const assert=require('node:assert/strict');\n" + setup + functions + checks
        subprocess.run(['node', '-e', script], check=True, text=True, capture_output=True)

    def test_rank_points_preserve_order_ties_and_equal_question_weight(self):
        self.javascript("""
const NAME=x=>x, finite=Number.isFinite, mean=a=>a.reduce((a,b)=>a+b,0)/a.length;
""", """
assert.equal(rankPoints(1,5),100);assert.equal(rankPoints(5,5),0);
assert.equal(rankPoints(3,5),50);assert.equal(rankPoints(1.5,3),75);
for(const [rank,n] of [[1,1],[0,5],[6,5],[NaN,5],[2,2.5]])assert.equal(rankPoints(rank,n),null);
const stat=n=>({mean_log2_T0:0,exact_geometric_T0:{numerator_product_decimal:String(n),denominator_product_decimal:'1',sample_count:1}});
const cohort=[['a',[3,1]],['b',[3,2]],['c',[1,3]]].map(([id,values])=>({id,
  perTask:Object.fromEntries(values.map((n,i)=>['q'+i,{compression:stat(n)}]))}));
const ranks=summarizeQuestionRanks(cohort,['q0','q1'],'compression');
assert.deepEqual(ranks.map(r=>[r.model,r.average,rankPoints(r.average,3)]),
  [['b',1.75,62.5],['c',2,50],['a',2.25,37.5]]);
for(const row of ranks)assert.equal(rankPoints(row.average,3),mean(Object.values(row.ranks).map(r=>rankPoints(r,3))));
assert.deepEqual(summarizeQuestionRanks(cohort,[],'compression'),[]);
""")

    def test_presets_disclose_overlap_and_keep_models_with_missing_values(self):
        self.javascript("""
const N=100,NAME=x=>x,esc=x=>String(x),finite=Number.isFinite;
const mean=a=>a.length?a.reduce((a,b)=>a+b,0)/a.length:null;
const node={innerHTML:''},document={querySelector:()=>node};
const eligible=(m,c)=>m.tasks,shared=(ms,c)=>ms.length?ms[0].tasks.filter(t=>ms.every(m=>m.tasks.includes(t))):[];
""", """
const models=[{id:'a',tasks:Array.from({length:100},(_,i)=>i)},
 {id:'b',tasks:Array.from({length:80},(_,i)=>i)},{id:'unmeasured',tasks:[]}];
const before=JSON.stringify(models);
renderRankPresets(models,'compression',25);
assert.equal((node.innerHTML.match(/aria-pressed="true"/g)||[]).length,1);
assert.match(node.innerHTML,/2 models · 80 shared questions/);
assert.match(node.innerHTML,/3 models · no shared questions/);
assert.match(node.innerHTML,/data-coverage="0"/);
assert.match(node.innerHTML,/at least 25 of 100 questions/);
assert.equal(JSON.stringify(models),before);
""")


if __name__ == '__main__':
    unittest.main()
