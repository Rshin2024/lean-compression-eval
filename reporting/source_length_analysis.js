/* Pure post hoc source-length/validity summaries. No model, compiler or file I/O. */
(function(root){
  'use strict';
  const cache=new WeakMap();
  const UNITS=['sourceNonWhitespaceCharacters','sourceCharacters','sourceUtf8Bytes'];
  const TERMINAL=new Set(['success','output_format_failure','parse_failure','elaboration_failure','target_mismatch',
    'forbidden_dependency','disallowed_axiom','validation_error','kernel_replay_failure','verification_timeout',
    'verification_resource_limit','refusal','truncated_output','malformed_response']);
  const FORMAT=new Set(['parse_failure','output_format_failure']);
  function require(ok,message){if(!ok)throw new Error(message);}
  function average(xs){return xs.reduce((sum,x)=>sum+x,0)/xs.length;}
  function correlation(xs,ys,method){
    const n=xs.length;
    if(n<3)return {value:null,n,reason:'fewer_than_three_points',method};
    if(xs.some(x=>!Number.isFinite(x))||ys.some(y=>!Number.isFinite(y)))return {value:null,n,reason:'non_finite_coordinate',method};
    const ax=xs.map(x=>x-xs[0]),ay=ys.map(y=>y-ys[0]),mx=average(ax),my=average(ay);
    const dx=ax.map(x=>x-mx),dy=ay.map(y=>y-my),sx=Math.max(...dx.map(Math.abs)),sy=Math.max(...dy.map(Math.abs));
    if(sx===0||sy===0)return {value:null,n,reason:sx===0&&sy===0?'constant_both':sx===0?'constant_x':'constant_y',method};
    let xx=0,yy=0,xy=0;
    for(let i=0;i<n;i++){const x=dx[i]/sx,y=dy[i]/sy;xx+=x*x;yy+=y*y;xy+=x*y;}
    return {value:Math.max(-1,Math.min(1,xy/Math.sqrt(xx*yy))),n,reason:null,method};
  }
  function midranks(xs){
    const order=xs.map((_,i)=>i).sort((a,b)=>xs[a]-xs[b]||a-b),ranks=Array(xs.length);
    for(let start=0;start<order.length;){
      let end=start+1;while(end<order.length&&xs[order[end]]===xs[order[start]])end++;
      for(let i=start;i<end;i++)ranks[order[i]]=(start+1+end)/2;
      start=end;
    }
    return ranks;
  }
  function associations(points){
    return {pearson:correlation(points.map(p=>p.x),points.map(p=>p.valid),'point_biserial_Pearson_on_selected_length_scale'),
      spearman:correlation(midranks(points.map(p=>p.length)),midranks(points.map(p=>p.valid)),'Spearman_exact_integer_length_midranks')};
  }
  function quantile(sorted,p){
    if(!sorted.length)return null;
    const index=(sorted.length-1)*p,lo=Math.floor(index),hi=Math.ceil(index);
    return sorted[lo]+(sorted[hi]-sorted[lo])*(index-lo);
  }
  function summary(points){
    const values=points.map(p=>p.length).sort((a,b)=>a-b);
    return {n:values.length,median:quantile(values,.5),q25:quantile(values,.25),q75:quantile(values,.75),
      min:values.length?values[0]:null,max:values.length?values[values.length-1]:null};
  }
  function statusSummaries(points){return {accepted:summary(points.filter(p=>p.valid===1)),
    rejected:summary(points.filter(p=>p.valid===0)),truncated:summary(points.filter(p=>p.truncated))};}
  function indexData(data){
    require(data&&Array.isArray(data.models)&&Array.isArray(data.tasks)&&Array.isArray(data.conditions)&&Array.isArray(data.attempts),
      'DATA lacks model/question/condition/attempt arrays');
    if(cache.has(data))return cache.get(data);
    const modelIds=data.models.map(m=>m.id),tasks=[...data.tasks],conditions=[...data.conditions];
    require(new Set(modelIds).size===modelIds.length&&new Set(tasks).size===tasks.length&&new Set(conditions).size===conditions.length,
      'Duplicate DATA identities');
    const models=new Set(modelIds),taskSet=new Set(tasks),conditionSet=new Set(conditions),identities=new Set();
    const maxima=Object.fromEntries(UNITS.map(unit=>[unit,0])),rows=[];
    for(const row of data.attempts){
      require(models.has(row.model)&&taskSet.has(row.task)&&conditionSet.has(row.condition)
        &&Number.isInteger(row.sample)&&row.sample>=1,'Attempt outside DATA identities');
      const key=JSON.stringify([row.model,row.task,row.condition,row.sample]);
      require(!identities.has(key),'Duplicate scheduled model/question/condition/sample');identities.add(key);
      const sl=row.sourceLength||{},lengths={};
      for(const unit of UNITS){
        const value=sl[unit];lengths[unit]=sl.available===true&&Number.isSafeInteger(value)&&value>0?value:null;
        if(lengths[unit]!==null)maxima[unit]=Math.max(maxima[unit],lengths[unit]);
      }
      const truncated=sl.truncated===true||row.status==='truncated_output';
      require(!(truncated&&row.status==='success'),'A truncated outcome cannot be a verified success');
      rows.push({model:row.model,task:row.task,condition:row.condition,sample:row.sample,
        attemptId:typeof row.attemptId==='string'?row.attemptId:null,status:row.status,truncated,
        sourceKind:typeof sl.sourceKind==='string'?sl.sourceKind:'unknown',lengths});
    }
    const result={modelIds,tasks,conditions,models,taskSet,conditionSet,rows,maxima};cache.set(data,result);return result;
  }
  function controls(index,supplied){
    const o={modelIds:index.modelIds,condition:'all',taskIds:null,unit:'sourceNonWhitespaceCharacters',
      truncation:'exclude',failures:'all',xScale:'log2',...supplied};
    require(Array.isArray(o.modelIds)&&new Set(o.modelIds).size===o.modelIds.length&&o.modelIds.every(id=>index.models.has(id)),
      'Unknown or duplicate model selection');
    require(o.condition==='all'||index.conditionSet.has(o.condition),'Unknown condition');
    require(o.taskIds===null||Array.isArray(o.taskIds)&&new Set(o.taskIds).size===o.taskIds.length&&o.taskIds.every(id=>index.taskSet.has(id)),
      'Unknown or duplicate question selection');
    require(UNITS.includes(o.unit),'Unknown source-length unit');
    require(['exclude','include','only'].includes(o.truncation),'Unknown truncation policy');
    require(['all','exclude_format'].includes(o.failures),'Unknown format-failure policy');
    require(['log2','linear'].includes(o.xScale),'Unknown length scale');
    return {...o,modelIds:[...o.modelIds],taskIds:o.taskIds===null?null:[...o.taskIds]};
  }
  function withinCells(points){
    const groups=new Map();
    for(const p of points){const key=JSON.stringify([p.model,p.task,p.condition]);if(!groups.has(key))groups.set(key,[]);groups.get(key).push(p);}
    const xs=[],ys=[];let usedCells=0,singletonCells=0,mixedOutcomeCells=0,mixedOutcomeAttempts=0;
    for(const rows of groups.values()){
      if(rows.length<2){singletonCells++;continue;}
      usedCells++;const mx=average(rows.map(p=>p.x)),my=average(rows.map(p=>p.valid));
      if(rows.some(p=>p.valid===1)&&rows.some(p=>p.valid===0)){mixedOutcomeCells++;mixedOutcomeAttempts+=rows.length;}
      for(const p of rows){xs.push(p.x-mx);ys.push(p.valid-my);}
    }
    const result=correlation(xs,ys,'within_model_question_prompt_demeaned_Pearson');
    if(!usedCells)result.reason='no_cells_with_repeated_observations';
    return {...result,cellCount:groups.size,usedCells,singletonCellsExcluded:singletonCells,
      singletonAttemptsExcluded:singletonCells,mixedOutcomeCells,mixedOutcomeAttempts,constantOutcomeCells:usedCells-mixedOutcomeCells};
  }
  function binEdges(maximum){
    const edges=[1,64];while(edges[edges.length-1]<=maximum)edges.push(edges[edges.length-1]*2);
    return edges;
  }
  function compute(data,supplied={}){
    const index=indexData(data),options=controls(index,supplied),models=new Set(options.modelIds);
    const tasks=options.taskIds===null?index.taskSet:new Set(options.taskIds);
    const selected=index.rows.filter(r=>models.has(r.model)&&tasks.has(r.task)
      &&(options.condition==='all'||r.condition===options.condition));
    const coverage={selectedAttempts:selected.length,selectedAccepted:0,selectedRejected:0,nonempty:0,missingText:0,truncatedAttempts:0,
      formatFailures:0,nonemptyTruncated:0,excludedTruncated:0,excludedNonTruncated:0,excludedFormat:0,
      includedAttempts:0,accepted:0,rejected:0,includedTruncated:0};
    const points=[],selectedTruncated=[],sourceKinds={};
    for(const row of selected){
      require(TERMINAL.has(row.status),'Unresolved or unknown outcome cannot be counted as rejected');
      if(row.status==='success')coverage.selectedAccepted++;else coverage.selectedRejected++;
      if(row.truncated)coverage.truncatedAttempts++;
      if(FORMAT.has(row.status))coverage.formatFailures++;
      const length=row.lengths[options.unit];
      if(length===null){coverage.missingText++;continue;}
      coverage.nonempty++;
      const point={index:points.length,x:options.xScale==='log2'?Math.log2(length):length,length,
        valid:row.status==='success'?1:0,status:row.status,model:row.model,task:row.task,condition:row.condition,
        sample:row.sample,attemptId:row.attemptId,truncated:row.truncated,sourceKind:row.sourceKind};
      if(row.truncated){coverage.nonemptyTruncated++;selectedTruncated.push(point);}
      if(options.truncation==='exclude'&&row.truncated){coverage.excludedTruncated++;continue;}
      if(options.truncation==='only'&&!row.truncated){coverage.excludedNonTruncated++;continue;}
      if(options.failures==='exclude_format'&&FORMAT.has(row.status)){coverage.excludedFormat++;continue;}
      points.push(point);sourceKinds[row.sourceKind]=(sourceKinds[row.sourceKind]||0)+1;
    }
    coverage.includedAttempts=points.length;coverage.accepted=points.filter(p=>p.valid===1).length;
    coverage.rejected=points.length-coverage.accepted;coverage.includedTruncated=points.filter(p=>p.truncated).length;
    coverage.excludedMissingText=coverage.missingText;coverage.excludedByTruncation=coverage.excludedTruncated+coverage.excludedNonTruncated;
    require(coverage.selectedAttempts===coverage.missingText+coverage.excludedTruncated+coverage.excludedNonTruncated+coverage.excludedFormat+coverage.includedAttempts,
      'Length population counters do not reconcile');
    const edges=binEdges(index.maxima[options.unit]);
    const bins=edges.slice(0,-1).map((lower,i)=>({lower,upper:edges[i+1],n:0,accepted:0,rejected:0,rate:null,truncated:0}));
    for(const point of points){
      const bin=bins.find(bin=>point.length>=bin.lower&&point.length<bin.upper);require(bin,'Positive source length is outside fixed bins');
      bin.n++;bin.accepted+=point.valid;bin.rejected+=1-point.valid;bin.truncated+=Number(point.truncated);
    }
    for(const bin of bins)if(bin.n)bin.rate=bin.accepted/bin.n;
    const perModel=options.modelIds.map(model=>{
      const rows=points.filter(p=>p.model===model),all=selected.filter(p=>p.model===model);
      const accepted=rows.filter(p=>p.valid===1).length;
      return {model,n:rows.length,accepted,rejected:rows.length-accepted,...associations(rows),summaries:statusSummaries(rows),
        selectedAttempts:all.length,truncatedAttempts:all.filter(r=>r.truncated).length,
        missingText:all.filter(r=>r.lengths[options.unit]===null).length};
    });
    const sourceMethods=JSON.parse(JSON.stringify(data.sourceLengthMeasurement??data.sourceMethods??data.sourceLengthMethods??null));
    return {points,coverage,...associations(points),withinCellPearson:withinCells(points),summaries:statusSummaries(points),bins,perModel,
      selectedTruncatedSummary:summary(selectedTruncated),
      metadata:{schemaVersion:1,kind:'exploratory_source_length_validity_association',options,sourceMethods,
        xDefinition:options.xScale==='log2'?'log2(positive measured source length)':'positive measured source length',
        yDefinition:'1 for verified success; 0 for a terminal model-attributable failure',
        population:'All selected recorded attempts, independently of successful-proof ranking cohorts or shared-success intersections.',
        missingPolicy:'Unavailable, empty, nonpositive or noninteger source measurements are excluded, never assigned zero.',
        exclusionOrder:['missingText','truncation policy','format policy'],
        counterReconciliation:'selectedAttempts = missingText + excludedTruncated + excludedNonTruncated + excludedFormat + includedAttempts',
        independentCounters:['selectedAccepted','selectedRejected','truncatedAttempts','formatFailures','nonemptyTruncated'],
        summaryPopulation:'Included statistical points; truncated summary is a subset of rejected, not an additional disjoint group.',
        selectedTruncatedSummaryPopulation:'Selected nonempty truncated responses before truncation/format exclusions.',
        quartiles:'Linear interpolation at (n-1)*p; raw length units.',
        bins:'Raw-length lower-inclusive/upper-exclusive doubling boundaries, with initial [1,64); extent fixed by whole-DATA maximum for this unit.',
        sourceKindCounts:sourceKinds,
        notes:['Character/byte length is not the Lean W_B token metric, API token usage, or a measure of complete intended proof length.',
          'Default exclusion of truncated outputs makes the association conditional on completion and available text; counts and model composition can change.',
          'Truncated responses are incomplete outputs under a token budget, not observations censored at a fixed character threshold.',
          'Model, question and prompt differences can confound pooled length/validity associations.',
          'Within-cell Pearson centers the observed eligible subset within model × question × prompt; singleton cells are discarded. Constant-outcome cells contribute no covariance.',
          'Attempts repeat models and questions; n is not a count of independent observations. No p-values, confidence intervals or causal conclusions are produced.',
          'Spearman uses exact integer-length ties and binary-outcome midranks; changing log2 versus linear scale does not change Spearman.']}};
  }
  const api=Object.freeze({compute,version:'source_length_analysis_v1'});
  root.SourceLengthAnalysis=api;if(typeof module!=='undefined'&&module.exports)module.exports=api;
})(typeof globalThis!=='undefined'?globalThis:this);
