#!/usr/bin/env python3
from pathlib import Path
import json,math
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.ticker import PercentFormatter

HERE=Path(__file__).resolve().parent;BASE=HERE.parents[2];R=HERE/'results';C=HERE.parent/'cross-run/results-v1';O=BASE/'outputs/lean-compression-paper-20261014/paper/figures';O.mkdir(parents=True,exist_ok=True)
ORDER=['claude-fable-5-1','gpt-6-astra','gpt-6.1-sol','claude-opus-5-5','gpt-6-sol','gpt-6-luna','claude-haiku-5-5','claude-sonnet-5-5']
NAME=dict(zip(ORDER,['Fable 5.1','GPT-6 Astra','GPT-6.1 Sol','Opus 5.5','GPT-6 Sol','GPT-6 Luna','Haiku 5.5','Sonnet 5.5']))
BLUE='#17658b';TEAL='#087e73';ORANGE='#c16624';RED='#a44445';GREY='#77818b';COND=['standard','mathematical_compression','source_concision'];CN=['Standard','Compression','Concision']
plt.rcParams.update({'font.family':'DejaVu Sans','font.size':13,'axes.titlesize':15,'axes.labelsize':13,'xtick.labelsize':12,'ytick.labelsize':12,'axes.spines.top':False,'axes.spines.right':False,'axes.edgecolor':'#aab1b8','text.color':'#1e2933','axes.labelcolor':'#1e2933','xtick.color':'#3b4855','ytick.color':'#3b4855','pdf.fonttype':42,'svg.fonttype':'none','figure.dpi':140,'savefig.dpi':200})
def save(fig,name):
    for ext in ['pdf','png','svg']:fig.savefig(O/(name+'.'+ext),bbox_inches='tight',facecolor='white')
    plt.close(fig)
def frame(ax):ax.grid(axis='x',color='#e4e8ec',linewidth=.7,zorder=0);ax.set_axisbelow(True)
rates=pd.read_csv(R/'rates.csv');effects=pd.read_csv(R/'prompt_effects.csv');df=pd.read_csv(R/'primary_attempts.csv');pairs=pd.read_csv(R/'pairwise_contests.csv')

fig,axs=plt.subplots(1,2,figsize=(11,4.8),sharey=True,layout='constrained')
for ax,measure,title in zip(axs,['valid','pass4'],['Accepted responses / 100 attempts','Questions solved in four attempts / 100']):
    ss=rates[(rates.condition=='standard')&(rates.measure==measure)].set_index('model').loc[ORDER];y=np.arange(8);x=ss['mean'].values*100
    ax.errorbar(x,y,xerr=np.array([ss['mean']-ss.ci_family_low,ss.ci_family_high-ss['mean']])*100,fmt='o',color=BLUE,ecolor='#859faf',capsize=3,markersize=6,zorder=3)
    for xx,yy in zip(x,y):ax.text(xx+1.5,yy-.16,f'{xx:.1f}' if measure=='valid' else f'{xx:.0f}',fontsize=12)
    ax.set_title(title,loc='left');ax.set_xlim(0,105);ax.set_xticks([0,25,50,75,100]);frame(ax)
axs[0].set_yticks(range(8),[NAME[m] for m in ORDER]);axs[0].invert_yaxis();fig.supxlabel('Standard prompt; 400 responses per model. Whiskers: 95% family-block bootstrap intervals.',fontsize=12)
save(fig,'01_reliability')

fig,ax=plt.subplots(figsize=(10.6,5.2),layout='constrained');y=np.arange(8)
for j,(a,color,label) in enumerate([('mathematical_compression',BLUE,'Compression minus standard'),('source_concision',ORANGE,'Concision minus standard')]):
    ss=effects[(effects.outcome=='valid_yield')&(effects.condition_a==a)&(effects.condition_b=='standard')].set_index('model').loc[ORDER];x=ss['mean'].values*100;yy=y+(j-.5)*.24
    ax.errorbar(x,yy,xerr=np.array([ss['mean']-ss.ci_family_low,ss.ci_family_high-ss['mean']])*100,fmt='o',color=color,capsize=3,label=label)
ax.axvline(0,color='#555',lw=.8);ax.set_yticks(y,[NAME[m] for m in ORDER]);ax.invert_yaxis();ax.set_xlabel('Change in scheduled accepted-proof yield (percentage points)');ax.set_title('Prompt instructions change acceptance differently across models',loc='left');ax.legend(loc='lower right',frameon=False);frame(ax);save(fig,'02_prompt_yield')

fig,axs=plt.subplots(1,2,figsize=(11.5,5.5),sharey=True,layout='constrained')
for ax,metric,title in zip(axs,['log2_T0','log2_W_B'],['Paper T0 ratio (higher)','Proof-source ratio (lower is shorter)']):
    for j,(a,color,label) in enumerate([('mathematical_compression',BLUE,'Compression / standard'),('source_concision',ORANGE,'Concision / standard')]):
        ss=effects[(effects.outcome==metric)&(effects.condition_a==a)&(effects.condition_b=='standard')].set_index('model').loc[ORDER];x=ss.geometric_ratio.values;yy=np.arange(8)+(j-.5)*.23
        ax.errorbar(x,yy,xerr=np.array([x-ss.ratio_family_low,ss.ratio_family_high-x]),fmt='o',color=color,capsize=2,label=label)
    ax.axvline(1,color='#555',lw=.8);ax.set_xscale('log');ax.set_title(title,fontsize=13,loc='left');frame(ax)
axs[0].set_xlim(.6,4.7);axs[1].set_xlim(.25,2);axs[0].set_yticks(range(8),[NAME[m] for m in ORDER]);axs[0].invert_yaxis();axs[1].legend(loc='lower left',frameon=False,fontsize=11);fig.supxlabel('Paired questions with a valid measured proof in both prompts; 95% family-block intervals. Supports vary.',fontsize=12);save(fig,'03_prompt_metrics')

mat=np.full((8,8),np.nan);nmat=np.zeros((8,8),int)
for _,r in pairs[(pairs.condition=='mathematical_compression')&(pairs.metric=='T0')&(pairs.kind=='valid_only')].iterrows():
    i=ORDER.index(r.model_a);j=ORDER.index(r.model_b);mat[i,j]=100*r['mean'];mat[j,i]=100*(1-r['mean']);nmat[i,j]=nmat[j,i]=int(r.n_questions)
fig,ax=plt.subplots(figsize=(10,8),layout='constrained');im=ax.imshow(np.where(nmat>=10,mat,np.nan),cmap='RdBu',vmin=0,vmax=100);ax.set_facecolor('#eef0f3')
for i in range(8):
    for j in range(8):
        if i==j:s='—'
        elif np.isnan(mat[i,j]):s='No overlap'
        else:s=f'{mat[i,j]:.0f}%'+('†' if nmat[i,j]<10 else '')+f'\nn={nmat[i,j]}'
        ax.text(j,i,s,ha='center',va='center',fontsize=12,color='white' if np.isfinite(mat[i,j]) and nmat[i,j]>=10 and abs(mat[i,j]-50)>28 else '#162333')
ax.set_xticks(range(8),[NAME[m] for m in ORDER],rotation=40,ha='right');ax.set_yticks(range(8),[NAME[m] for m in ORDER]);ax.set_title('Conditional T0 win rate: row model versus column model',loc='left');fig.colorbar(im,ax=ax,label='Win points (%) among valid-vs-valid comparisons',shrink=.75);fig.supxlabel('Compression prompt. Equal weight per overlapping question, then all valid response pairs; ties count half.\nEach pair selects different questions; n = shared questions. Grey cells (†): fewer than 10 shared questions.',fontsize=12);save(fig,'04_pairwise_compression')

fig,axs=plt.subplots(1,2,figsize=(11,4.7),sharey=True,layout='constrained');cr=pd.read_csv(C/'outcome_estimates_family.csv')
for ax,metric,title in zip(axs,['scheduled_success_rate','truncation_rate'],['Scheduled accepted-proof yield','Responses reaching the output ceiling']):
    for j,(run,color,label) in enumerate([('cap8192',GREY,'8,192 ceiling'),('cap128000',BLUE,'128,000 ceiling')]):
        ss=cr[(cr.run==run)&(cr.condition=='ALL_CONDITIONS')&(cr.metric==metric)&(cr.model!='ALL_MODELS')].set_index('model')
        if len(ss)!=8:
            ss=cr[(cr.run==run)&(cr.condition=='all')&(cr.metric==metric)&(cr.model!='ALL_MODELS')].set_index('model')
        if len(ss)!=8:raise ValueError(('cross schema conditions',cr.condition.unique().tolist()))
        ss=ss.loc[ORDER];yy=np.arange(8)+(j-.5)*.22;xx=ss.estimate.values*100
        ax.errorbar(xx,yy,xerr=np.array([ss.estimate-ss.ci_low,ss.ci_high-ss.estimate])*100,fmt='o',color=color,capsize=2,label=label)
    ax.set_title(title,loc='left');ax.set_xlim(-2,103);ax.xaxis.set_major_formatter(PercentFormatter());frame(ax)
axs[0].set_yticks(range(8),[NAME[m] for m in ORDER]);axs[0].invert_yaxis();axs[1].legend(frameon=False,fontsize=12);fig.supxlabel('All three prompts, 1,200 scheduled responses/model/run. Separate cohorts; service tier and transport also changed.',fontsize=12);save(fig,'05_budget_comparison')

fig,ax=plt.subplots(figsize=(10,4.2),layout='constrained');lr=pd.read_csv(R/'source_length_associations.csv');ss=lr[lr.condition=='all'].set_index('population').loc[['completed','completed_excluding_format','include_partial']]
y=np.arange(3);xx=ss.slope_probability_per_doubling.values*100
ax.errorbar(xx,y,xerr=np.array([ss.slope_probability_per_doubling-ss.ci_family_low,ss.ci_family_high-ss.slope_probability_per_doubling])*100,fmt='o',color=TEAL,capsize=4,markersize=7)
ax.set_yticks(y,['Completed, nonempty\n(n = 9,477)','Completed, excluding parse failures\n(n = 6,809)','Include nonempty truncated partials\n(n = 9,501)']);ax.invert_yaxis();ax.axvline(0,color='#555',lw=.8);ax.set_xlabel('Acceptance association per doubling of nonwhitespace characters (percentage points)');ax.set_title('The source-length association reverses after excluding parse failures',loc='left');frame(ax);fig.supxlabel('Within question × model × prompt fixed-cell slope; pooled covariance/variance weighting.\n95% family-block bootstrap intervals. This is an association, not an effect of shortening a proof.',fontsize=12);save(fig,'06_length_association')

status=pd.read_csv(R/'statuses.csv');fig,ax=plt.subplots(figsize=(10.7,4.8),layout='constrained');st=status[status.condition=='all'].pivot(index='model',columns='status',values='count').fillna(0).reindex(ORDER);left=np.zeros(8)
groups=[('Accepted',['success'],TEAL),('Parse failure',['parse_failure'],ORANGE),('Elaboration failure',['elaboration_failure'],BLUE),('Truncated',['truncated_output'],'#c4a867'),('Other',['verification_timeout','verification_resource_limit','disallowed_axiom'],GREY)]
for label,cols,color in groups:
    vals=st.reindex(columns=cols,fill_value=0).sum(axis=1).values/12;ax.barh(range(8),vals,left=left,label=label,color=color,height=.65);left+=vals
ax.set_xlim(0,100);ax.set_yticks(range(8),[NAME[m] for m in ORDER]);ax.invert_yaxis();ax.set_xlabel('Share of 1,200 scheduled responses (%)');ax.set_title('Low acceptance often reflects output-format failures',loc='left');ax.legend(loc='upper center',bbox_to_anchor=(.5,-.13),ncol=3,frameon=False,fontsize=12);save(fig,'07_outcomes')

fig,axs=plt.subplots(1,2,figsize=(11,4.1),layout='constrained');out=json.loads((HERE.parent/'methods/outlier-audit.json').read_text())['records']
for ax,t in zip(axs,['OM100-065','OM100-100']):
    lo=next(x for x in out if x['task']==t and x['direction']=='min')['metrics'];hi=next(x for x in out if x['task']==t and x['direction']=='max')['metrics'];du=math.log2(int(hi['U_S_decimal'])+int(hi['U_B_decimal']))-math.log2(int(lo['U_S_decimal'])+int(lo['U_B_decimal']));dw=math.log2(lo['W_S']+lo['W_B'])-math.log2(hi['W_S']+hi['W_B'])
    ax.barh([0],[du],height=.24,color=ORANGE,label='Larger expanded dependency numerator');ax.barh([0],[dw],height=.24,left=[du],color=BLUE,label='Smaller total source denominator');ax.set_xlim(0,56);ax.set_yticks([]);ax.set_title(t,loc='left');ax.set_xlabel('Contribution to increase in log2 T0 (bits)');ax.text(du/2,0,f'{du:.2f} bits',ha='center',va='center',color='white',weight='bold');ax.text(du+dw+1,0,f'{dw:.2f}',va='center');ax.text(0,.36,f'Proof tokens: {lo["W_B"]:,} → {hi["W_B"]:,}',fontsize=13);ax.set_ylim(-.35,.55)
handles,labels=axs[0].get_legend_handles_labels();fig.legend(handles,labels,loc='lower center',bbox_to_anchor=(.5,-.13),frameon=False,ncol=1,fontsize=12);save(fig,'08_dependency_cases')

desc=pd.read_csv(R/'descriptive.csv');fig,axs=plt.subplots(1,2,figsize=(11,4.5),sharey=True,layout='constrained');ss=desc.groupby('model')[['cost_usd','valid']].sum().reindex(ORDER);cost=ss.cost_usd;per=cost/ss.valid
for ax,values,title in zip(axs,[cost,per],['Recorded-response API cost (USD)','Estimated API cost per accepted proof (USD)']):
    ax.scatter(values,range(8),color=BLUE,s=36,zorder=3);ax.set_xscale('log');ax.set_title(title,loc='left',fontsize=11)
    for i,v in enumerate(values):ax.text(v*1.04,i,f'${v:,.2f}',va='center',fontsize=12)
    ax.set_xlim(min(values)*.7,max(values)*1.8);frame(ax)
axs[0].set_yticks(range(8),[NAME[m] for m in ORDER]);axs[0].invert_yaxis();fig.supxlabel('All three prompts. Frozen delivered-tier estimates; 30 original failed-request charges and cloud costs excluded.',fontsize=12);save(fig,'09_costs')

rr=pd.read_csv(R/'conditional_ranks.csv');ss=rr[(rr.condition=='mathematical_compression')&(rr.coverage_gate==25)&(rr.metric=='T0')].sort_values('mean');fig,ax=plt.subplots(figsize=(9.5,4.5),layout='constrained');x=ss['mean'].values
ax.errorbar(x,range(len(ss)),xerr=np.array([x-ss.ci_family_low,ss.ci_family_high-x]),fmt='o',color=BLUE,capsize=3);ax.set_yticks(range(len(ss)),[NAME[m] for m in ss.model]);ax.invert_yaxis();ax.set_xlim(.75,6.25);ax.set_xticks(range(1,7));ax.set_xlabel('Average within-question rank (1 = highest T0)');ax.set_title('Conditional six-model ranking on 23 shared questions',loc='left');frame(ax);fig.supxlabel('Models first required measurable proofs on ≥25/100 compression questions.\nThis outcome-selected subset is a sensitivity view, not a universal eight-model ranking.',fontsize=12);save(fig,'10_conditional_rank')

family=pd.read_csv(R/'family_results.csv');ss=family[family.condition=='standard'].pivot(index='family',columns='model',values='valid_yield').reindex(columns=ORDER);fig,ax=plt.subplots(figsize=(10,8),layout='constrained');im=ax.imshow(ss.values*100,vmin=0,vmax=100,cmap='Blues',aspect='auto');ax.set_yticks(range(len(ss)),[f'Family {int(x):03d}' for x in ss.index]);ax.set_xticks(range(8),[NAME[m] for m in ORDER],rotation=35,ha='right');ax.set_title('Acceptance varies across the 19 source-result families',loc='left');fig.colorbar(im,ax=ax,label='Scheduled accepted-proof yield (%)',shrink=.8);save(fig,'11_family_heatmap')

(O/'figure_manifest.json').write_text(json.dumps({'figures':sorted(x.stem for x in O.glob('*.pdf')),'source':'Reviewed summary tables and exact outlier records; no values estimated from screenshots.','intervals':'10,000 family-block percentile replicates unless explicitly stated.'},indent=2))
print('Wrote',len(list(O.glob('*.pdf'))),'publication figures')
