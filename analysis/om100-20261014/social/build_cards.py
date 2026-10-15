"""Render reviewed standalone social cards; no publication or evaluator calls."""
from pathlib import Path
import hashlib,json
from fractions import Fraction
from collections import defaultdict
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

HERE=Path(__file__).resolve().parent;BASE=HERE.parents[2]
MAIN=BASE/'work/paper-analysis-20261014/main/results'
INPUT=BASE/'outputs/lean-compression-eval/runs/om100-max-suite-high-20261014/comparison-data.json'
INDEX=BASE/'outputs/lean-compression-eval/data/prepared/openai_math_100_v1/index.json'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
assert sha(INPUT)=='4ddf80101317bb6158bd13a435eccae899684479dfd74b91c2047c81c550f264'
data=json.loads(INPUT.read_text());rates=json.loads((MAIN/'rates.json').read_text());pairs=json.loads((MAIN/'pairwise_contests.json').read_text())
tasks=sorted(data['tasks']);meta={r['task_id']:r for r in json.loads(INDEX.read_text())['questions']};families=sorted({r['family_id'] for r in meta.values()})
rng=np.random.default_rng(20261014);rng.multinomial(100,np.full(100,.01),size=10000);draws=rng.multinomial(19,np.full(19,1/19),size=10000)
def ci(y):
 sums=np.array([sum(v for t,v in y.items() if meta[t]['family_id']==f) for f in families]);ns=np.array([sum(meta[t]['family_id']==f for t in y) for f in families]);den=draws@ns;values=(draws@sums)[den>0]/den[den>0];return np.quantile(values,[.025,.975])
def verify(x,y):assert abs(x-y)<1e-10,(x,y)
g=defaultdict(list)
for r in data['attempts']:g[r['model'],r['condition'],r['task']].append(r)
names={'claude-fable-5-1':'Claude Fable 5.1','gpt-6-astra':'GPT-6 Astra','gpt-6.1-sol':'GPT-6.1 Sol','claude-opus-5-5':'Claude Opus 5.5','gpt-6-sol':'GPT-6 Sol','gpt-6-luna':'GPT-6 Luna','claude-haiku-5-5':'Claude Haiku 5.5','claude-sonnet-5-5':'Claude Sonnet 5.5'}
order=list(names);expected=[96,89,84,81,67,30,19,16];card1=[]
for model,n in zip(order,expected):
 r=next(x for x in rates if x['model']==model and x['condition']=='standard' and x['measure']=='pass4')
 y={t:float(any(a['status']=='success' for a in g[model,'standard',t])) for t in tasks};lo,hi=ci(y);verify(sum(y.values()),n);verify(r['mean'],n/100);verify(lo,r['ci_family_low']);verify(hi,r['ci_family_high'])
 card1.append({'model':model,'label':names[model],'solved':n,'questions':100,'ci_low_count':100*float(lo),'ci_high_count':100*float(hi)})
selected=[('claude-fable-5-1','gpt-6-astra','Fable vs Astra'),('claude-fable-5-1','claude-opus-5-5','Fable vs Opus'),('claude-opus-5-5','gpt-6-astra','Opus vs Astra')];card2=[]
for a,b,label in selected:
 y={}
 for task in tasks:
  aa=[x for x in g[a,'mathematical_compression',task] if x['status']=='success'];bb=[x for x in g[b,'mathematical_compression',task] if x['status']=='success']
  if not aa or not bb:continue
  credit=Fraction()
  for x in aa:
   for z in bb:
    m=x['metrics'];n=z['metrics'];u=Fraction(int(m['T0']['numerator_decimal']),int(m['T0']['denominator_decimal']));v=Fraction(int(n['T0']['numerator_decimal']),int(n['T0']['denominator_decimal']))
    credit+=1 if u>v else 0 if u<v else Fraction(1,2)
  y[task]=credit/(len(aa)*len(bb))
 exact=sum(y.values(),Fraction())/len(y);lo,hi=ci({t:float(v) for t,v in y.items()})
 p=next(x for x in pairs if x['condition']=='mathematical_compression' and x['metric']=='T0' and x['kind']=='valid_only' and {x['model_a'],x['model_b']}=={a,b})
 reverse=p['model_a']!=a;mean=1-p['mean'] if reverse else p['mean'];lower=1-p['ci_family_high'] if reverse else p['ci_family_low'];upper=1-p['ci_family_low'] if reverse else p['ci_family_high']
 verify(float(exact),mean);verify(lo,lower);verify(hi,upper);assert len(y)==p['n_questions']
 card2.append({'model_a':a,'model_b':b,'label':label,'exact_win_credit':str(exact),'win_credit_percent':100*float(exact),'ci_low_percent':100*float(lo),'ci_high_percent':100*float(hi),'shared_questions':len(y),'shared_families':len({meta[t]['family_id'] for t in y}),'shared_question_ids':list(y)})

BLUE='#2167A5';INK='#182430';GRAY='#53616D';GRID='#DFE5EA'
plt.rcParams.update({'font.family':'DejaVu Sans','font.size':16,'text.color':INK,'axes.labelcolor':INK,'xtick.color':GRAY,'ytick.color':INK,'svg.fonttype':'none','svg.hashsalt':'om100-social-v1','savefig.facecolor':'white'})
def base(tag,title,subtitle):
 f=plt.figure(figsize=(16,10),dpi=100,facecolor='white');f.text(.055,.944,tag,color=BLUE,size=14,weight='bold',va='top');f.text(.055,.887,title,size=34,weight='bold',va='top');f.text(.055,.817,subtitle,size=17,color=GRAY,va='top');return f
def export(f,stem):
 f.savefig(HERE/(stem+'.png'),dpi=100,facecolor='white');f.savefig(HERE/(stem+'.svg'),facecolor='white',metadata={'Date':None});plt.close(f)
 p=HERE/(stem+'.svg');text=p.read_text();assert 'width="1152pt" height="720pt"' in text;text=text.replace('width="1152pt" height="720pt"','width="1600px" height="1000px"',1);p.write_text(text)

f=base('BENCHMARK 01  /  VERIFIED PROOF COVERAGE','Questions solved in four attempts','Standard prompt · 100 selected public Lean lemmas · 4 samples per question')
ax=f.add_axes([.285,.235,.535,.525]);yy=np.arange(8)[::-1];counts=np.array([r['solved'] for r in card1]);lo=np.array([r['ci_low_count'] for r in card1]);hi=np.array([r['ci_high_count'] for r in card1])
ax.barh(yy,counts,height=.57,color=BLUE,zorder=2);ax.errorbar(counts,yy,xerr=[counts-lo,hi-counts],fmt='none',ecolor=INK,elinewidth=2,capsize=5,capthick=2,zorder=3)
ax.set_xlim(0,100);ax.set_ylim(-.6,7.6);ax.set_yticks(yy,[r['label'] for r in card1],fontsize=18);ax.tick_params(axis='y',length=0,pad=18);ax.set_xticks([0,25,50,75,100],['0','25','50','75','100'],fontsize=15);ax.xaxis.grid(True,color=GRID,linewidth=1);ax.set_axisbelow(True);ax.set_xlabel('Questions solved (out of 100)',fontsize=17,labelpad=14)
for side in ['top','right','left']:ax.spines[side].set_visible(False)
ax.spines['bottom'].set_color(GRID)
for y,r in zip(yy,card1):ax.text(1.045,y,f"{r['solved']} / 100",transform=ax.get_yaxis_transform(),va='center',size=23,weight='bold',color=INK)
f.text(.055,.133,'At least one accepted Lean proof in four samples. All 100 questions count.',size=17)
f.text(.055,.091,'Whiskers: 95% family-bootstrap intervals · 19 source families · selected public corpus',size=15,color=GRAY)
f.text(.055,.050,'High effort · 128,000-token provider ceiling · training exposure unknown',size=15,color=GRAY)
export(f,'01-questions-solved')

f=plt.figure(figsize=(16,10),dpi=100,facecolor='white');f.text(.055,.944,'BENCHMARK 02  /  CONDITIONAL T₀ COMPARISON',color=BLUE,size=14,weight='bold',va='top');f.text(.055,.887,'Compression head-to-head,',size=34,weight='bold',va='top');f.text(.055,.823,'when both solve',size=34,weight='bold',va='top');f.text(.055,.751,'Mathematical-compression prompt · successful proofs only · selected model pairs',size=17,color=GRAY,va='top')
f.text(.055,.714,'Models: Claude Fable 5.1, Claude Opus 5.5 and GPT-6 Astra',size=15,color=GRAY,va='top')
ax=f.add_axes([.35,.296,.39,.355]);ax.set_xlim(0,100);ax.set_ylim(-.5,2.5);ax.set_yticks([]);ax.set_xticks([0,25,50,75,100],[f'{v}%' for v in [0,25,50,75,100]],fontsize=15);ax.tick_params(axis='x',pad=10);ax.set_xlabel('Win credit for the first model',fontsize=17,labelpad=16);ax.xaxis.grid(True,color=GRID,linewidth=1);ax.axvline(50,color=INK,linewidth=1.6,linestyle=(0,(4,4)))
for side in ['top','right','left']:ax.spines[side].set_visible(False)
ax.spines['bottom'].set_color(GRID)
for y,r in zip([2,1,0],card2):
 v=r['win_credit_percent'];ax.errorbar(v,y,xerr=[[v-r['ci_low_percent']],[r['ci_high_percent']-v]],fmt='o',markersize=12,color=BLUE,ecolor=BLUE,elinewidth=3,capsize=7,capthick=2,zorder=4)
 pos=.296+.355*((y+.5)/3)
 f.text(.055,pos+.012,r['label'],size=24,weight='bold',va='center');f.text(.055,pos-.030,f"{r['shared_questions']} shared questions",size=17,color=GRAY,va='center')
 f.text(.79,pos+.014,f"{v:.1f}%",size=31,weight='bold',va='center',color=BLUE);f.text(.79,pos-.033,f"95% CI {r['ci_low_percent']:.1f}–{r['ci_high_percent']:.1f}%",size=15,color=GRAY,va='center')
f.text(.35,.666,'50% = equal win credit; exact ties count half',size=15,color=GRAY)
f.text(.055,.170,'T₀ = expanded dependency references / Lean source tokens, including the statement.',size=17)
f.text(.055,.126,'Each shared question has equal weight; all valid response pairs count. Supports differ by pair.',size=16,color=GRAY)
f.text(.055,.084,'Whiskers: 95% family-bootstrap intervals. Conditional results; no overall reasoning ranking.',size=16,color=GRAY)
f.text(.055,.042,'100 selected public Lean lemmas · high effort · 128k-token ceiling · training exposure unknown',size=15,color=GRAY)
export(f,'02-compression-head-to-head')

posts=["100 selected public Lean lemmas, four standard-prompt attempts: Fable solved 96, Astra 89, GPT-6.1 Sol 84, Opus 81. All eight models + family-bootstrap intervals in the card. High effort; 128k-token ceiling. Training exposure unknown.","Compression when both solve: Fable–Astra 60.9% (77 questions), Fable–Opus 48.7% (78), Opus–Astra 67.1% (70). Exact T₀ wins, ties half; T₀ measures dependency expansion per source token. Intervals and pair-specific coverage are shown. Conditional results, not a reasoning rank."]
assert all(len(p)<=280 for p in posts),[len(p) for p in posts]
(HERE/'draft-posts.md').write_text('# Draft posts — not published\n\n'+ '\n\n'.join(f'## Card {i+1} ({len(p)} characters)\n\n{p}' for i,p in enumerate(posts))+'\n')
inputs={str(p.relative_to(BASE)):sha(p) for p in [INPUT,INDEX,MAIN/'rates.json',MAIN/'pairwise_contests.json',MAIN/'analysis_manifest.json']}
evidence={'schema_version':1,'inputs':inputs,'card_1':card1,'card_2':card2,'independent_checks':'Every displayed point and 95% family-bootstrap interval recomputed directly from saved comparison rows; exact Fraction comparisons for T0.','bootstrap':{'seed':20261014,'replicates':10000,'unit':'19 theorem-family blocks; unstratified; task-weighted ratio recomputed','interval':'percentile 2.5 and 97.5; no empty replicates for selected values'},'draft_post_character_counts':[len(p) for p in posts],'generator_sha256':sha(Path(__file__))}
(HERE/'card-data.json').write_text(json.dumps(evidence,indent=2)+'\n');print(json.dumps({'cards':[r['label'] for r in card2],'post_lengths':[len(p) for p in posts],'outputs':str(HERE)}))
