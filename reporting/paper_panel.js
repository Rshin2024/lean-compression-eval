// Presentation only: all observations come from the reviewed, frozen DTO.
let paperMetric='T0', paperRankingReady=false, paperResult=null, paperOptions=null;
const PAPER_LABEL={T0:'T₀',I0:'I₀',J0:'J₀',W_B:'Wᴮ'};
const paperNode=id=>document.getElementById(id);
function paperSettings(){
 return {modelIds:selected().map(m=>m.id),condition:paperNode('paperCondition').value,
  metric:paperMetric,aggregation:paperNode('paperAggregation').value,
  minCoverage:Number(paperNode('paperCoverage').value),minSamples:Number(paperNode('paperMinSamples').value),
  sample:paperNode('paperSample').value==='all'?'all':Number(paperNode('paperSample').value),
  maxProofTokens:paperNode('paperMaxProof').value==='none'?null:Number(paperNode('paperMaxProof').value),
  questionIds:paperNode('paperQuestionMode').value==='all'?null:Array.from(paperNode('paperQuestions').selectedOptions,x=>x.value),
  beta:Number(paperNode('paperBeta').value)/100};
}
function paperScientific(log){
 if(!finite(log))return '—';
 const exponent=Math.floor(log*Math.LOG10E*Math.LN2),mantissa=10**(log*Math.LOG10E*Math.LN2-exponent);
 return exponent>=5||exponent<=-3?`${mantissa.toFixed(2)} × 10<sup>${exponent}</sup>`:num(2**log);
}
function paperDownloadJson(){
 if(!paperResult?.rows.length)return;
 const payload={schemaVersion:1,kind:'exploratory_paper_metric_ranking',paper:'https://arxiv.org/abs/2603.20396',
  sourceBuilt:DATA.built,sourceAnalyses:DATA.sources,verification:DATA.verification,
  definitions:{T0:'(U_S+U_B)/(W_S+W_B)',I0:'W_B/W_S',W_B:'proof-body source tokens; lower first',
   J0:'beta*(per-question geometric T0 / cohort maximum T0)+(1-beta)*(per-question geometric I0 / cohort maximum I0)',
   withinQuestion:'geometric mean over the same usable samples',rankAggregation:'arithmetic mean of within-question midranks, equal question weights',
   magnitudeAggregation:'equal-question geometric mean, except J0 arithmetic mean',
   scope:'valid measurable outputs only; no reference-proof comparison; no PageRank I1; no uncertainty intervals'},
  controls:paperOptions,result:paperResult};
 const url=URL.createObjectURL(new Blob([JSON.stringify(payload,null,2)],{type:'application/json'}));
 const a=document.createElement('a');a.href=url;a.download='paper-metric-ranking-'+paperMetric+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
function renderPaperRanking(){
 if(!paperRankingReady)return;
 const o=paperSettings(),r=PaperRankings.compute(DATA,o);paperOptions=o;paperResult=r;
 const isRank=o.aggregation==='rank',isBlend=o.metric==='J0',lower=o.metric==='W_B',sampleSlots=o.sample==='all'?S:1;
 document.querySelectorAll('[data-paper-metric]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.paperMetric===o.metric)));
 paperNode('paperBetaBox').hidden=!isBlend;
 paperNode('paperBetaValue').textContent=`${Math.round(o.beta*100)}% T₀ / ${100-Math.round(o.beta*100)}% I₀`;
 paperNode('paperQuestionChoices').hidden=o.questionIds===null;
 paperNode('paperDefinition').textContent=({
  T0:'Higher T₀ = more expanded references represented per source token. This is the paper’s reductive compression ratio; it can increase through heavier library use.',
  I0:'Higher I₀ = a longer proof relative to its statement. On these fixed questions it rewards longer proofs, not concision.',
  J0:'Higher J₀ = a larger weighted blend of normalized T₀ and I₀. Change β to explore how the two objectives affect the order.',
  W_B:'Lower Wᴮ = fewer Lean source tokens in the proof body. This ranks proof concision separately from the paper’s interest measures.'})[o.metric];
 const labels=r.excluded.map(m=>`${NAME(m.id)} (${m.coverage}/${N})`);
 paperNode('paperScope').innerHTML=`<strong>${r.tasks.length} shared questions</strong> · ${r.cohort.length} compared models · ${LABEL[o.condition]}`+
  `<p>${o.minSamples}+ usable sample${o.minSamples===1?'':'s'} per model and question · ${o.sample==='all'?'all saved samples':'sample '+o.sample+' only'}${o.maxProofTokens===null?'':` · proof body ≤ ${o.maxProofTokens} tokens`}${o.questionIds===null?'':` · ${o.questionIds.length} questions selected`}.</p>`+
  '';
 paperNode('paperExclusions').textContent=labels.length?`Below ${o.minCoverage}-question model coverage: ${labels.join(' · ')}. These models remain in the full validity and cost views.`:'';
 paperNode('paperExport').disabled=!r.rows.length;
 renderCompressionValidity();
 if(!r.rows.length){
  paperNode('paperLeaderboard').innerHTML='<p class="empty">'+(r.cohort.length<2?'At least two models must meet the coverage requirement. Lower model coverage or relax the sample/length filters.':'No selected questions have enough usable samples for every compared model. Choose fewer models or relax the filters; no ranking is assigned.')+'</p>';
  paperNode('paperReading').textContent='Missing or invalid proofs receive no compression score. An empty shared question set cannot support an aggregate comparison.';
  paperNode('paperQuestionTable').innerHTML=table(['Model','Questions passing filters','In compared group'],selected().map(m=>[esc(NAME(m.id)),`${r.coverage[m.id]??0}/${N}`,r.cohort.includes(m.id)?'Yes':'Below coverage threshold']));return;
 }
 const values=r.rows.map(x=>isRank?x.averageRank:isBlend?x.value:x.metricLog2);
 let lo=isRank?1:isBlend?0:Math.floor(Math.min(...values)),hi=isRank?r.rows.length:isBlend?1:Math.ceil(Math.max(...values));
 if(hi===lo){lo-=.5;hi+=.5;}
 const reverse=isRank||lower;
 const left=isRank?String(r.cohort.length):isBlend?'0':`${reverse?hi:lo}`,right=isRank?'1':isBlend?'1':`${reverse?lo:hi}`;
 let body='';
 for(const row of r.rows){
  const score=isRank?row.averageRank:isBlend?row.value:row.metricLog2;
  const pos=100*(reverse?(hi-score)/(hi-lo):(score-lo)/(hi-lo));
  const same=r.rows.filter(x=>x.place===row.place).length>1;
  const main=isRank?row.averageRank.toFixed(2):isBlend?row.value.toFixed(3):paperScientific(row.metricLog2);
  const sub=isRank?`mean rank / ${r.cohort.length}`:isBlend?'mean normalized blend':lower?'geometric mean tokens':'geometric mean '+PAPER_LABEL[o.metric];
  const m=DATA.models.find(m=>m.id===row.model);
  body+=`<li class="paperRow" data-model="${esc(row.model)}" data-place="${row.place}" data-mean-rank="${row.averageRank}" data-value="${row.value}"><span class="paperPlace">${same?'=':''}${row.place}</span><div class="paperModel"><b>${esc(NAME(row.model))}</b><small>${row.qualifyingSamples}/${r.tasks.length*sampleSlots} usable · ${m.conditions[o.condition].valid_attempts}/${A} valid overall</small></div><div class="paperPlot" role="img" aria-label="${esc(NAME(row.model))}: ${isRank?'mean rank '+row.averageRank.toFixed(4):PAPER_LABEL[o.metric]+' '+row.value}"><span class="paperDot" style="left:${Math.max(0,Math.min(100,pos))}%"></span></div><div class="paperValue"><b>${main}</b><small${isRank?' class="paperMetricValue"':''}>${isRank?'':sub+' · '}${o.metric==='T0'&&isRank?'T₀ '+paperScientific(row.t0Log2):o.metric==='I0'&&isRank?'I₀ '+paperScientific(row.i0Log2):o.metric==='W_B'&&isRank?'Wᴮ '+paperScientific(row.wbLog2):isRank?'J₀ '+row.value.toFixed(3):'mean rank '+row.averageRank.toFixed(2)}</small></div></li>`;
 }
 paperNode('paperLeaderboard').innerHTML=`<div class="paperAxis"><div><span>${left}</span><span>${isRank?'Mean question rank':isBlend?'Normalized J₀':'log₂ '+PAPER_LABEL[o.metric]} · better →</span><span>${right}</span></div><span>${isRank?'Lower rank is better':lower?'Lower value is shorter':'Higher value first'}</span></div><ol class="paperList" aria-label="${esc(PAPER_LABEL[o.metric])} model rankings">${body}</ol>`;
 paperNode('paperReading').textContent=(isRank?`Models are ranked on each of these ${r.tasks.length} questions, then their ranks are averaged equally. Rank 1 is best; ties share ranks. `:isBlend?'Each question has equal weight in the mean normalized blend. ':'Points use a log₂ axis: one unit is a doubling. Values are geometric means with equal weight per question. ')+
  (isBlend?'The blend uses our maximum-based normalization and approximate ties (10⁻¹² tolerance). ':'Ordering uses exact ratios; the visible numbers are rounded. ')+
  'This is a comparison of measurable valid proofs, not a combined success score. Small differences do not establish a reliable winner.';
 const qrows=[];
 for(const task of r.tasks)for(const row of r.rows){const q=row.questions.find(x=>x.task===task);qrows.push([esc(task),esc(NAME(row.model)),num(q.rank),...(isBlend?[num(q.value)]:[]),paperScientific(q.t0Log2),paperScientific(q.i0Log2),paperScientific(q.wbLog2),`${q.samples}/${sampleSlots}`]);}
 paperNode('paperQuestionTable').innerHTML=table(['Question','Model','Question rank',...(isBlend?['J₀']:[]),'T₀','I₀','Proof tokens Wᴮ','Usable samples'],qrows);
}
function resetPaperControls(){
 paperMetric='T0';paperNode('paperCondition').value='mathematical_compression';paperNode('paperAggregation').value='rank';
 paperNode('paperCoverage').value=String(DATA.rankCoverageDefault);paperNode('paperMinSamples').value='1';paperNode('paperSample').value='all';
 paperNode('paperMaxProof').value='none';paperNode('paperQuestionMode').value='all';paperNode('paperBeta').value='50';
 Array.from(paperNode('paperMinSamples').options).forEach(x=>x.disabled=false);
 Array.from(paperNode('paperQuestions').options).forEach(x=>x.selected=false);
 if(paperRankingReady)renderPaperRanking();
}
function initializePaperRanking(){
 C.forEach(c=>paperNode('paperCondition').add(new Option(LABEL[c],c)));
 [...new Set([0,1,DATA.rankCoverageDefault,Math.ceil(N*.5),Math.ceil(N*.75),N])].forEach(n=>paperNode('paperCoverage').add(new Option(n===0?'All selected models':`At least ${n} of ${N} questions`,String(n))));
 for(let i=1;i<=S;i++){paperNode('paperSample').add(new Option(`Sample ${i} only`,String(i)));paperNode('paperMinSamples').add(new Option(String(i),String(i)));}
 DATA.tasks.forEach(t=>paperNode('paperQuestions').add(new Option(t,t)));
 resetPaperControls();paperRankingReady=true;
 document.querySelectorAll('[data-paper-metric]').forEach(b=>b.addEventListener('click',()=>{paperMetric=b.dataset.paperMetric;renderPaperRanking()}));
 for(const id of ['paperCondition','paperAggregation','paperCoverage','paperMinSamples','paperMaxProof','paperQuestionMode','paperQuestions'])paperNode(id).addEventListener('change',renderPaperRanking);
 paperNode('paperSample').addEventListener('change',()=>{
  const fixed=paperNode('paperSample').value!=='all';
  if(fixed)paperNode('paperMinSamples').value='1';
  Array.from(paperNode('paperMinSamples').options).forEach(x=>x.disabled=fixed&&Number(x.value)>1);
  renderPaperRanking();
 });
 paperNode('paperBeta').addEventListener('input',renderPaperRanking);
 paperNode('paperReset').addEventListener('click',resetPaperControls);paperNode('paperExport').addEventListener('click',paperDownloadJson);
}
