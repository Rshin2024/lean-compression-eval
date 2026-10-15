/* Descriptive compression/validity associations; frozen observations only. */
(function(root) {
  'use strict';
  const TOLERANCE=1e-12, cache=new WeakMap();
  const TERMINAL=new Set(['success','output_format_failure','parse_failure','elaboration_failure','target_mismatch',
    'forbidden_dependency','disallowed_axiom','validation_error','kernel_replay_failure','verification_timeout',
    'verification_resource_limit','refusal','truncated_output','malformed_response']);
  function require(ok,message){if(!ok)throw new Error(message);}
  function gcd(a,b){while(b){const r=a%b;a=b;b=r;}return a;}
  function exact(value){
    require(value&&typeof value==='object','Missing exact magnitude evidence');
    const fields=['numerator_product_decimal','denominator_product_decimal','root_degree'];
    require(fields.every(k=>typeof value[k]==='string'&&/^\d+$/.test(value[k])),'Malformed exact magnitude evidence');
    const [n,d,degree]=fields.map(k=>BigInt(value[k]));
    require(n>0n&&d>0n&&degree>0n,'Exact magnitude evidence must be positive');
    return {n,d,degree};
  }
  function compareExact(a,b){
    const g=gcd(a.degree,b.degree),ae=b.degree/g,be=a.degree/g;
    const left=a.n**ae*b.d**be,right=b.n**be*a.d**ae;
    return left<right?-1:left>right?1:0; // Raw magnitude increasing, including W_B.
  }
  function sourceIndex(data){
    require(data&&Array.isArray(data.attempts),'DATA.attempts is required');
    if(cache.has(data))return cache.get(data);
    const samples=data.samplesPerQuestion;
    require(Number.isSafeInteger(samples)&&samples>0,'DATA.samplesPerQuestion must be a positive integer');
    const map=new Map();
    for(const row of data.attempts){
      require(typeof row.model==='string'&&typeof row.task==='string'&&typeof row.condition==='string'
        &&Number.isInteger(row.sample)&&row.sample>=1&&row.sample<=samples,'Malformed scheduled attempt identity');
      const key=JSON.stringify([row.model,row.condition,row.task]);
      if(!map.has(key))map.set(key,new Map());
      require(!map.get(key).has(row.sample),'Duplicate scheduled model/question/condition/sample');
      map.get(key).set(row.sample,row);
    }
    const index={samples,map};cache.set(data,index);return index;
  }
  function completedClassification(row){
    const s=row.sourceLength;
    if(!s||typeof s!=='object'||typeof s.available!=='boolean'||typeof s.truncated!=='boolean'
      ||typeof s.responseStatus!=='string'||typeof s.sourceKind!=='string')return null;
    if(s.responseStatus==='completed'&&s.sourceKind==='submitted_text'&&s.truncated===false)
      return {completed:s.available,empty:!s.available,truncated:false};
    if(s.responseStatus==='truncated_output'&&s.sourceKind==='partial_saved_text'&&s.truncated===true)
      return {completed:false,empty:!s.available,truncated:true};
    return null;
  }
  function summarizeOutcomes(rows){
    require(Array.isArray(rows),'Outcome rows must be an array');
    let accepted=0,knownCompleted=0,truncated=0,knownEmpty=0,unclassified=0;
    const completedAttemptIds=[];
    for(const row of rows){
      require(TERMINAL.has(row.status),'Unresolved or unknown outcomes cannot be treated as proof failures');
      const classification=completedClassification(row);
      if(row.status==='success'){
        accepted++;
        require(classification===null||classification.completed,'Accepted proof is not a completed nonempty submitted text');
      }
      if(row.status==='truncated_output'||row.sourceLength?.truncated===true)truncated++;
      if(classification===null){unclassified++;continue;}
      require(!(row.status==='truncated_output'&&classification.completed),'Truncated outcome mislabeled as a completed submission');
      if(classification.completed){knownCompleted++;completedAttemptIds.push(typeof row.attemptId==='string'?row.attemptId:null);}
      if(classification.empty)knownEmpty++;
    }
    const scheduled=rows.length,classificationAvailable=unclassified===0,completed=classificationAvailable?knownCompleted:null;
    return {scheduled,completed,accepted,truncated,empty:classificationAvailable?knownEmpty:null,unclassified,
      knownCompleted,knownEmpty,classificationAvailable,
      completedRejected:completed===null?null:completed-accepted,
      incomplete:completed===null?null:scheduled-completed,
      completionRate:scheduled&&completed!==null?completed/scheduled:null,
      completedAcceptanceRate:completed?accepted/completed:null,
      scheduledSolveRate:scheduled?accepted/scheduled:null,
      completedAttemptIds,
      reason:!scheduled?'no_scheduled_attempts':!classificationAvailable?'completion_classification_unavailable':
        completed===0?'no_completed_submissions':null,
      classificationPolicy:'responseStatus=completed, available=true, sourceKind=submitted_text, truncated=false',
      countNote:'empty and truncated are overlapping diagnostics; empty truncated text is counted in both.'};
  }
  function average(values){return values.reduce((sum,x)=>sum+x,0)/values.length;}
  function correlation(xs,ys,method){
    const n=xs.length;
    if(n<3)return {value:null,n,reason:'fewer_than_three_points',method};
    if(xs.some(x=>!Number.isFinite(x))||ys.some(y=>!Number.isFinite(y)))return {value:null,n,reason:'non_finite_coordinate',method};
    // Translate by the first coordinate before averaging to reduce cancellation.
    const ax=xs.map(x=>x-xs[0]),ay=ys.map(y=>y-ys[0]);
    const mx=average(ax),my=average(ay),dx=ax.map(x=>x-mx),dy=ay.map(y=>y-my);
    const sx=Math.max(...dx.map(Math.abs)),sy=Math.max(...dy.map(Math.abs));
    if(sx===0||sy===0)return {value:null,n,reason:sx===0&&sy===0?'constant_both':sx===0?'constant_x':'constant_y',method};
    let xx=0,yy=0,xy=0;
    for(let i=0;i<n;i++){const x=dx[i]/sx,y=dy[i]/sy;xx+=x*x;yy+=y*y;xy+=x*y;}
    const value=xy/Math.sqrt(xx*yy);
    return {value:Math.max(-1,Math.min(1,value)),n,reason:null,method};
  }
  function midranks(points,compare,{approximateDescending=false}={}){
    const order=points.map((_,i)=>i).sort((a,b)=>compare(points[a],points[b])||a-b),ranks=Array(points.length);
    for(let start=0;start<order.length;){
      let end=start+1;
      // J0 inherits the paper module's descending, leader-anchored tolerance
      // groups. Convert their midranks to ascending only after grouping.
      while(end<order.length&&(approximateDescending?
        Math.abs(points[order[start]].x-points[order[end]].x)<=TOLERANCE:
        compare(points[order[start]],points[order[end]])===0))end++;
      const rank=(start+1+end)/2;
      for(let i=start;i<end;i++)ranks[order[i]]=approximateDescending?points.length+1-rank:rank;
      start=end;
    }
    return ranks;
  }
  function countCell(index,model,task,options){
    const map=index.map.get(JSON.stringify([model,options.condition,task]));
    const samples=options.sample==='all'?Array.from({length:index.samples},(_,i)=>i+1):[options.sample];
    require(map,'Missing scheduled model/question cell');
    const rows=samples.map(sample=>{
      require(map.has(sample),'Incomplete selected sample schedule');
      const row=map.get(sample);
      require(TERMINAL.has(row.status),'Unresolved or unknown outcomes cannot be treated as proof failures');
      return row;
    });
    const outcomes=summarizeOutcomes(rows);
    return {valid:outcomes.accepted,scheduled:rows.length,outcomes,
      attemptIds:rows.map(r=>typeof r.attemptId==='string'?r.attemptId:null),rows};
  }
  function compute(data,paperResult,paperOptions={},view={}){
    const index=sourceIndex(data),normalized=paperResult?.metadata?.options;
    if(normalized)for(const key of ['condition','metric','sample','maxProofTokens','minSamples','minCoverage','beta','questionIds','modelIds'])
      if(Object.prototype.hasOwnProperty.call(paperOptions,key))require(JSON.stringify(normalized[key])===JSON.stringify(paperOptions[key]),'Paper options/result mismatch: '+key);
    const options={condition:'mathematical_compression',metric:'T0',sample:'all',minSamples:1,...normalized,...paperOptions};
    const level=view.level??'model',xMode=view.xMode??'rank',denominatorPolicy=view.denominator??'scheduled';
    require(['model','question'].includes(level)&&['rank','magnitude'].includes(xMode),'Unknown point level or x mode');
    require(['completed','scheduled'].includes(denominatorPolicy),'Unknown acceptance denominator policy');
    require(['T0','I0','J0','W_B'].includes(options.metric),'Unknown paper metric');
    require(options.sample==='all'||Number.isInteger(options.sample)&&options.sample>=1&&options.sample<=index.samples,'Sample must use one-based display indices');
    require(paperResult&&Array.isArray(paperResult.cohort)&&Array.isArray(paperResult.tasks)&&Array.isArray(paperResult.rows),'Malformed paper result');
    const cohort=[...paperResult.cohort],tasks=[...paperResult.tasks],M=cohort.length;
    require(new Set(cohort).size===M&&new Set(tasks).size===tasks.length,'Duplicate paper comparison identity');
    const selectedSamples=options.sample==='all'?index.samples:1;
    const metadata={schemaVersion:1,kind:'descriptive_compression_validity_association',level,xMode,metric:options.metric,
      condition:options.condition,cohort,tasks,sample:options.sample,denominatorPolicy,
      xDefinition:xMode==='rank'?'100*(cohort_size-mean_or_question_rank)/(cohort_size-1)':
        options.metric==='J0'?'normalized_J0_value':'log2_raw_'+options.metric,
      xDirection:xMode==='magnitude'&&options.metric==='W_B'?'increasing_means_longer_proof_source':'increasing_x',
      yDefinition:denominatorPolicy==='completed'?'accepted_completed_submissions / completed_nonempty_submitted_texts_on_shared_questions':'success_count / all_original_scheduled_selected_sample_rows_on_the_shared_questions',
      proofLengthFilterNarrowsValidityDenominator:false,
      minimumSamplesNarrowsValidityDenominator:false,
      scope:'Fixed paper cohort and shared questions; denominator policy changes only y. Scheduled counts and completed counts remain separate.',
      selectionForcesValidityOne:options.sample!=='all'||options.minSamples>=selectedSamples,
      spearmanXMethod:xMode==='rank'?'exact_rank_metadata':options.metric==='J0'?'J0_descending_anchor_midrank_ties_1e-12':'exact_positive_rational_product_roots',
      withinQuestionDefinition:'Pearson of x and y after subtracting each question\'s across-model means',
      notes:['Compression is available only for valid measurable outputs; conditioning on their availability and shared-question selection can induce associations.',
        'Proof-length and minimum-sample filters change compression eligibility and shared support, but never remove complete failed submissions or long valid submissions from a retained question\'s completed denominator.',
        'Completed acceptance excludes incomplete, empty and truncated submissions. Completion rate and scheduled solve rate are separately reported; unknown completion provenance makes completed acceptance unavailable.',
        'Model/question points repeat models and questions; point count is not a count of independent observations.',
        'Correlations are descriptive associations, not causal evidence, p-values or confidence intervals.',
        'I0 magnitude increases with proof length relative to the statement. W_B magnitude increases with raw proof length; a positive correlation then means longer, not more preferred.',
        'Rank coordinates are preferred-order rank points. Magnitude coordinates are raw logarithms except the exploratory normalized J0 blend.']};
    const empty=reason=>({points:[],pearson:{value:null,n:0,reason},spearman:{value:null,n:0,reason},
      withinQuestionPearson:{value:null,n:0,reason},metadata});
    if(M<2||!tasks.length||!paperResult.rows.length)return empty('no_shared_paper_comparison');
    const modelRows=new Map(paperResult.rows.map(row=>[row.model,row]));
    require(modelRows.size===M&&paperResult.rows.length===M&&cohort.every(id=>modelRows.has(id)),'Paper rows do not match cohort');
    const cellMap=new Map(),scopeRows=[];
    for(const model of cohort)for(const task of tasks){
      const cell=countCell(index,model,task,options);cellMap.set(JSON.stringify([model,task]),cell);scopeRows.push(...cell.rows);
    }
    const scopeOutcomes=summarizeOutcomes(scopeRows);metadata.outcomes=scopeOutcomes;
    if(denominatorPolicy==='completed'&&!scopeOutcomes.classificationAvailable)return empty('completion_classification_unavailable');
    if(denominatorPolicy==='completed'){
      const zero=level==='question'?[...cellMap.values()].some(cell=>cell.outcomes.completed===0):
        cohort.some(model=>tasks.reduce((sum,task)=>sum+cellMap.get(JSON.stringify([model,task])).outcomes.completed,0)===0);
      if(zero)return empty('no_completed_submissions');
    }
    function denominatorFields(outcomes){
      const denominator=denominatorPolicy==='completed'?outcomes.completed:outcomes.scheduled;
      return {denominator,completed:outcomes.completed,excludedIncomplete:denominatorPolicy==='completed'?outcomes.incomplete:0,
        completionRate:outcomes.completionRate,scheduledSolveRate:outcomes.scheduledSolveRate,
        completedAcceptanceRate:outcomes.completedAcceptanceRate,completionClassificationAvailable:outcomes.classificationAvailable,
        completedAttemptIds:[...outcomes.completedAttemptIds],truncated:outcomes.truncated,empty:outcomes.empty,
        unclassified:outcomes.unclassified};
    }
    const points=[],coordinates=new Map();
    for(const model of cohort){
      const row=modelRows.get(model),qs=new Map(row.questions.map(q=>[q.task,q]));
      require(qs.size===tasks.length&&row.questions.length===tasks.length&&tasks.every(task=>qs.has(task)),'Paper question rows do not match shared support');
      let totalValid=0,totalScheduled=0,totalMetric=0;const allAttempts=[],allMetricAttempts=[],allRows=[];
      for(const task of tasks){
        const q=qs.get(task),cell=cellMap.get(JSON.stringify([model,task]));
        require(Number.isInteger(q.samples)&&q.samples>0&&q.samples<=cell.valid&&q.samples>=options.minSamples,
          'Compression sample count differs from valid scheduled outcomes or its minimum');
        require(Array.isArray(q.sampleIndices)&&q.sampleIndices.length===q.samples&&new Set(q.sampleIndices).size===q.samples,
          'Missing or duplicate compression sample identities');
        require(Array.isArray(q.attemptIds)&&q.attemptIds.length===q.samples,'Missing compression attempt identities');
        for(let i=0;i<q.samples;i++){
          const source=cell.rows.find(r=>r.sample===q.sampleIndices[i]);
          require(source&&source.status==='success'&&source.metricStatus==='available',
            'Compression coordinate includes a failed, unavailable or unselected sample');
          require(q.attemptIds[i]===(typeof source.attemptId==='string'?source.attemptId:null),'Compression attempt identity differs from schedule');
          if(options.maxProofTokens!=null)require(Number.isFinite(source.metrics?.W_B)&&source.metrics.W_B<=options.maxProofTokens,
            'Compression coordinate violates its proof-length filter');
        }
        require(Number.isFinite(q.rank)&&q.rank>=1&&q.rank<=M,'Invalid question rank');
        totalValid+=cell.valid;totalScheduled+=cell.scheduled;totalMetric+=q.samples;allRows.push(...cell.rows);
        allAttempts.push(...cell.attemptIds);allMetricAttempts.push(...(q.attemptIds||[]));
        if(level==='question'){
          const x=xMode==='rank'?100*(M-q.rank)/(M-1):options.metric==='J0'?q.value:q.metricLog2;
          require(Number.isFinite(x),'Missing/nonfinite compression coordinate; no zero imputation');
          const rates=denominatorFields(cell.outcomes);
          const point={index:points.length,model,task,x,y:cell.valid/rates.denominator,valid:cell.valid,scheduled:cell.scheduled,...rates,
            metricSamples:q.samples,metricLog2:q.metricLog2,value:q.value,rank:q.rank,
            attemptIds:cell.attemptIds,metricAttemptIds:[...(q.attemptIds||[])]};
          coordinates.set(point,xMode==='rank'?q.rank:options.metric==='J0'?q.value:exact(q.exact?.[options.metric]));points.push(point);
        }
      }
      if(level==='model'){
        require(Number.isInteger(row.qualifyingSamples)&&row.qualifyingSamples===totalMetric,'Paper model sample count differs from its questions');
        require(Number.isFinite(row.averageRank)&&row.averageRank>=1&&row.averageRank<=M,'Invalid model mean rank');
        const x=xMode==='rank'?row.rankPoints:options.metric==='J0'?row.value:row.metricLog2;
        require(Number.isFinite(x),'Missing/nonfinite compression coordinate; no zero imputation');
        const rates=denominatorFields(summarizeOutcomes(allRows));
        const point={index:points.length,model,task:null,x,y:totalValid/rates.denominator,valid:totalValid,scheduled:totalScheduled,...rates,
          metricSamples:totalMetric,metricLog2:row.metricLog2,value:row.value,rank:row.averageRank,
          attemptIds:allAttempts,metricAttemptIds:allMetricAttempts};
        if(xMode==='rank'){
          const sum=row.exact?.rank_sum_twice;
          require(typeof sum==='string'&&/^\d+$/.test(sum),'Missing exact mean-rank sum');
          coordinates.set(point,BigInt(sum));
        }else coordinates.set(point,options.metric==='J0'?row.value:exact(row.exact?.[options.metric]));
        points.push(point);
      }
    }
    const xs=points.map(p=>p.x),ys=points.map(p=>p.y);
    const pearson=correlation(xs,ys,'Pearson_on_plotted_coordinates');
    const compareX=(a,b)=>{
      const x=coordinates.get(a),y=coordinates.get(b);
      if(xMode==='rank')return x>y?-1:x<y?1:0; // Smaller rank means larger plotted x.
      return options.metric==='J0'?b.x-a.x:compareExact(x,y);
    };
    const xr=midranks(points,compareX,{approximateDescending:xMode==='magnitude'&&options.metric==='J0'});
    const yr=midranks(points,(a,b)=>{
      const left=BigInt(a.valid)*BigInt(b.denominator),right=BigInt(b.valid)*BigInt(a.denominator);
      return left<right?-1:left>right?1:0;
    });
    const spearman=correlation(xr,yr,'Pearson_of_midrank_transforms');
    let withinQuestionPearson={value:null,n:points.length,reason:'requires_question_level',method:'within_question_demeaned_Pearson'};
    if(level==='question'){
      const centeredX=[],centeredY=[];
      for(const task of tasks){
        const group=points.filter(p=>p.task===task),mx=average(group.map(p=>p.x)),my=average(group.map(p=>p.y));
        for(const p of group){centeredX.push(p.x-mx);centeredY.push(p.y-my);}
      }
      withinQuestionPearson=correlation(centeredX,centeredY,'within_question_demeaned_Pearson');
    }
    metadata.pointCount=points.length;metadata.sharedQuestionCount=tasks.length;metadata.modelCount=M;
    metadata.allPointValiditiesEqual=points.every(p=>p.y===points[0].y);
    return {points,pearson,spearman,withinQuestionPearson,metadata};
  }
  const api=Object.freeze({compute,summarizeOutcomes,version:'compression_validity_v2_completed_denominators'});
  root.CompressionValidity=api;
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
})(typeof globalThis!=='undefined'?globalThis:this);
