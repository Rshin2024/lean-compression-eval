import React, { useState } from "react";
import { DataComponent, DataTable, EvidenceChart, ReportSection, RichNarrative, useDataApp } from "../../data-app-public.jsx";
import { CompressionExplorer } from "./CompressionExplorer.jsx";
import { ReportDisclosure } from "../shared/ReportDisclosure.jsx";

const pct=(x,d=1)=>Number.isFinite(x)?`${x.toFixed(d)}%`:"Unavailable";
const number=(x,d=1)=>Number.isFinite(x)?x.toLocaleString(undefined,{minimumFractionDigits:d,maximumFractionDigits:d}):"Unavailable";
const interval=(a,b,d=1)=>Number.isFinite(a)&&Number.isFinite(b)?`[${number(a,d)}, ${number(b,d)}]`:"Unavailable";
const money=x=>Number.isFinite(x)?new Intl.NumberFormat("en-US",{style:"currency",currency:"USD",maximumFractionDigits:2}).format(x):"Unknown";
const signed=x=>Number.isFinite(x)?`${x>0?"+":""}${x.toFixed(2)}`:"Unavailable";
function Narrative({id,title,queryIds,rows,children}) {
  return <ReportSection id={id} title={title} queryId={queryIds[0]} queryIds={queryIds}
    sourceRowsByQuery={Object.fromEntries(queryIds.map(q=>[q,rows[q]]))} showHeading={false}>
    <RichNarrative id={`${id}:body`} value={children} label="Edit interpretation" />
  </ReportSection>;
}
function EvidenceTable({id,title,queryId,rows,sourceRows=rows,columns,description,controls}) {
  return <DataComponent id={id} title={title} kind="table" queryId={queryId} sourceRows={sourceRows} displayRows={rows} description={description}>
    <DataTable rows={rows} columns={columns} searchable={false} compactNumbers={false} label={title} toolbarControls={controls}/>
  </DataComponent>;
}
export function ReportContent() {
  const {snapshot,visible,appTitle,setAppTitle,canEdit,mode}=useDataApp();
  const rows=Object.fromEntries(Object.entries(snapshot.queries).map(([k,v])=>[k,v.rows]));
  const [prompt,setPrompt]=useState("Compression prompt");
  const [promptMetric,setPromptMetric]=useState("t0");
  const rates=rows.standard_rates.filter(r=>r.measure==="valid").sort((a,b)=>b.value-a.value);
  const passRows=rates.map(r=>{const p=rows.standard_rates.find(p=>p.model===r.model&&p.measure==="pass4");return {...r,pass4Questions:Math.round(p.value),pass4Low:p.ciLow,pass4High:p.ciHigh};});
  const yieldPlot=rates.map(r=>({...r,"Accepted (%)":r.value}));
  const cross=rows.cross_run.map(r=>({...r,"8,192 tokens (%)":r.baseline,"128,000 tokens (%)":r.rerun})).sort((a,b)=>b.rerun-a.rerun);
  const effects=rows.prompt_effects.filter(r=>r.prompt===prompt);
  const pairs=[...rows.conditional_t0].sort((a,b)=>b.sharedQuestions-a.sharedQuestions);
  const summaryRows={standard_rates:rows.standard_rates,conditional_t0:rows.conditional_t0,cross_run:rows.cross_run,methods:rows.methods};
  return <article className="report-content paper-report" aria-label="Lean compression analysis report">
    <header className="report-hero">
      <h1 data-data-app-title contentEditable={canEdit&&mode==="edit"} suppressContentEditableWarning
        onBlur={canEdit&&mode==="edit"?e=>setAppTitle(e.currentTarget.textContent.trim()||appTitle):undefined}>{appTitle}</h1>
      <RichNarrative id="paper:deck" className="report-deck" value="Eight models on 100 Lean proof problems. A readable companion to the statistical paper, covering proof success, conditional compression and budget sensitivity." />
    </header>

    <CompressionExplorer />

    {visible("executive-summary")&&<Narrative id="executive-summary" title="Executive summary" queryIds={["standard_rates","conditional_t0","cross_run","methods"]} rows={summaryRows}>{`## Executive summary

- **Fable has the highest observed standard-prompt success rate: 77.0%.** Astra is close at 75.25%. With four samples, Fable solves 96 of 100 questions and Astra solves 89.
- **Compression remains a separate comparison.** On shared solved questions, Fable wins 60.9% of valid-proof T₀ pairings against Astra, but 48.7% against Opus. The Fable–Opus interval includes a tie, and no question is jointly measurable for all eight models under the compression prompt.
- **The larger-budget run returns more usable answers.** Across all prompts, accepted proofs rise from 25.47% to 39.91%. The runs also differ in processing tier and transport, so this is not an isolated token-cap experiment.`}</Narrative>}

    <section className="report-section" aria-label="Successful proof benchmarks">
      {visible("main-findings")&&<Narrative id="main-findings" title="Measure accepted proofs before compression" queryIds={["standard_rates"]} rows={rows}>{`## Measure accepted proofs before compression

The standard prompt provides the clearest starting point. Pass@1 is the observed success rate across four samples per question; pass@4 counts a question as solved if any of those four proofs is accepted. A higher point estimate alone does not establish a resolved ranking between nearby models.`}</Narrative>}
      {visible("standard-yield")&&<EvidenceChart id="standard-yield" title="Standard prompt: accepted proofs per 100 scheduled attempts" queryId="standard_rates"
        spec={{type:"horizontalBar",x:"label",y:"Accepted (%)",startAtZero:true,valueDecimals:2,stackable:false}}
        rows={yieldPlot} sourceRows={rates} height={360} description="400 attempts per model. Bars show observed yield; the table below retains the 95% family-block intervals."/>}
      {visible("pass-four")&&<EvidenceTable id="pass-four" title="Four-sample coverage and uncertainty" queryId="standard_rates" rows={passRows} sourceRows={rows.standard_rates}
        description="100 questions per model, standard prompt. Intervals resample 19 problem families, not 400 independent attempts."
        columns={[{field:"label",label:"Model"},{field:"value",label:"Pass@1",renderCell:v=>pct(v,2)},
          {field:"ciLow",label:"95% interval",renderCell:(_,r)=>interval(r.ciLow,r.ciHigh)},
          {field:"pass4Questions",label:"Solved / 100",renderCell:v=>`${v} / 100`},
          {field:"pass4Low",label:"Pass@4 interval",renderCell:(_,r)=>interval(r.pass4Low,r.pass4High)}]}/>}
    </section>

    <section className="report-section" aria-label="Original prompt comparisons">
      {visible("prompt-findings")&&<Narrative id="prompt-findings" title="Keep the original prompt comparisons central" queryIds={["prompt_effects"]} rows={rows}>{`## Keep the original prompt comparisons central

The evaluation plan compares ordinary proof generation with a compression instruction and a shorter-source instruction. Read success and T₀ together: a prompt can change the proofs that succeed as well as their measured compression. Yield uses all 100 questions; each T₀ or proof-length contrast uses only questions measurable under both prompts. Switch the metric to inspect whether the shorter-source instruction actually reduces measured proof length.`}</Narrative>}
      {visible("prompt-effects")&&<EvidenceTable id="prompt-effects" title={`${prompt} versus standard`} queryId="prompt_effects" rows={effects}
        description={`Yield changes are percentage points. ${promptMetric==="t0"?"Larger T₀ means greater measured compression.":"Smaller W_B means a shorter canonical-token proof."} Ratios compare with standard; 1 means no change. Bounds are 95% family-block intervals.`}
        controls={<div className="paper-controls"><label className="paper-control">Instruction <select value={prompt} onChange={e=>setPrompt(e.target.value)}><option>Compression prompt</option><option>Shorter-source prompt</option></select></label><label className="paper-control">Metric <select value={promptMetric} onChange={e=>setPromptMetric(e.target.value)}><option value="t0">T₀ compression</option><option value="wb">Proof length W_B</option></select></label></div>}
        columns={[{field:"model",label:"Model"},{field:"yieldChange",label:"Yield change",renderCell:v=>`${signed(v)} pp`},
          {field:"yieldLow",label:"95% interval (pp)",renderCell:(_,r)=>interval(r.yieldLow,r.yieldHigh,2)},
          {field:`${promptMetric}Ratio`,label:promptMetric==="t0"?"T₀ ratio":"W_B ratio",renderCell:v=>number(v,3)},
          {field:`${promptMetric}Low`,label:"95% interval",renderCell:(_,r)=>interval(r[`${promptMetric}Low`],r[`${promptMetric}High`],3)},
          {field:`${promptMetric}SharedQuestions`,label:"Shared questions"}]}/>}
    </section>

    <section className="report-section" aria-label="Conditional compression comparisons">
      {visible("compression-findings")&&<Narrative id="compression-findings" title="Fable exceeds Astra on T₀, while Fable versus Opus remains unresolved" queryIds={["conditional_t0","methods"]} rows={rows}>{`## Fable exceeds Astra on T₀, while Fable versus Opus remains unresolved

Under the compression prompt, Fable’s conditional win share against Astra is **60.9% [53.4%, 69.0%]** across 77 shared questions. Against Opus it is **48.7% [40.3%, 57.2%]** across 78 questions. These exploratory intervals support different conclusions for the two comparisons.

Each row has its own shared-question population. It is therefore not a complete eight-model leaderboard. Failures receive no T₀ value, and there are **zero questions** measurable for all eight models in this prompt condition.`}</Narrative>}
      {visible("t0-comparisons")&&<EvidenceTable id="t0-comparisons" title="Fable’s conditional T₀ head-to-head results" queryId="conditional_t0" rows={pairs}
        description="Higher exact T₀ wins; ties count one-half. Valid-proof pairings are averaged within question, then questions receive equal weight."
        columns={[{field:"opponent",label:"Opponent"},{field:"winShare",label:"Fable win share",renderCell:v=>pct(v)},
          {field:"ciLow",label:"95% family interval",renderCell:(_,r)=>`${interval(r.ciLow,r.ciHigh)}%`},
          {field:"sharedQuestions",label:"Shared questions"},{field:"families",label:"Families"}]}/>}
    </section>

    <section className="report-section" aria-label="Cross-run results">
      {visible("cross-run-findings")&&<Narrative id="cross-run-findings" title="The final cohort contains many more completed submissions" queryIds={["cross_run","costs"]} rows={rows}>{`## The final cohort contains many more completed submissions

Across all models and prompts, completion rises from **48.26% to 98.72%**, while scheduled success rises by **14.44 percentage points [11.66, 17.19]**. Opus and Fable show the largest observed yield changes.

The same question, model, prompt and sample positions are paired across runs, but the generations are separate. The final run uses a 128,000-token ceiling, OpenAI priority/Fast processing and streaming transport; the baseline uses 8,192 tokens and default OpenAI processing. The comparison does not isolate a causal effect of the cap.`}</Narrative>}
      {visible("cross-run-yield")&&<EvidenceChart id="cross-run-yield" title="Accepted-proof yield across all three prompts" queryId="cross_run"
        spec={{type:"horizontalBar",x:"model",y:"8,192 tokens (%)",fields:["8,192 tokens (%)","128,000 tokens (%)"],startAtZero:true,stackable:false,valueDecimals:2}}
        rows={cross} sourceRows={rows.cross_run} height={420} description="Each bar uses 1,200 scheduled attempts. The source table includes paired family-block intervals for the change."/>}
    </section>

    <section className="report-section" aria-label="Length and formatting sensitivity">
      {visible("length-findings")&&<Narrative id="length-findings" title="The source-length association changes when formatting failures are excluded" queryIds={["source_length","methods"]} rows={rows}>{`## The source-length association changes when formatting failures are excluded

Within the same model, question and prompt, doubling source length is associated with **−2.87 percentage points** of acceptance among completed submissions. Excluding parse and format failures changes the slope to **+0.55 points**. This sensitivity makes a general “shorter is better” conclusion unsupported.

Length here means non-whitespace source characters, not API tokens or the paper’s expanded proof size. Separate held-out-sample analyses of T₀ are inconclusive about its association with later proof success.`}</Narrative>}
      {visible("length-sensitivity")&&<EvidenceTable id="length-sensitivity" title="Within-cell length sensitivity" queryId="source_length" rows={rows.source_length}
        description="Observed probability change per doubling of source characters. The covariance/variance slope is observational, not a causal effect."
        columns={[{field:"population",label:"Population"},{field:"value",label:"Change per doubling",renderCell:v=>`${signed(v)} pp`},
          {field:"ciLow",label:"95% family interval",renderCell:(_,r)=>`${interval(r.ciLow,r.ciHigh,2)} pp`},{field:"submissions",label:"Submissions"}]}/>}
      {visible("format-findings")&&<Narrative id="format-findings" title="Formatting is part of what this benchmark measures" queryIds={["format_audit"]} rows={rows}>{`## Formatting is part of what this benchmark measures

Haiku and Sonnet each have more than 1,000 parse failures among 1,200 responses. Most begin failing on the first line; saved examples often combine prose or Markdown with a proof instead of supplying the required Lean term. These outcomes remain failures under the frozen protocol.

For Opus, parse failures fall from **146 under the standard prompt to 31 under the shorter-source prompt**, out of 400 responses in each condition. Prompt effects therefore include interface compliance. These scores measure acceptance under the strict submission rules, not pure mathematical ability.

The audit does not extract or repair a proof and rerun it, so these diagnostics do not establish hidden mathematical success.`}</Narrative>}
      {visible("format-audit")&&<EvidenceTable id="format-audit" title="Recorded parse failures in three audited models" queryId="format_audit" rows={rows.format_audit}
        description="Complete diagnostic counts for the three audited models; first-line counts are a subset of parse failures."
        columns={[{field:"model",label:"Model"},{field:"parseFailures",label:"Total / 1,200"},{field:"firstLine",label:"At first line"},{field:"standardParse",label:"Standard / 400"},{field:"shorterParse",label:"Shorter source / 400"}]}/>}
    </section>

    <section className="report-section" aria-label="Costs and limitations">
      {visible("cost-findings")&&<Narrative id="cost-findings" title="Recorded-response costs are known; complete invoices are not" queryIds={["costs"]} rows={rows}>{`## Recorded-response costs are known; complete invoices are not

The final run’s recorded-response API estimate is **$4,369.72**, compared with **$1,848.44** for the baseline. Failed and truncated responses are included. The final run requested OpenAI Fast/priority processing, so cost changes reflect both token usage and processing prices.

The separately listed original interrupted requests have unknown charges. The rerun compute estimate covers closed VM allocation intervals. The baseline retains its historical as-of subtotal. Storage, IP, network, tax and credit adjustments are not included.`}</Narrative>}
      {visible("cohort-costs")&&<EvidenceTable id="cohort-costs" title="Known estimates and unknown charges" queryId="costs" rows={rows.costs}
        description="No combined invoice total is calculated. Dollar estimates remain separate from scientific performance scores."
        columns={[{field:"cohort",label:"Cohort"},{field:"apiEstimate",label:"Recorded API estimate",renderCell:money},{field:"computeEstimate",label:"VM compute",renderCell:money},
          {field:"computeScope",label:"Compute scope"},{field:"unknownRequests",label:"Requests with unknown cost"},{field:"invoiceTotal",label:"Full invoice",renderCell:()=>"Unknown"}]}/>}
      {visible("methods-limits")&&<ReportSection id="methods-limits" title="Methods and limitations" queryId="methods" queryIds={["methods","prompt_effects"]} sourceRowsByQuery={{methods:rows.methods,prompt_effects:rows.prompt_effects}} showHeading={false}>
        <RichNarrative id="methods-limits:body" value={`## What the numbers can and cannot establish

The 100 selected lemmas span 19 problem families. Intervals use 10,000 family-block bootstrap resamples and retain question weighting; question-bootstrap intervals are a sensitivity analysis. This is a curated corpus, not a probability sample of mathematics.

T₀ is the expanded statement-and-proof size divided by their source size. Large expansion can come from library and typeclass dependencies. Audits of two extreme questions show why an enormous T₀ is not automatically a more informative mathematical argument. I₀ compares proof-source size with statement-source size; a larger value is not universally preferable.

The original prompt comparisons remain primary. A later joint reliability-plus-T₀ contest is supplemental, was designed after observing the data, and is not the original paper metric.`}/>
        <ReportDisclosure id="methods-detail" label="Study boundaries and reproducibility">
          <RichNarrative id="methods-limits:detail" value={`- The final main cohort contains 9,600 attempts: eight pinned models, 100 questions, three prompts and four samples, all at requested high effort. High is a common label, not an equal-compute guarantee.
- Only Lean-accepted proofs receive compression measurements. Empty answers, truncations and parse failures are retained in scheduled-success denominators, but not assigned compression zero.
- Original response records, proof checks and reference proofs were preserved. The report contains reviewed summaries only; it makes no new model or Lean calls.
- An earlier 960-attempt smoke test used ten simpler questions. It is descriptive and is not pooled with the main comparison.
- Fresh source release dates do not prove that every underlying mathematical idea was absent from training. Physical checker hardware also varied; runtime is not a controlled model-speed comparison.
- Use each component’s source action for its exact rows, metric definition, transformation and input SHA-256 hashes. The statistical paper is a separate authored document, not an export of this page.`}/>
        </ReportDisclosure>
      </ReportSection>}
    </section>
  </article>;
}
