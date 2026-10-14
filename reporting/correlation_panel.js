let corrReady=false,corrResults=null,corrExportPayload=null;
const corrNode=id=>document.getElementById(id);
const corrFmt=value=>finite(value)?(value>=0?'+':'')+value.toFixed(3):'Unavailable';
function correlationOptions(){return {level:corrNode('corrLevel').value,xMode:corrNode('corrX').value};}
function correlationMetricName(metric,rank){return ({T0:'T₀',I0:'I₀',J0:'J₀',W_B:rank?'Concision':'Length Wᴮ'})[metric];}
function correlationAxisName(metric,rank){
 return rank?correlationMetricName(metric,true)+' question-rank points · higher is better':
  metric==='J0'?'Normalized J₀ blend · higher is larger':metric==='W_B'?'log₂ proof-body tokens · higher is longer':'log₂ '+PAPER_LABEL[metric]+' · higher is larger';
}
function corrInspection(point){
 if(!point)return 'Hover over or focus a point to inspect its values.';
 const xMode=corrNode('corrX').value;
 return `${NAME(point.model)}${point.task?' · '+point.task:''}: ${point.valid}/${point.scheduled} accepted (${(100*point.y).toFixed(1)}%); ${point.metricSamples} samples supply the compression value. ${correlationAxisName(paperMetric,xMode==='rank')}: ${num(point.x)}.`;
}
function renderCorrelationPlot(result,settings,metric){
 const points=result.points,rank=settings.xMode==='rank';
 if(!points.length){corrNode('corrChart').innerHTML='<p class="empty">No shared comparison under the current ranking filters. Adjust the model coverage or sample requirement above.</p>';return;}
 const width=Math.max(320,Math.min(760,corrNode('corrChart').clientWidth||760)),height=width<500?350:400,left=width<500?48:65,right=20,top=32,bottom=65,pw=width-left-right,ph=height-top-bottom;
 const xs=points.map(p=>p.x);let lo=rank?0:metric==='J0'?0:Math.floor(Math.min(...xs)),hi=rank?100:metric==='J0'?1:Math.ceil(Math.max(...xs));
 if(lo===hi){lo-=1;hi+=1;}
 const px=x=>left+(x-lo)/(hi-lo)*pw,py=y=>top+(1-y)*ph;
 let body=`<text x="${left}" y="15" font-size="12" fill="#566777">Accepted proofs / scheduled attempts</text>`;
 for(let i=0;i<=4;i++){
  const y=py(i/4);body+=`<path d="M${left} ${y}H${width-right}" stroke="#dfe7ed"/><text x="${left-10}" y="${y+4}" text-anchor="end" font-size="12" fill="#566777">${i*25}%</text>`;
 }
 for(let i=0;i<=4;i++){
  const v=lo+(hi-lo)*i/4,x=px(v);body+=`<path d="M${x} ${top}V${height-bottom}" stroke="#edf1f4"/><text x="${x}" y="${height-bottom+23}" text-anchor="middle" font-size="12" fill="#566777">${num(v)}</text>`;
 }
 body+=`<path d="M${left} ${top}V${height-bottom}H${width-right}" fill="none" stroke="#a8bccb"/>`;
 points.forEach((p,i)=>{
  const x=px(p.x),y=py(p.y),description=corrInspection(p),label=settings.level==='model'?NAME(p.model):'';
  const anchor=x>width-170?'end':'start',lx=x+(anchor==='end'?-11:11),ly=y<top+20?y+20:y-11;
  body+=`<g class="corrPoint" tabindex="0" role="button" aria-label="${esc(description)}" data-point="${i}"><circle cx="${x}" cy="${y}" r="${settings.level==='model'?7:4.5}"${settings.level==='question'?' opacity=".55"':''}/>${label?`<text x="${lx}" y="${ly}" text-anchor="${anchor}">${esc(label)}</text>`:''}</g>`;
 });
 body+=`<text x="${left+pw/2}" y="${height-12}" text-anchor="middle" font-size="13" fill="#243e52">${esc(correlationAxisName(metric,rank))}</text>`;
 corrNode('corrChart').innerHTML=`<svg viewBox="0 0 ${width} ${height}" role="group" aria-label="Compression and Lean acceptance on shared questions">${body}</svg>`;
}
function showCorrelationPoint(event){
 const mark=event.target.closest('[data-point]');
 if(mark&&corrResults)corrNode('corrHover').textContent=corrInspection(corrResults[paperMetric].points[Number(mark.dataset.point)]);
 // A persistent listener is installed at initialization. The plot's SVG is replaced on filter changes.
}
function renderCompressionValidity(){
 if(!corrReady||!paperOptions||!paperResult)return;
 const settings=correlationOptions(),rank=settings.xMode==='rank',metric=paperOptions.metric;
 corrNode('corrMetric').value=metric;
 const results={},support={};
 for(const key of ['T0','I0','J0','W_B']){
  const opts={...paperOptions,metric:key,modelIds:[...paperResult.cohort],minCoverage:0,questionIds:[...paperResult.tasks]};
  const ranking=PaperRankings.compute(DATA,opts);
  support[key]=ranking.cohort.join('|')===paperResult.cohort.join('|')&&ranking.tasks.join('|')===paperResult.tasks.join('|');
  results[key]=CompressionValidity.compute(DATA,ranking,opts,settings);
 }
 corrResults=results;const r=results[metric],pointCount=r.points.length;
 corrNode('corrScope').textContent=`${paperResult.cohort.length} models · ${paperResult.tasks.length} shared questions · ${LABEL[paperOptions.condition]} · ${pointCount} plotted ${settings.level==='model'?'models':'model–question groups'}. ${paperOptions.sample==='all'?S+' scheduled samples per question':'Sample '+paperOptions.sample+' only'} · minimum ${paperOptions.minSamples} usable${paperOptions.maxProofTokens===null?'':` · compression filter Wᴮ ≤ ${paperOptions.maxProofTokens}`}.`;
 renderCorrelationPlot(r,settings,metric);corrNode('corrHover').textContent=corrInspection(null);
 corrNode('corrSummary').innerHTML=`<table><thead><tr><th>Metric</th><th>Pearson r</th><th>Spearman ρ</th></tr></thead><tbody>`+
  ['T0','I0','J0','W_B'].map(key=>`<tr${key===metric?' class="activeMetric"':''}><td><button type="button" class="corrMetricLink" data-corr-metric="${key}">${esc(correlationMetricName(key,rank))}${key==='J0'?` <small>β = ${num(paperOptions.beta)}</small>`:''}</button></td><td>${support[key]?corrFmt(results[key].pearson.value):'Different support'}</td><td>${support[key]?corrFmt(results[key].spearman.value):'Different support'}</td></tr>`).join('')+'</tbody></table>';
 const centered=r.withinQuestionPearson;
 corrNode('corrCentered').innerHTML=settings.level==='question'?`<div class="corrCentered">${esc(PAPER_LABEL[metric])}: after removing question means<strong>${corrFmt(centered.value)}</strong><small>Within-question Pearson r. Removes differences between question means only.</small></div>`:'';
 const usable=finite(r.pearson.value)||finite(r.spearman.value);
 corrNode('corrReading').textContent=usable?
  `${correlationMetricName(metric,rank)}: Pearson r = ${corrFmt(r.pearson.value)}, Spearman ρ = ${corrFmt(r.spearman.value)}. `+
   (settings.level==='model'?`Only ${pointCount} models contribute; treat this as a descriptive comparison, not a general conclusion about models. `:'These groups repeat the same models and questions; the point count is not an independent sample size. Overlapping points can be inspected by keyboard or in the values table. ')+
   (rank?'Positive means better compression rank accompanies higher acceptance. ':metric==='W_B'?'Positive means longer proofs accompany higher acceptance. ':'Positive means a larger metric accompanies higher acceptance. '):
  (!pointCount?'No correlation is calculated without a shared comparison.':pointCount<3?'At least three plotted groups are required for a correlation.':'Correlation is unavailable because compression or acceptance has no variation in this selection. Selecting one sample or requiring all samples to succeed can force acceptance to 100%.');
 corrNode('corrTable').innerHTML=table(['Model','Question','Compression x','Accepted','Scheduled','Acceptance','Metric samples'],r.points.map(p=>[esc(NAME(p.model)),p.task?esc(p.task):'Shared-question aggregate',num(p.x),p.valid,p.scheduled,(100*p.y).toFixed(1)+'%',p.metricSamples]));
 corrExportPayload={schemaVersion:1,kind:'descriptive_conditional_compression_validity_associations',sourceBuilt:DATA.built,sourceAnalyses:DATA.sources,verification:DATA.verification,rankingControls:paperOptions,correlationControls:settings,
  definition:'Compression is measured only on valid outputs. Validity uses all scheduled selected sample slots on the same shared questions, including failures. No direct attempt-level correctness correlation is estimable.',
  allMetricResults:results,sameSupportForOverview:support};
 corrNode('corrExport').disabled=!pointCount;
}
function resetCompressionValidityControls(){if(corrReady){corrNode('corrLevel').value='model';corrNode('corrX').value='rank';}}
function initializeCompressionValidity(){
 corrReady=true;
 let resizeTimer;window.addEventListener('resize',()=>{clearTimeout(resizeTimer);resizeTimer=setTimeout(renderCompressionValidity,100);});
 corrNode('corrMetric').addEventListener('change',()=>{paperMetric=corrNode('corrMetric').value;renderPaperRanking()});
 for(const id of ['corrLevel','corrX'])corrNode(id).addEventListener('change',renderCompressionValidity);
 corrNode('corrSummary').addEventListener('click',e=>{const b=e.target.closest('[data-corr-metric]');if(b){paperMetric=b.dataset.corrMetric;renderPaperRanking();corrNode('corrSummary').querySelector(`[data-corr-metric="${paperMetric}"]`)?.focus();}});
 for(const type of ['pointerover','focusin','click'])corrNode('corrChart').addEventListener(type,showCorrelationPoint);
 corrNode('corrExport').addEventListener('click',()=>{if(!corrExportPayload||!corrResults[paperMetric].points.length)return;const url=URL.createObjectURL(new Blob([JSON.stringify(corrExportPayload,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download='compression-validity-correlations.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
}
