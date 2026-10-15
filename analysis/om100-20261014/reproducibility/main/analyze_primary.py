#!/usr/bin/env python3
"""Read-only, deterministic post-hoc analysis of the audited OM100 rerun."""
from pathlib import Path
import collections, hashlib, json, math, platform, sys
from fractions import Fraction
from functools import cmp_to_key
import numpy as np
import pandas as pd
from scipy import stats
import statsmodels.api as sm
import patsy

HERE=Path(__file__).resolve().parent
BASE=HERE.parents[2]
OUT=HERE/'results'
INPUT=BASE/'outputs/lean-compression-eval/runs/om100-max-suite-high-20261014/comparison-data.json'
META=Path('/Users/ryanshin/Downloads/openai_math_100_candidates.jsonl')
SEED=20261014
B=10000
CONDITIONS=['standard','mathematical_compression','source_concision']
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def clean(x):
    if isinstance(x,dict):return {str(k):clean(v) for k,v in x.items()}
    if isinstance(x,(list,tuple)):return [clean(v) for v in x]
    if isinstance(x,np.ndarray):return clean(x.tolist())
    if isinstance(x,np.generic):return clean(x.item())
    if isinstance(x,float) and not math.isfinite(x):return None
    return x
def save(name,rows):
    if isinstance(rows,list):pd.DataFrame(rows).to_csv(OUT/(name+'.csv'),index=False)
    (OUT/(name+'.json')).write_text(json.dumps(clean(rows),indent=2,allow_nan=False)+'\n')
def holm(rows,key='p_family',groupkey=None):
    groups=collections.defaultdict(list)
    for i,r in enumerate(rows):
        groups[r.get(groupkey,'all')].append((i,r.get(key)))
    for members in groups.values():
        running=0
        for rank,(i,p) in enumerate(sorted(members,key=lambda x:1. if x[1] is None else x[1])):
            rows[i]['holm_family_size']=len(members)
            if p is None:rows[i]['p_holm']=None;continue
            running=max(running,min(1,p*(len(members)-rank)));rows[i]['p_holm']=running

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    source_pins={str(p):sha(p) for p in [INPUT,META,HERE.parent/'analysis-plan.md']}
    data=json.loads(INPUT.read_text());meta={x['id']:x for x in map(json.loads,META.read_text().splitlines())}
    tasks=sorted(data['tasks']);models=[m['id'] for m in data['models']];ti={t:i for i,t in enumerate(tasks)}
    assert len(tasks)==100 and len(models)==8 and set(tasks)==set(meta)
    families=np.array([meta[t]['family'] for t in tasks]);fg=sorted(set(families));findex={f:i for i,f in enumerate(fg)}
    family_matrix=np.array([[int(f==families[i]) for i in range(100)] for f in fg])
    rng=np.random.default_rng(SEED)
    qw=rng.multinomial(100,np.full(100,.01),size=B).astype(float)
    fw=rng.multinomial(len(fg),np.full(len(fg),1/len(fg)),size=B)@family_matrix
    def summary(y, null=None):
        y=np.asarray(y,dtype=float);ok=np.isfinite(y);n=int(ok.sum())
        if n==0:return {'mean':None,'n_questions':0,'n_families':0,'ci_family_low':None,'ci_family_high':None,'ci_question_low':None,'ci_question_high':None,'p_family':None}
        mean=float(y[ok].mean());z=np.where(ok,y,0);ans={'mean':mean,'n_questions':n,'n_families':len(set(families[ok]))}
        for name,w in [('family',fw),('question',qw)]:
            den=w@ok.astype(float);vals=(w@z)[den>0]/den[den>0];lo,hi=np.quantile(vals,[.025,.975]);ans.update({f'ci_{name}_low':float(lo),f'ci_{name}_high':float(hi),f'empty_{name}_replicates':int((den==0).sum())})
        gs=[float(np.sum(y[ok & (families==f)]-mean)) for f in set(families[ok])];g=len(gs)
        se=math.sqrt(g/(g-1)*np.sum(np.square(gs))/n**2) if g>1 else float('nan')
        ans['se_family']=se
        ans['test_null']=null
        ans['p_family']=float(2*stats.t.sf(abs((mean-null)/se),g-1)) if null is not None and g>=5 and se>0 else (1. if null is not None and g>=5 and se==0 and mean==null else None)
        return ans
    rows=[];lookup={};fractions={}
    for a in data['attempts']:
        key=(a['model'],a['task'],a['condition'],a['sample']);assert key not in lookup;lookup[key]=a
        sl=a['sourceLength'];valid=a['status']=='success';trunc=bool(sl['truncated']);completed=sl['available'] and not trunc
        r={k:a[k] for k in ['model','task','condition','sample','attemptId','status']}
        r.update(family=meta[a['task']]['family'],domain=meta[a['task']]['selection_group'],valid=int(valid),completed=int(completed),truncated=int(trunc),source_chars=sl['sourceNonWhitespaceCharacters'],source_available=sl['available'],source_hash=sl['sourceTextSha256'],cost=float(a['cost']['total_usd']))
        if valid:
            assert a['metricStatus']=='available';m=a['metrics'];ws=int(m['W_S']);wb=int(m['W_B']);us=int(m['U_S_decimal']);ub=int(m['U_B_decimal']);t0=Fraction(us+ub,ws+wb)
            assert t0==Fraction(int(m['T0']['numerator_decimal']),int(m['T0']['denominator_decimal']))
            assert Fraction(wb,ws)==Fraction(int(m['I0']['numerator_decimal']),int(m['I0']['denominator_decimal']))
            fractions[key]=t0
            r.update(W_S=ws,W_B=wb,U_S_decimal=str(us),U_B_decimal=str(ub),log2_T0=math.log2(us+ub)-math.log2(ws+wb),log2_I0=math.log2(wb)-math.log2(ws),log2_W_B=math.log2(wb),log2_total_U=math.log2(us+ub),log2_total_W=math.log2(ws+wb),statement_U_fraction=us/(us+ub))
        rows.append(r)
    df=pd.DataFrame(rows);assert len(df)==9600 and df.attemptId.nunique()==9600 and df.valid.sum()==3831
    assert (df.groupby(['model','task','condition']).size()==4).all()
    df.to_csv(OUT/'primary_attempts.csv',index=False)
    save('task_metadata',[{k:meta[t][k] for k in ['id','family','family_title','selection_group','subject','source_path','declaration_name','title']} for t in tasks])
    def vector(s):return s.reindex(tasks).to_numpy(dtype=float)
    rate_rows=[];rate_arrays={};status_rows=[]
    for model in models:
        for cond in CONDITIONS+['all']:
            sub=df[(df.model==model)&((df.condition==cond) if cond!='all' else True)]
            for measure in ['valid','completed','truncated','pass4','accepted_given_complete']:
                if measure=='pass4':
                    if cond=='all':continue
                    v=vector(sub.groupby('task').valid.max())
                elif measure=='accepted_given_complete':
                    # Macro average conditional acceptance across questions with >=1 complete submission.
                    v=vector(sub.groupby('task').apply(lambda g:g.valid.sum()/g.completed.sum() if g.completed.sum() else np.nan,include_groups=False))
                else:v=vector(sub.groupby('task')[measure].mean())
                rate_arrays[model,cond,measure]=v
                rate_rows.append({'model':model,'condition':cond,'measure':measure,**summary(v),'attempts':len(sub),'accepted_count':int(sub.valid.sum()),'completed_count':int(sub.completed.sum()),'truncated_count':int(sub.truncated.sum()),'micro_completed_acceptance':sub.valid.sum()/sub.completed.sum() if sub.completed.sum() else None,'cost_usd':sub.cost.sum()})
            status_rows.extend({'model':model,'condition':cond,'status':s,'count':int(n)} for s,n in sub.status.value_counts().items())
    save('rates',rate_rows);save('statuses',status_rows)
    # Paired model differences on the same questions; family is the resampling unit.
    pair_yield=[]
    for cond in CONDITIONS:
        for i,m in enumerate(models):
            for n in models[i+1:]:
                pair_yield.append({'condition':cond,'model_a':m,'model_b':n,**summary(rate_arrays[m,cond,'valid']-rate_arrays[n,cond,'valid'],null=0)})
    holm(pair_yield,groupkey='condition');save('model_yield_differences',pair_yield)
    # Original-plan prompt contrasts, equal question weighting.
    effects=[];metric_means=df[df.valid==1].groupby(['model','condition','task'])[['log2_T0','log2_I0','log2_W_B','log2_total_U','log2_total_W']].mean()
    contrasts=[('mathematical_compression','standard'),('source_concision','standard'),('mathematical_compression','source_concision')]
    for model in models:
        for a,b in contrasts:
            effects.append({'model':model,'condition_a':a,'condition_b':b,'outcome':'valid_yield',**summary(rate_arrays[model,a,'valid']-rate_arrays[model,b,'valid'],null=0)})
            for col in metric_means.columns:
                x=metric_means.loc[(model,a)][col].reindex(tasks);y=metric_means.loc[(model,b)][col].reindex(tasks)
                s=summary((x-y).to_numpy(),null=0);effects.append({'model':model,'condition_a':a,'condition_b':b,'outcome':col,**s,'geometric_ratio':2**s['mean'] if s['mean'] is not None else None,'ratio_family_low':2**s['ci_family_low'] if s['ci_family_low'] is not None else None,'ratio_family_high':2**s['ci_family_high'] if s['ci_family_high'] is not None else None})
    holm(effects,groupkey='outcome');save('prompt_effects',effects)
    # Descriptive domain/family performance; no new population extrapolation.
    domain=[]
    for (model,cond,dom),sub in df.groupby(['model','condition','domain']):
        domain.append({'model':model,'condition':cond,'domain':dom,'questions':sub.task.nunique(),'attempts':len(sub),'valid_yield':sub.valid.mean(),'pass4':sub.groupby('task').valid.max().mean(),'truncation':sub.truncated.mean()})
    save('domain_results',domain)
    family=[]
    for (model,cond,f),sub in df.groupby(['model','condition','family']):family.append({'model':model,'condition':cond,'family':f,'questions':sub.task.nunique(),'valid_yield':sub.valid.mean(),'pass4':sub.groupby('task').valid.max().mean()})
    save('family_results',family)
    # Exact all-response joint contests, and conditional valid contests.
    contest=[];pairs=[];score_arrays={};rank_sensitivity=[]
    for cond in CONDITIONS:
        for metric in ['T0','W_B']:
            scores=np.full((8,8,100),np.nan);parts={x:np.full_like(scores,np.nan) for x in ['reliability','compression','both_failed','both_valid']}
            for i,m in enumerate(models):
                for j,n in enumerate(models):
                    if i>=j:continue
                    pure=np.full(100,np.nan)
                    for qi,t in enumerate(tasks):
                        aa=[lookup[m,t,cond,k] for k in range(1,5)];bb=[lookup[n,t,cond,k] for k in range(1,5)]
                        pa=sum(a['status']=='success' for a in aa)/4;pb=sum(a['status']=='success' for a in bb)/4
                        total=0.;vv=0.;nv=0
                        for a in aa:
                            for b in bb:
                                va=a['status']=='success';vb=b['status']=='success'
                                if va and vb:
                                    av=fractions[m,t,cond,a['sample']] if metric=='T0' else -a['metrics']['W_B'];bv=fractions[n,t,cond,b['sample']] if metric=='T0' else -b['metrics']['W_B']
                                    pts=1. if av>bv else (0. if av<bv else .5);vv+=pts;nv+=1
                                elif va:pts=1.
                                elif vb:pts=0.
                                else:pts=.5
                                total+=pts
                        v=total/16;scores[i,j,qi]=v;scores[j,i,qi]=1-v
                        rel=.5*(pa-pb);comp=(vv/16-.5*pa*pb)
                        assert abs(v-(.5+rel+comp))<1e-12
                        if nv:pure[qi]=vv/nv
                        for a,b,sign in [(i,j,1),(j,i,-1)]:
                            parts['reliability'][a,b,qi]=sign*rel;parts['compression'][a,b,qi]=sign*comp;parts['both_failed'][a,b,qi]=(1-pa)*(1-pb);parts['both_valid'][a,b,qi]=pa*pb
                    pairs.append({'condition':cond,'metric':metric,'model_a':m,'model_b':n,'kind':'joint',**summary(scores[i,j])})
                    pairs.append({'condition':cond,'metric':metric,'model_a':m,'model_b':n,'kind':'valid_only',**summary(pure)})
            for i,m in enumerate(models):
                v=np.nanmean(scores[i],axis=0);score_arrays[m,cond,metric]=v
                row={'model':m,'condition':cond,'metric':metric,**summary(v)}
                for part,arr in parts.items():row[part]=float(np.nanmean(arr[i]))
                row['reliability_only_score']=.5+row['reliability'];assert abs(row['mean']-row['reliability_only_score']-row['compression'])<1e-12
                contest.append(row)
                for f in fg:rank_sensitivity.append({'model':m,'condition':cond,'metric':metric,'left_out_family':f,'score':float(v[families!=f].mean())})
    save('joint_contest',contest);save('pairwise_contests',pairs);save('contest_leave_family_out',rank_sensitivity)
    score_diff=[]
    for i,m in enumerate(models):
        for n in models[i+1:]:score_diff.append({'model_a':m,'model_b':n,**summary(score_arrays[m,'mathematical_compression','T0']-score_arrays[n,'mathematical_compression','T0'],null=0)})
    holm(score_diff);save('joint_contest_differences',score_diff)
    # Question-geometric-mean pure ranks on explicit fixed shared supports.
    rankrows=[];coverage=[];rankperq=[]
    valid=df[df.valid==1]
    for cond in CONDITIONS:
        cv={m:set(valid[(valid.model==m)&(valid.condition==cond)].task) for m in models}
        for gate in [0,25,50,75]:
            chosen=[m for m in models if len(cv[m])>=gate];shared=sorted(set.intersection(*(cv[m] for m in chosen))) if chosen else []
            coverage.append({'condition':cond,'coverage_gate':gate,'models':chosen,'n_models':len(chosen),'n_shared':len(shared),'tasks':shared})
            if len(chosen)<2 or not shared:continue
            for metric in ['T0','W_B','I0']:
                per={m:np.full(100,np.nan) for m in chosen}
                for t in shared:
                    vals={}
                    for m in chosen:
                        vv=[a for k,a in lookup.items() if k[0]==m and k[1]==t and k[2]==cond and a['status']=='success']
                        pr=Fraction(1)
                        for a in vv:
                            mm=a['metrics'];pr*=fractions[m,t,cond,a['sample']] if metric=='T0' else (Fraction(mm['W_B']) if metric=='W_B' else Fraction(mm['W_B'],mm['W_S']))
                        vals[m]=(pr,len(vv))
                    def cmp(a,b):
                        x,na=vals[a];y,nb=vals[b];xx=x**nb;yy=y**na
                        return ((xx>yy)-(xx<yy))*(-1 if metric!='W_B' else 1)
                    ordered=sorted(chosen,key=cmp_to_key(cmp));idx=0
                    while idx<len(ordered):
                        end=idx+1
                        while end<len(ordered) and cmp(ordered[idx],ordered[end])==0:end+=1
                        rank=(idx+1+end)/2
                        for m in ordered[idx:end]:per[m][ti[t]]=rank;rankperq.append({'condition':cond,'coverage_gate':gate,'metric':metric,'task':t,'model':m,'rank':rank})
                        idx=end
                for m,v in per.items():rankrows.append({'condition':cond,'coverage_gate':gate,'metric':metric,'model':m,'n_compared_models':len(chosen),**summary(v)})
    save('coverage',coverage);save('conditional_ranks',rankrows);save('conditional_ranks_by_question',rankperq)
    # Source length versus correctness: matched cell fixed effects.
    length_results=[]
    for selection in ['completed','completed_excluding_format','include_partial']:
        sub=df[df.source_available & (df.source_chars>0)].copy()
        if selection!='include_partial':sub=sub[sub.truncated==0]
        if selection=='completed_excluding_format':sub=sub[sub.status!='parse_failure']
        sub['x']=np.log2(sub.source_chars);sub['y']=sub.valid.astype(float)
        for sample in ['all','standard','mathematical_compression','source_concision']:
            ss=sub if sample=='all' else sub[sub.condition==sample]
            ss=ss.copy();group=ss.groupby(['model','condition','task'])
            ss['xr']=ss.x-group.x.transform('mean');ss['yr']=ss.y-group.y.transform('mean')
            ss['xy']=ss.xr*ss.yr;ss['xx']=ss.xr**2
            totals=ss.groupby('task')[['xy','xx']].sum().reindex(tasks,fill_value=0)
            beta=totals.xy.sum()/totals.xx.sum();ans={'population':selection,'condition':sample,'n':len(ss),'n_cells':group.ngroups,'n_varying_validity_cells':int((group.y.nunique()>1).sum()),'slope_probability_per_doubling':float(beta),'raw_point_biserial_log_length':float(stats.pointbiserialr(ss.y,ss.x).statistic),'estimand':'Pooled within-cell covariance/variance slope; not an equal-question mean effect'}
            for name,w in [('family',fw),('question',qw)]:
                den=w@totals.xx.to_numpy();values=(w@totals.xy.to_numpy())[den>0]/den[den>0];lo,hi=np.quantile(values,[.025,.975]);ans[f'ci_{name}_low']=float(lo);ans[f'ci_{name}_high']=float(hi)
            length_results.append(ans)
    save('source_length_associations',length_results)
    # Split-sample metric/correctness association, selected on early valid proof.
    split_results=[]
    for first,last,label in [([1,2],[3,4],'forward'),([3,4],[1,2],'reverse')]:
        early=df[(df['sample'].isin(first))&(df.valid==1)].groupby(['model','condition','task'])[['log2_T0','log2_W_B']].mean()
        later=df[df['sample'].isin(last)].groupby(['model','condition','task']).valid.mean().rename('heldout_yield')
        ss=early.join(later,how='inner').reset_index();ss['family']=ss.task.map(lambda t:meta[t]['family'])
        for metric in ['log2_T0','log2_W_B']:
            y,X=patsy.dmatrices('heldout_yield ~ '+metric+' + C(task) + C(model)*C(condition)',ss,return_type='dataframe')
            fit=sm.OLS(y,X).fit(cov_type='cluster',cov_kwds={'groups':ss.family.to_numpy(),'use_correction':True,'df_correction':True},use_t=True)
            lo,hi=fit.conf_int().loc[metric];split_results.append({'split':label,'metric':metric,'groups':len(ss),'questions':ss.task.nunique(),'families':ss.family.nunique(),'coefficient_probability_per_bit':float(fit.params[metric]),'ci_family_low':float(lo),'ci_family_high':float(hi),'p_family':float(fit.pvalues[metric]),'design_rank':int(np.linalg.matrix_rank(X)),'selection':'At least one valid measurable response in the early half; held-out yield over both later scheduled samples.'})
    holm(split_results);save('split_sample_associations',split_results)
    # Within-model/condition log metrics and source distributions, cost efficiency.
    descriptive=[]
    for (model,cond),ss in df.groupby(['model','condition']):
        vv=ss[ss.valid==1];cc=ss[(ss.completed==1)&(ss.source_chars>0)]
        descriptive.append({'model':model,'condition':cond,'valid':len(vv),'cost_usd':ss.cost.sum(),'usd_per_accepted':ss.cost.sum()/len(vv) if len(vv) else None,'cost_per_scheduled':ss.cost.mean(),'median_valid_W_B':vv.W_B.median(),'median_valid_source_chars':vv.source_chars.median(),'median_invalid_completed_chars':cc[cc.valid==0].source_chars.median(),'median_log2_T0_valid':vv.log2_T0.median(),'p95_log2_T0_valid':vv.log2_T0.quantile(.95),'min_log2_T0_valid':vv.log2_T0.min(),'max_log2_T0_valid':vv.log2_T0.max()})
    save('descriptive',descriptive)
    assert all(sha(Path(p))==v for p,v in source_pins.items()),'Input modified during analysis'
    save('analysis_manifest',{'status':'complete','seed':SEED,'bootstrap_replicates':B,'question_count':100,'family_count':len(fg),'model_count':8,'rows':len(df),'accepted':int(df.valid.sum()),'completed':int(df.completed.sum()),'source_pins':source_pins,'analysis_source_sha256':sha(Path(__file__)),'runtime':{'python':sys.version,'numpy':np.__version__,'pandas':pd.__version__},'scope':'Post-hoc exploratory analysis; original input files unchanged; no model or Lean calls.'})
    print(json.dumps({'status':'complete','rows':len(df),'accepted':int(df.valid.sum()),'outputs':str(OUT)}))
if __name__=='__main__':main()
