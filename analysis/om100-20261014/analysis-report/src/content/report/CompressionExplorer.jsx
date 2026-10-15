import React, { useMemo } from "react";
import { DataComponent, DataTable, MetricCard, RichNarrative, SegmentedControl, Dropdown, useDataApp } from "../../data-app-public.jsx";
import { ReportDisclosure } from "../shared/ReportDisclosure.jsx";
import { aggregateRanks, rankQuestion, rankOutputs } from "./compression-ranking.mjs";
import "./compression-explorer.css";

const PROMPTS={standard:"Standard prompt",mathematical_compression:"Compression prompt",source_concision:"Shorter-source prompt"};
const PRESETS={coverage25:"Measured on ≥25 questions",coverage50:"Measured on ≥50 questions",all:"All eight models",custom:"Custom selection"};
const n=(x,d=2)=>Number.isFinite(x)?x.toLocaleString("en-US",{maximumFractionDigits:d,minimumFractionDigits:d}):"Unavailable";
const sci=x=>Number.isFinite(x)?x.toExponential(3):"Unavailable";
const pick=(value,choices,fallback)=>choices.includes(value)?value:fallback;
const rawT0=row=>Number.isFinite(row.meanLog2T0??row.log2T0)?2**(row.meanLog2T0??row.log2T0):null;
function Selector({label,value,choices,labels,onChange}) {
  return <Dropdown label={label} showLabel value={value} choices={choices} choiceLabels={labels} onChange={onChange}/>;
}
function Table({id,title,queryId,queryIds,sourceRows,sourceRowsByQuery,rows,columns,description,onRowSelect,rowKey,searchable=false,selectedRowKey,resetKey=0}) {
  return <DataComponent id={id} title={title} kind="table" queryId={queryId} queryIds={queryIds} sourceRows={sourceRows} sourceRowsByQuery={sourceRowsByQuery} displayRows={rows} description={description}>
    <DataTable key={resetKey} rows={rows} columns={columns} label={title} searchable={searchable} compactNumbers={false}
      onRowSelect={onRowSelect} rowKey={rowKey} selectedRowKey={selectedRowKey}
      rowActionLabel={onRowSelect?r=>r.attemptId?`Inspect ${r.modelLabel}, ${r.task}, ${r.promptLabel}, sample ${r.sample}`:`Inspect ${r.modelLabel??r.task}`:undefined}/>
  </DataComponent>;
}

export function CompressionExplorer() {
  const {snapshot,chartStates,updateChartState,visible}=useDataApp();
  const config=snapshot.compressionExplorer;
  const attempts=snapshot.queries.compression_attempts.rows;
  const cells=snapshot.queries.compression_cells.rows;
  const coverage=snapshot.queries.compression_coverage.rows;
  const questions=snapshot.queries.compression_questions.rows;
  const ids=config.modelIds, labels=config.modelLabels;
  const saved=chartStates["compression-explorer"]?.inlineFilters??{};
  const change=patch=>updateChartState("compression-explorer",{inlineFilters:{...saved,...patch}});
  const view=pick(saved.view,["models","questions","outputs"],"models");
  const condition=pick(saved.condition,Object.keys(PROMPTS),"mathematical_compression");
  const metric=pick(saved.metric,["T0","WB"],"T0");
  const preset=pick(saved.preset,Object.keys(PRESETS),"coverage25");
  const task=pick(saved.task,questions.map(q=>q.task),config.defaults.defaultQuestion);
  const selectedCoverage=coverage.filter(r=>r.condition===condition);
  const models=useMemo(()=>preset==="custom"?ids.filter(id=>Array.isArray(saved.models)&&saved.models.includes(id)):
    preset==="all"?ids:ids.filter(id=>selectedCoverage.find(r=>r.model===id).measurableQuestionCount>=(preset==="coverage50"?50:25)),[preset,condition,saved.models]);
  const board=useMemo(()=>aggregateRanks(cells,{condition,models,metric}),[cells,condition,models,metric]);
  const question=useMemo(()=>rankQuestion(cells,{condition,models,metric,task}),[cells,condition,models,metric,task]);
  const outputRanks=useMemo(()=>rankOutputs(attempts,{metric}),[attempts,metric]);
  const displayModels=board.rows.map(r=>({...r,modelLabel:labels[r.model],rawT0:rawT0(r)}));
  const questionRows=question.rows.map(r=>({...r,modelLabel:labels[r.model],rawT0:rawT0(r)}));
  const sourceCells=cells.filter(r=>r.condition===condition&&models.includes(r.model));
  const qSource=cells.filter(r=>r.condition===condition&&r.task===task);
  const excluded=selectedCoverage.filter(r=>!models.includes(r.model));
  const questionMeta=questions.find(q=>q.task===task);
  const outTask=pick(saved.outTask,["all",...questions.map(q=>q.task)],task);
  const outPrompt=pick(saved.outPrompt,["all",...Object.keys(PROMPTS)],condition);
  const outModel=pick(saved.outModel,["all",...ids],"all");
  const outStatus=pick(saved.outStatus,["all","measured","unavailable"],"all");
  const outSample=pick(saved.outSample,["all","1","2","3","4"],"all");
  const outputSource=attempts.filter(r=>(outTask==="all"||r.task===outTask)&&(outPrompt==="all"||r.condition===outPrompt));
  const outputRows=useMemo(()=>outputRanks.filter(r=>(outTask==="all"||r.task===outTask)&&(outPrompt==="all"||r.condition===outPrompt)&&
    (outModel==="all"||r.model===outModel)&&(outStatus==="all"||(outStatus==="measured"?r.measurable:!r.measurable))&&
    (outSample==="all"||r.sample===Number(outSample))).map(r=>({...r,modelLabel:labels[r.model],rawT0:rawT0(r),promptLabel:PROMPTS[r.condition]})),
    [outputRanks,outTask,outPrompt,outModel,outStatus,outSample]);
  const detail=outputRows.find(r=>r.attemptId===saved.attempt)??null;
  const measured=attempts.filter(r=>r.measurable).length;
  const questionLabels=Object.fromEntries(questions.map(q=>[q.task,`${q.task}: ${q.title}`]));
  const drill=(model,questionId=task)=>change({view:"outputs",outModel:model,outTask:questionId,outPrompt:condition,outStatus:"all",outSample:"all",attempt:""});
  const rankedTitle=metric==="T0"?"Compression rank points":"Proof-concision rank points";

  return <section className="compression-explorer" id="compression-explorer" aria-label="Compression explorer">
    {visible("compression-explorer-intro")&&<DataComponent id="compression-explorer-intro" title="Compression explorer" kind="custom" queryId="compression_attempts" sourceRows={attempts}
      displayRows={[{scheduled:attempts.length,measurable:measured,unavailable:attempts.length-measured}]}>
      <RichNarrative id="compression-intro:body" value="Compare raw compression scores and rank models on the same questions. Higher T₀ means more measured compression."/>
      <p className="compression-inventory" data-reviewed-rows><strong>{attempts.length.toLocaleString()} outputs</strong> · {measured.toLocaleString()} measured proofs · {(attempts.length-measured).toLocaleString()} without a valid compression measurement · final 128,000-token run</p>
    </DataComponent>}

    <SegmentedControl ariaLabel="Compression explorer view" value={view} onChange={v=>change({view:v})}
      options={[{value:"models",label:"Model ranks"},{value:"questions",label:"Question ranks"},{value:"outputs",label:"Outputs"}]}/>

    {view!=="outputs"&&<div className="compression-controls" aria-label="Compression comparison settings">
      <Selector label="Prompt" value={condition} choices={Object.keys(PROMPTS)} labels={PROMPTS} onChange={v=>change({condition:v})}/>
      <Selector label="Rank by" value={metric} choices={["T0","WB"]} labels={{T0:"T₀ compression · higher is better",WB:"Proof length W_B · lower is better"}} onChange={v=>change({metric:v})}/>
      <Selector label="Models" value={preset} choices={Object.keys(PRESETS)} labels={PRESETS} onChange={v=>change({preset:v,models})}/>
    </div>}

    {view==="models"&&<>
      <div className="compression-scope" data-reviewed-rows>
        <strong>{models.length} models · {board.sharedTasks.length} shared questions</strong>
        <span>{PROMPTS[condition]} · same questions for every selected model · {metric==="T0"?"higher T₀ ranks first":"shorter W_B ranks first"}</span>
      </div>
      {!board.reason&&<div className="compression-podium">
        {displayModels.slice(0,3).map((r,i)=><MetricCard key={r.model} id={`compression-podium-${i+1}`} title={`#${r.place} ${r.modelLabel}`} queryId="compression_cells"
          sourceRows={sourceCells} displayRows={[r]} value={`${n(r.rankPoints,1)} / 100`} comparison={rankedTitle}
          description={`${rankedTitle}, relative to ${models.length} selected models on ${board.sharedTasks.length} shared questions. Not percentage compression.`}>
          <div className="compression-podium-detail" data-reviewed-rows>Mean rank {n(r.meanRank,2)} · first on {r.firstPlaceCount} questions{r.tiedFirstPlaceCount?` + ${r.tiedFirstPlaceCount} tied`:""}</div>
        </MetricCard>)}
      </div>}
      {board.reason&&<div className="compression-empty" role="status" data-reviewed-rows>
        <strong>{models.length<2?"Select at least two models to rank them.":"No shared questions — aggregate ranks are unavailable."}</strong>
        <p>{models.length<2?"Individual measurements remain available below.":"No question has a measured proof from every selected model under this prompt. Missing proofs do not receive a zero compression score."}</p>
        <button type="button" className="compression-action" onClick={()=>change({preset:"coverage25"})}>Use ≥25-question coverage group</button>
        <button type="button" className="compression-action" onClick={()=>change({view:"questions"})}>Inspect a question</button>
      </div>}
      {visible("compression-model-ranking")&&<Table id="compression-model-ranking" title={rankedTitle} queryId="compression_cells" sourceRows={sourceCells} rows={displayModels}
        description="Observed descriptive ranking, conditional on measured proofs. Within each question, use the geometric mean of available samples; average midranks equally over shared questions. Source retains all 100 candidate questions per selected model to support coverage and the shared-question intersection. Select a model to inspect its four outputs on the selected question."
        rowKey="model" onRowSelect={r=>drill(r.model)} columns={[
          {field:"place",label:"Place",renderCell:v=>v?`#${v}`:"Unavailable"},
          {field:"modelLabel",label:"Model"},
          {field:"rankPoints",label:"Rank points / 100",renderCell:v=><strong>{n(v)}</strong>},
          {field:"meanRank",label:"Mean rank",renderCell:v=>n(v)},
          {field:"firstPlaceCount",label:"First / shared",renderCell:(_,r)=>board.reason?"Unavailable":`${r.firstPlaceCount} / ${r.sharedQuestionCount}${r.tiedFirstPlaceCount?` (+${r.tiedFirstPlaceCount} tied)`:""}`},
          {field:"rawT0",label:"Raw T₀ · geometric mean",renderCell:sci},
          {field:"geomeanWB",label:"W_B · geometric mean",renderCell:v=>n(v,1)},
          {field:"measuredQuestionCount",label:"Measured / 100",renderCell:v=>`${v} / 100`}
        ]}/>}
      <RichNarrative id="compression-rank-formula:body" value="**How to read rank points:** first on every shared question = 100; last on every shared question = 0. For each question, points = 100 × (number of models − rank) ÷ (number of models − 1); exact ties share the average rank. Then average the points across the same questions. These are **rank points, not percent compression**. Raw T₀ and W_B are also shown; magnitude summaries use those same shared questions, weighted equally. Rankings are descriptive and do not establish a statistically resolved winner."/>
    </>}

    {view!=="outputs"&&<ReportDisclosure id="compression-model-selection" label={`Choose models and inspect coverage (${models.length} selected)`}>
      {visible("compression-model-coverage")&&<DataComponent id="compression-model-coverage" title="Model coverage and selection" kind="custom" queryId="compression_coverage" sourceRows={selectedCoverage} displayRows={selectedCoverage}>
        <div className="compression-roster" data-reviewed-rows>{selectedCoverage.map(r=><label key={r.model}>
          <input type="checkbox" checked={models.includes(r.model)} onChange={e=>change({preset:"custom",models:e.target.checked?[...models,r.model]:models.filter(x=>x!==r.model)})}/>
          <span>{r.modelLabel}<small>{r.measurableQuestionCount} / 100 questions measured · {r.validCount} / 400 accepted outputs</small></span>
        </label>)}</div>
        <p className="compression-note">Changing the roster changes the shared-question population. Scores from different rosters or prompts are not directly comparable.</p>
      </DataComponent>}
    </ReportDisclosure>}
    {view==="models"&&excluded.length>0&&<p className="compression-note" data-reviewed-rows>Outside this ranking: {excluded.map(r=>`${r.modelLabel} (${r.measurableQuestionCount}/100 measured questions)`).join("; ")}. Their outputs remain available in the other views.</p>}

    {view==="questions"&&<>
      <div className="compression-question-picker">
        <Selector label="Question" value={task} choices={questions.map(q=>q.task)} labels={questionLabels} onChange={v=>change({task:v})}/>
        <button type="button" className="compression-action" onClick={()=>change({task:questions[(questions.findIndex(q=>q.task===task)+99)%100].task})}>Previous</button>
        <button type="button" className="compression-action" onClick={()=>change({task:questions[(questions.findIndex(q=>q.task===task)+1)%100].task})}>Next</button>
      </div>
      <div className="compression-scope" data-reviewed-rows><strong>{task} · {questionMeta.title}</strong><span>{questionMeta.subject} · {question.inAggregate?`Included in the ${board.sharedTasks.length}-question aggregate`:"Not included in the aggregate"}</span></div>
      {!question.inAggregate&&<p className="compression-note" data-reviewed-rows>This question is not jointly measurable for the selected roster. Only its {question.eligibleCount} measured selected models receive a question rank; these partial ranks are not used in the aggregate.</p>}
      {visible("compression-question-ranking")&&<Table id="compression-question-ranking" title={`Question ranking · ${metric==="T0"?"higher T₀":"shorter W_B"} first`} queryId="compression_cells"
        queryIds={["compression_cells","compression_questions"]} sourceRowsByQuery={{compression_cells:qSource,compression_questions:[questionMeta]}}
        sourceRows={qSource} rows={questionRows} rowKey="model" onRowSelect={r=>drill(r.model)} description="All eight models stay visible. Each metric is the geometric mean of accepted measurable samples on this question. Select a row to see all four outputs, including failures."
        columns={[
          {field:"rank",label:"Question rank",renderCell:(_,r)=>!r.selected?"Not selected":r.rank==null?"Unavailable":`#${n(r.rank,r.rank%1?1:0)}`},
          {field:"modelLabel",label:"Model"},{field:"rawT0",label:"Raw T₀",renderCell:sci},
          {field:"geomeanWB",label:"Proof length W_B",renderCell:v=>n(v,1)},
          {field:"measurableCount",label:"Measured / 4",renderCell:v=>`${v??0} / 4`},
          {field:"validCount",label:"Accepted / 4",renderCell:v=>`${v??0} / 4`},
          {field:"rankPoints",label:"Question rank points",renderCell:v=>n(v,1)}
        ]}/>}
      <p className="compression-note">A row’s raw T₀ describes measured compression. Its rank describes position among the selected models on this question. Unavailable proofs are never assigned compression zero.</p>
    </>}

    {view==="outputs"&&<>
      <div className="compression-controls" aria-label="Individual output filters">
        <Selector label="Question" value={outTask} choices={["all",...questions.map(q=>q.task)]} labels={{all:"All 100 questions",...questionLabels}} onChange={v=>change({outTask:v,attempt:""})}/>
        <Selector label="Prompt" value={outPrompt} choices={["all",...Object.keys(PROMPTS)]} labels={{all:"All three prompts",...PROMPTS}} onChange={v=>change({outPrompt:v,attempt:""})}/>
        <Selector label="Model" value={outModel} choices={["all",...ids]} labels={{all:"All eight models",...labels}} onChange={v=>change({outModel:v,attempt:""})}/>
        <Selector label="Measurement" value={outStatus} choices={["all","measured","unavailable"]} labels={{all:"All outcomes",measured:"Measured proofs",unavailable:"Unavailable compression"}} onChange={v=>change({outStatus:v,attempt:""})}/>
        <Selector label="Sample" value={outSample} choices={["all","1","2","3","4"]} labels={{all:"All four samples"}} onChange={v=>change({outSample:v,attempt:""})}/>
        <Selector label="Output rank metric" value={metric} choices={["T0","WB"]} labels={{T0:"T₀ · higher first",WB:"W_B · lower first"}} onChange={v=>change({metric:v})}/>
        <button type="button" className="compression-action" onClick={()=>change({outTask:"all",outPrompt:"all",outModel:"all",outStatus:"all",outSample:"all",attempt:"",outputReset:(Number.isSafeInteger(saved.outputReset)?saved.outputReset:0)+1})}>Show all 9,600 outputs</button>
      </div>
      <div className="compression-scope" data-reviewed-rows><strong>{outputRows.length.toLocaleString()} outputs in selected filters · {outputRows.filter(r=>r.measurable).length.toLocaleString()} with compression measurements</strong>
        <span>Each output’s rank compares all measured outputs for its question + prompt, across eight models × four samples. Display filters do not change ranks.</span></div>
      {visible("compression-output-table")&&<Table id="compression-output-table" title="Individual output measurements" queryId="compression_attempts" sourceRows={outputSource} rows={outputRows}
        searchable resetKey={saved.outputReset??0} rowKey="attemptId" selectedRowKey={detail?.attemptId} onRowSelect={r=>change({attempt:r.attemptId})}
        description="Select any row for its exact fraction and metric components. Source evidence retains the full eight-model comparison group before model, status and sample display filters. Scientific notation is rounded for display; ranks use exact integer arithmetic."
        columns={[
          {field:"outputRank",label:`${metric==="T0"?"T₀":"W_B"} rank / measured`,renderCell:(_,r)=>r.outputRank==null?"Unavailable":`${n(r.outputRank,r.outputRank%1?1:0)} / ${r.outputPopulation}`},
          {field:"modelLabel",label:"Model"},{field:"task",label:"Question"},{field:"promptLabel",label:"Prompt"},{field:"sample",label:"Sample"},
          {field:"status",label:"Proof outcome",renderCell:v=>v==="success"?"Accepted":v.replaceAll("_"," ")},
          {field:"rawT0",label:"Raw T₀",renderCell:sci},{field:"W_B",label:"Proof tokens W_B",renderCell:v=>n(v,0)},
          {field:"sourceNonWhitespaceCharacters",label:"Source characters",renderCell:v=>n(v,0)}
        ]}/>}
      {!detail&&<p className="compression-note">Select an output row to inspect the exact T₀ numerator and denominator, statement/proof sizes, and its rank.</p>}
      {detail&&visible("compression-output-detail")&&<DataComponent id="compression-output-detail" title={`${detail.modelLabel} · ${detail.task} · sample ${detail.sample}`} kind="custom" queryId="compression_attempts"
        sourceRows={outputSource.filter(r=>r.task===detail.task&&r.condition===detail.condition)} displayRows={[detail]} description={`${PROMPTS[detail.condition]}. Rank evidence includes all 32 scheduled slots in this question/prompt group.`}>
        <div className="compression-output-detail" data-reviewed-rows>
          <dl>
            <div><dt>Proof outcome</dt><dd>{detail.status==="success"?"Accepted":detail.status.replaceAll("_"," ")}</dd></div>
            <div><dt>Raw T₀</dt><dd>{detail.t0Scientific??"Unavailable"}</dd></div>
            <div><dt>Rank in this question + prompt</dt><dd>{detail.outputRank==null?"Unavailable":`${n(detail.outputRank,1)} of ${detail.outputPopulation} measured outputs (${metric})`}</dd></div>
            <div><dt>Exact T₀ = (U_S + U_B) / (W_S + W_B)</dt><dd>{detail.measurable?<><code>{detail.t0Numerator}</code> / <code>{detail.t0Denominator}</code></>:"Unavailable: this output has no accepted, measured proof."}</dd></div>
            <div><dt>Expanded statement size U_S</dt><dd>{detail.U_S_decimal??"Unavailable"}</dd></div>
            <div><dt>Expanded proof size U_B</dt><dd>{detail.U_B_decimal??"Unavailable"}</dd></div>
            <div><dt>Statement tokens W_S / proof tokens W_B</dt><dd>{n(detail.W_S,0)} / {n(detail.W_B,0)}</dd></div>
            <div><dt>I₀ = W_B / W_S</dt><dd>{detail.i0Numerator?`${detail.i0Numerator} / ${detail.i0Denominator} ≈ ${detail.i0Scientific}`:"Unavailable"}</dd></div>
            <div><dt>Non-whitespace source characters</dt><dd>{n(detail.sourceNonWhitespaceCharacters,0)}</dd></div>
            <div><dt>Attempt ID</dt><dd><code>{detail.attemptId}</code></dd></div>
          </dl>
        </div>
      </DataComponent>}
    </>}
    <ReportDisclosure id="compression-methods" label="Metric definitions, ranking rules, and limitations">
      <RichNarrative id="compression-methods:body" value={`**T₀ = (U_S + U_B) / (W_S + W_B).** U counts expanded dependency size; W counts canonical source tokens. Higher T₀ means more represented expansion per source token. It does not compare against a reference proof. Library and typeclass expansion can dominate this metric.

**W_B** is the canonical proof-source token count. Lower means a shorter measured proof. It is different from API output tokens and from non-whitespace source characters, which can also be counted for failed answers. **I₀ = W_B / W_S** is shown in individual details; larger is not universally better.

**Samples and missingness:** each model has four scheduled samples per question and prompt. Use the geometric mean of accepted measurable samples; report how many exist. Unavailable proofs stay in the output inventory but receive no T₀ or W_B score. No best-of-four selection is used.

**Aggregate comparison:** keep the selected model roster fixed and use only questions measurable for every selected model. Rank each question by exact rational arithmetic, with average ranks for ties, then average questions equally. Coverage ≥25 is a transparent display preset, not a scientific qualification threshold. The ranking remains conditional on successful proofs and should be read with acceptance and coverage below.

**Scope:** these rankings summarize the completed 128,000-token experiment. They are exploratory descriptions of a curated corpus, not new significance tests or an overall intelligence score. Existing statistical comparisons and their uncertainty remain in the report below.`}/>
    </ReportDisclosure>
  </section>;
}
