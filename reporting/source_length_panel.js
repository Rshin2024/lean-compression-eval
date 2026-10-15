let lengthReady=false,lengthResult=null,lengthSettings=null,lengthVisibleBins=[];
const lengthNode=id=>document.getElementById(id);
const LENGTH_UNITS={sourceNonWhitespaceCharacters:'non-whitespace characters',sourceCharacters:'characters',sourceUtf8Bytes:'UTF-8 bytes'};
function sourceLengthOptions(){return {modelIds:selected().map(m=>m.id),condition:lengthNode('lengthCondition').value,
 unit:lengthNode('lengthUnit').value,truncation:lengthNode('lengthTruncation').value,failures:lengthNode('lengthFailures').value,
 xScale:lengthNode('lengthScale').value,taskIds:lengthNode('lengthTask').value==='all'?null:[lengthNode('lengthTask').value]};}
function lengthBinLabel(bin){return `${num(bin.lower)}–${num(bin.upper-1)}`;}
function lengthBinDetails(bin){return `${lengthBinLabel(bin)} ${LENGTH_UNITS[lengthSettings.unit]}: ${bin.accepted}/${bin.n} accepted${bin.n?' ('+(bin.rate*100).toFixed(1)+'%)':''}; ${bin.rejected} not accepted, ${bin.truncated} truncated. The upper boundary is exclusive.`;}
function sourceLengthChart(bins){
 const occupied=bins.map((b,i)=>b.n?i:-1).filter(i=>i>=0);
 if(!occupied.length){lengthVisibleBins=[];lengthNode('lengthChart').innerHTML='<p class="empty">No nonempty submitted text under these filters.</p>';return;}
 lengthVisibleBins=bins.slice(occupied[0],occupied[occupied.length-1]+1);
 const width=Math.max(560,lengthVisibleBins.length*76+70),height=330,left=52,right=16,top=25,bottom=80,pw=width-left-right,ph=height-top-bottom,step=pw/lengthVisibleBins.length;
 let body='';for(let i=0;i<=4;i++){const y=top+ph*(1-i/4);body+=`<path d="M${left} ${y}H${width-right}" stroke="#dfe7ed"/><text x="${left-8}" y="${y+4}" text-anchor="end" font-size="12" fill="#566777">${i*25}%</text>`;}
 lengthVisibleBins.forEach((b,i)=>{const x=left+i*step+step*.14,bw=step*.72,bar=b.n?b.rate*ph:0,y=top+ph-bar;
  body+=`<g class="lengthBin" data-length-bin="${i}" role="button" tabindex="0" aria-label="${esc(lengthBinDetails(b))}"><rect x="${x}" y="${y}" width="${bw}" height="${Math.max(bar,1)}"${!b.n?' style="fill:#c8d5de"':''}/><text x="${x+bw/2}" y="${Math.max(top-7,y-7)}" text-anchor="middle" font-size="12" fill="#243e52">${b.n?(b.rate*100).toFixed(0)+'%':'—'}</text><text x="${x+bw/2}" y="${top+ph+22}" text-anchor="middle" font-size="11" fill="#566777">${num(b.lower)}–</text><text x="${x+bw/2}" y="${top+ph+36}" text-anchor="middle" font-size="11" fill="#566777">${num(b.upper-1)}</text><text x="${x+bw/2}" y="${top+ph+54}" text-anchor="middle" font-size="11" fill="#566777">n=${b.n.toLocaleString()}</text></g>`;});
 body+=`<text x="${left+pw/2}" y="${height-5}" text-anchor="middle" font-size="12" fill="#243e52">${esc(LENGTH_UNITS[lengthSettings.unit])} · fixed doubling buckets</text>`;
 lengthNode('lengthChart').innerHTML=`<svg viewBox="0 0 ${width} ${height}" role="group" aria-label="Lean acceptance by source length">${body}</svg>`;
}
function lengthSummaryRow(name,s){return [name,s.n.toLocaleString(),num(s.median),s.n?`${num(s.q25)}–${num(s.q75)}`:'—'];}
function renderSourceLengths(){
 if(!lengthReady)return;
 if(!DATA.sourceLengthMeasurement?.available){lengthNode('lengthCoverage').textContent='Source-length measurements are not available for this report snapshot.';lengthNode('lengthExport').disabled=true;return;}
 const o=sourceLengthOptions(),r=SourceLengthAnalysis.compute(DATA,o),c=r.coverage;lengthSettings=o;lengthResult=r;
 lengthNode('lengthCoverage').innerHTML=[['Selected attempts',c.selectedAttempts],['Accepted in analysis',c.accepted],['Not accepted in analysis',c.rejected],['No source text',c.excludedMissingText],['Excluded by truncation filter',c.excludedByTruncation],['Excluded parse / format',c.excludedFormat]].map(([name,n])=>`<div>${esc(name)}<b>${n.toLocaleString()}</b></div>`).join('')+
  `<div class="lengthCoverageNote">${o.modelIds.length} models · ${o.condition==='all'?'all three prompts':LABEL[o.condition]} · ${o.taskIds?'question '+esc(o.taskIds[0]):'all '+N+' questions'} · ${c.includedAttempts.toLocaleString()} nonempty texts in this analysis. ${c.truncatedAttempts.toLocaleString()} selected responses were truncated, including any with no returned text.</div>`;
 sourceLengthChart(r.bins);lengthNode('lengthHover').textContent='Hover over or focus a bucket to see accepted and unsuccessful counts. Empty outer buckets are omitted.';
 lengthNode('lengthDistributions').innerHTML=table(['Outcome','n','Median','Middle 50%'],[lengthSummaryRow('Accepted',r.summaries.accepted),lengthSummaryRow('Not accepted',r.summaries.rejected)]);
 const adjusted=r.withinCellPearson;
 lengthNode('lengthStats').innerHTML=`<div class="lengthStats"><div>Pearson r<strong>${corrFmt(r.pearson.value)}</strong></div><div>Spearman ρ<strong>${corrFmt(r.spearman.value)}</strong></div></div><div class="lengthAdjusted">After removing model × question × prompt means<br><strong>${corrFmt(adjusted.value)}</strong><small>Pearson r · ${adjusted.n.toLocaleString()} attempts in ${adjusted.usedCells.toLocaleString()} groups; ${adjusted.mixedOutcomeCells.toLocaleString()} groups have mixed outcomes</small></div>`;
 lengthNode('lengthReading').textContent=`Length unit: ${LENGTH_UNITS[o.unit]}. Pearson uses ${o.xScale==='log2'?'log₂ length':'raw length'}; Spearman uses integer-length ranks. Positive means longer submissions were more often accepted; negative means shorter submissions were more often accepted. These are descriptive associations, not an effect of changing proof length.`;
 lengthNode('lengthCaution').textContent=o.truncation==='exclude'?'Truncated responses are excluded from these associations. Nonempty unsuccessful submissions remain included, including prose or malformed source that was actually submitted. Missing text is not a zero-length proof.':o.truncation==='only'?'Only preserved text from truncated responses is included. These outputs were not accepted, so correctness is constant and its correlation is unavailable. Empty truncated responses remain missing.':'Truncated responses contribute only the text preserved before the response stopped. This can change the association through the token limit and incomplete outputs; it does not recover the intended full proof length.';
 lengthNode('lengthModelTable').innerHTML=table(['Model','Included attempts','Accepted','Acceptance','Accepted median','Not-accepted median','Pearson r','Spearman ρ'],r.perModel.map(m=>[esc(NAME(m.model)),m.n,`${m.accepted}/${m.n}`,m.n?(100*m.accepted/m.n).toFixed(1)+'%':'—',num(m.summaries.accepted.median),num(m.summaries.rejected.median),corrFmt(m.pearson.value),corrFmt(m.spearman.value)]));
 lengthNode('lengthBinTable').innerHTML=table(['Length interval','Attempts','Accepted','Not accepted','Acceptance','Truncated'],r.bins.map(b=>[lengthBinLabel(b),b.n,b.accepted,b.rejected,b.n?(100*b.rate).toFixed(1)+'%':'Unavailable',b.truncated]));
 lengthNode('lengthExport').disabled=!r.points.length;
 const policies=DATA.sourceLengthMeasurement.policies;
 lengthNode('lengthProvenance').innerHTML=`<small>${policies.length} source policies; ${DATA.sourceLengthMeasurement.sources.length} report/manifest pairs verified. Source hashes and the complete measurement policy accompany the export. No model responses are embedded in the page.</small>`;
}
function resetSourceLengthControls(){
 if(!lengthReady)return;
 lengthNode('lengthCondition').value='all';lengthNode('lengthUnit').value='sourceNonWhitespaceCharacters';lengthNode('lengthTruncation').value='exclude';
 lengthNode('lengthTask').value='all';lengthNode('lengthFailures').value='all';lengthNode('lengthScale').value='log2';renderSourceLengths();
}
function initializeSourceLengths(){
 C.forEach(c=>lengthNode('lengthCondition').add(new Option(LABEL[c],c)));DATA.tasks.forEach(t=>lengthNode('lengthTask').add(new Option(t,t)));lengthReady=true;
 for(const id of ['lengthCondition','lengthUnit','lengthTruncation','lengthTask','lengthFailures','lengthScale'])lengthNode(id).addEventListener('change',renderSourceLengths);
 lengthNode('lengthReset').addEventListener('click',resetSourceLengthControls);
 for(const type of ['pointerover','focusin','click'])lengthNode('lengthChart').addEventListener(type,e=>{const b=e.target.closest('[data-length-bin]');if(b)lengthNode('lengthHover').textContent=lengthBinDetails(lengthVisibleBins[Number(b.dataset.lengthBin)]);});
 lengthNode('lengthExport').addEventListener('click',()=>{if(!lengthResult?.points.length)return;
  const includedIds=new Set(lengthResult.points.map(p=>p.attemptId));
  const payload={schemaVersion:1,kind:'source_length_validity_analysis',sourceBuilt:DATA.built,measurement:DATA.sourceLengthMeasurement,controls:lengthSettings,result:lengthResult,
   measuredAttempts:DATA.attempts.filter(a=>includedIds.has(a.attemptId)).map(a=>({model:a.model,task:a.task,condition:a.condition,sample:a.sample,attemptId:a.attemptId,status:a.status,sourceLength:a.sourceLength}))};
  const url=URL.createObjectURL(new Blob([JSON.stringify(payload,null,2)],{type:'application/json'})),a=document.createElement('a');a.href=url;a.download='source-length-validity.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
}
