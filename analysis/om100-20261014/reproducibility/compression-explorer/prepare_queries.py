#!/usr/bin/env python3
"""Portable exact explorer projection; reads the published package and writes one NEW directory. No model/Lean/network calls. Mathematical projection is preserved from the historical builder."""
from __future__ import annotations
from collections import Counter,defaultdict
from decimal import Decimal,localcontext
from fractions import Fraction
from functools import cmp_to_key
from pathlib import Path
from datetime import datetime,timezone
import argparse,hashlib,json,math,re

HERE=Path(__file__).resolve().parent
BASE=HERE.parents[1]
INPUT_RELATIVE=Path('inputs/om100-max-suite-high-20261014/comparison-data.json')
DATA=BASE/INPUT_RELATIVE
MAIN=BASE/'tables/main'
DATA_SHA='4ddf80101317bb6158bd13a435eccae899684479dfd74b91c2047c81c550f264'
METRIC_FIELDS=['U_S_decimal','U_B_decimal','t0Numerator','t0Denominator','log2T0','t0Scientific','W_B','W_S','i0Numerator','i0Denominator','log2I0','i0Scientific']
CELL_FIELDS=['t0NumeratorProduct','t0DenominatorProduct','meanLog2T0','geomeanT0Scientific','wbProduct','meanLog2WB','geomeanWB','i0NumeratorProduct','i0DenominatorProduct','meanLog2I0','geomeanI0Scientific','minT0Numerator','minT0Denominator','minT0Scientific','minT0Sample','minT0AttemptId','maxT0Numerator','maxT0Denominator','maxT0Scientific','maxT0Sample','maxT0AttemptId','minWB','maxWB','minI0Scientific','maxI0Scientific']

def require(ok,message):
    if not ok:raise ValueError(message)
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def load(path,inputs):
    raw=path.read_bytes();inputs[str(path.relative_to(BASE))]={'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw)};return json.loads(raw)
def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()
def fraction(numerator,denominator):
    require(isinstance(numerator,str) and re.fullmatch('[1-9][0-9]*',numerator) and isinstance(denominator,str) and re.fullmatch('[1-9][0-9]*',denominator),'Invalid positive exact ratio')
    return Fraction(int(numerator),int(denominator))
def scientific(value:Fraction,root=1):
    require(value>0 and type(root) is int and root>0,'Scientific display requires a positive measured value')
    with localcontext() as context:
        context.prec=36
        number=Decimal(value.numerator)/Decimal(value.denominator)
        if root==2:number=number.sqrt()
        elif root==4:number=number.sqrt().sqrt()
        elif root!=1:number=(number.ln()/root).exp()
        return format(number,'.5E').replace('E','e')
def decimal_root(value:Fraction,root:int):
    with localcontext() as context:
        context.prec=36
        number=Decimal(value.numerator)/Decimal(value.denominator)
        if root==2:return number.sqrt()
        if root==4:return number.sqrt().sqrt()
        return number if root==1 else (number.ln()/root).exp()

def project_attempt(row):
    valid=row['status']=='success';available=row['metricStatus']=='available'
    require(not available or valid,'Unaccepted proof has available measurements')
    measurable=valid and available
    out={k:row[k] for k in ['attemptId','model','task','condition','sample','status','metricStatus']}
    out.update(valid=valid,measurable=measurable,**{k:None for k in METRIC_FIELDS},sourceNonWhitespaceCharacters=(row.get('sourceLength') or {}).get('sourceNonWhitespaceCharacters'))
    chars=out['sourceNonWhitespaceCharacters'];require(chars is None or type(chars) is int and chars>=0,'Invalid source character count')
    if not measurable:return out
    m=row['metrics'];wb,ws=m['W_B'],m['W_S']
    require(type(wb) is int and type(ws) is int and 0<wb<2**53 and 0<ws<2**53,'Unsafe/nonpositive source-token counts')
    t=fraction(m['T0']['numerator_decimal'],m['T0']['denominator_decimal']);i=fraction(m['I0']['numerator_decimal'],m['I0']['denominator_decimal'])
    require(t==Fraction(int(m['U_S_decimal'])+int(m['U_B_decimal']),ws+wb),'T0 formula mismatch')
    require(i==Fraction(wb,ws),'I0 formula mismatch')
    for name,value in [('T0',t),('I0',i)]:
        logged=m['log2_'+name];require(isinstance(logged,(int,float)) and math.isfinite(logged) and abs(logged-(math.log2(value.numerator)-math.log2(value.denominator)))<=1e-10,'Metric log differs from exact fraction')
    out.update(U_S_decimal=m['U_S_decimal'],U_B_decimal=m['U_B_decimal'],t0Numerator=m['T0']['numerator_decimal'],t0Denominator=m['T0']['denominator_decimal'],log2T0=m['log2_T0'],t0Scientific=scientific(t),
               W_B=wb,W_S=ws,i0Numerator=m['I0']['numerator_decimal'],i0Denominator=m['I0']['denominator_decimal'],log2I0=m['log2_I0'],i0Scientific=scientific(i))
    return out

def make_cell(attempts):
    require(len(attempts)==4 and {r['sample'] for r in attempts}=={1,2,3,4},'Cell requires all four scheduled samples')
    identities={(r['model'],r['task'],r['condition']) for r in attempts};require(len(identities)==1,'Mixed cell identities')
    model,task,condition=next(iter(identities));selected=sorted((r for r in attempts if r['measurable']),key=lambda r:r['sample']);n=len(selected)
    cell={'model':model,'task':task,'condition':condition,'scheduledCount':4,'validCount':sum(r['valid'] for r in attempts),'measurableCount':n,'rootCount':n,
          'sampleIndices':[r['sample'] for r in selected],'attemptIds':[r['attemptId'] for r in selected],**{key:None for key in CELL_FIELDS}}
    if not n:return cell
    t0=Fraction(1);i0=Fraction(1);wb=1
    for row in selected:
        t0*=fraction(row['t0Numerator'],row['t0Denominator']);i0*=fraction(row['i0Numerator'],row['i0Denominator']);wb*=row['W_B']
    cell.update(t0NumeratorProduct=str(t0.numerator),t0DenominatorProduct=str(t0.denominator),meanLog2T0=math.fsum(r['log2T0'] for r in selected)/n,geomeanT0Scientific=scientific(t0,n),
                wbProduct=str(wb),meanLog2WB=math.fsum(math.log2(r['W_B']) for r in selected)/n,geomeanWB=float(decimal_root(Fraction(wb),n)),
                i0NumeratorProduct=str(i0.numerator),i0DenominatorProduct=str(i0.denominator),meanLog2I0=math.fsum(r['log2I0'] for r in selected)/n,geomeanI0Scientific=scientific(i0,n),
                minWB=min(r['W_B'] for r in selected),maxWB=max(r['W_B'] for r in selected))
    ordered=sorted(selected,key=lambda r:(fraction(r['t0Numerator'],r['t0Denominator']),r['sample']))
    minimum=ordered[0];maximum=min((r for r in selected if fraction(r['t0Numerator'],r['t0Denominator'])==fraction(ordered[-1]['t0Numerator'],ordered[-1]['t0Denominator'])),key=lambda r:r['sample'])
    for prefix,row in [('min',minimum),('max',maximum)]:
        cell.update({prefix+'T0Numerator':row['t0Numerator'],prefix+'T0Denominator':row['t0Denominator'],prefix+'T0Scientific':row['t0Scientific'],prefix+'T0Sample':row['sample'],prefix+'T0AttemptId':row['attemptId']})
    i_values=[fraction(r['i0Numerator'],r['i0Denominator']) for r in selected]
    cell.update(minI0Scientific=scientific(min(i_values)),maxI0Scientific=scientific(max(i_values)))
    return cell

def exact_product(cell,metric):
    if not cell['rootCount']:return None
    if metric=='W_B':return Fraction(int(cell['wbProduct']))
    prefix='t0' if metric=='T0' else 'i0'
    return fraction(cell[prefix+'NumeratorProduct'],cell[prefix+'DenominatorProduct'])
def compare_cells(a,b,metric='T0'):
    av,bv=exact_product(a,metric),exact_product(b,metric)
    require(av is not None and bv is not None,'Missing metrics cannot be ranked')
    left,right=av**b['rootCount'],bv**a['rootCount']
    sign=(left>right)-(left<right)
    return sign if metric=='W_B' else -sign

def ranking(cells,models,tasks,condition,gate,metric):
    lookup={(c['model'],c['task']):c for c in cells if c['condition']==condition}
    coverage={m:sum(lookup[m,t]['rootCount']>0 for t in tasks) for m in models};cohort=[m for m in models if coverage[m]>=gate]
    shared=[t for t in tasks if cohort and all(lookup[m,t]['rootCount']>0 for m in cohort)]
    result={'condition':condition,'coverageGate':gate,'metric':metric,'models':cohort,'sharedQuestionIds':shared,'coverage':coverage,'rows':[],'questionRanks':[]}
    if len(cohort)<2 or not shared:return result
    sums={m:Fraction(0) for m in cohort}
    for task in shared:
        cmp=lambda a,b:compare_cells(lookup[a,task],lookup[b,task],metric)
        order=sorted(cohort,key=cmp_to_key(cmp));start=0
        while start<len(order):
            stop=start+1
            while stop<len(order) and cmp(order[start],order[stop])==0:stop+=1
            rank=Fraction(start+1+stop,2);points=(len(cohort)-rank)*100/(len(cohort)-1)
            for model in order[start:stop]:
                sums[model]+=rank;result['questionRanks'].append({'model':model,'task':task,'midrank':float(rank),'rankPoints':float(points)})
            start=stop
    for model in sorted(cohort,key=lambda m:(sums[m],models.index(m))):
        avg=sums[model]/len(shared);pts=(len(cohort)-avg)*100/(len(cohort)-1)
        result['rows'].append({'model':model,'meanRank':float(avg),'rankPoints':float(pts),'rankSumTwice':str(int(sums[model]*2)),'questionCount':len(shared),'exactMeanRank':str(avg),'exactRankPoints':str(pts)})
    return result

def source(label,paths,inputs,definition,methods):
    files=[str(p.relative_to(BASE)) for p in paths]
    return {'source':{'label':label,'files':files,'sourceHashes':{p:inputs[p]['sha256'] for p in files},'evidenceFlow':[{'title':'Read-only frozen source','detail':'; '.join(p+': SHA-256 '+inputs[p]['sha256'] for p in files)},{'title':'Projection and arithmetic','detail':methods}],
        'metricDefinitions':[{'label':label,'definition':definition,'componentIds':['compression-explorer-controls', 'compression-explorer-intro', 'compression-methods', 'compression-model-coverage', 'compression-model-ranking', 'compression-output-detail', 'compression-output-table', 'compression-podium-1', 'compression-podium-2', 'compression-podium-3', 'compression-question-ranking'],'sourceLineage':[{'files':files}]}]},'methods':[{'language':'text','code':methods}]}

def validate_output_directory(package_root, output):
    """Only a new output directory; never overwrite published evidence or app data."""
    package_root=Path(package_root).resolve()
    raw_output=Path(output)
    require(not raw_output.is_symlink(), 'Output directory cannot be a symlink')
    output=raw_output.resolve()
    require(not output.exists(), 'Output directory already exists; choose a new directory')
    require(output.parent.is_dir(), 'Output parent must already exist')
    for name in ('inputs','tables','analysis-report','reproducibility'):
        require(not output.is_relative_to(package_root/name), 'Output cannot be inside a protected package directory')
    require(not package_root.is_relative_to(output), 'Output cannot contain the input package')
    return output


def main(package_root=None, output=None, input_relative=INPUT_RELATIVE):
    global BASE,DATA,MAIN
    require(output is not None, 'An explicit new output directory is required')
    BASE=Path(package_root).resolve() if package_root is not None else HERE.parents[1]
    relative=Path(input_relative)
    require(not relative.is_absolute() and '..' not in relative.parts, 'Input must be relative to package root')
    DATA=(BASE/relative).resolve();MAIN=BASE/'tables/main'
    require(DATA.is_relative_to(BASE), 'Input escapes package root')
    OUTPUT=validate_output_directory(BASE,output)
    require(DATA.is_file() and MAIN.is_dir(), 'Package inputs or tables/main are missing')
    OUTPUT.mkdir(exist_ok=False)
    inputs={};data=load(DATA,inputs);require(inputs[str(DATA.relative_to(BASE))]['sha256']==DATA_SHA,'Provider-maximum source changed')
    meta=load(MAIN/'task_metadata.json',inputs);prior=load(MAIN/'conditional_ranks.json',inputs);prior_questions=load(MAIN/'conditional_ranks_by_question.json',inputs);prior_coverage=load(MAIN/'coverage.json',inputs);manifest=load(MAIN/'analysis_manifest.json',inputs)
    require(manifest['status']=='complete' and data['buildStatus']=='complete' and data['classification']=='observed','Sources incomplete')
    models=[m['id'] for m in data['models']];tasks=data['tasks'];conditions=data['conditions']
    require(len(models)==len(set(models))==8 and tasks==[f'OM100-{i:03d}' for i in range(1,101)] and len(set(conditions))==3,'Wrong cohort shape')
    expected={(m,t,c,s) for m in models for t in tasks for c in conditions for s in range(1,5)}
    raw={ (r['model'],r['task'],r['condition'],r['sample']):r for r in data['attempts'] }
    require(len(data['attempts'])==len(raw)==9600 and set(raw)==expected and len({r['attemptId'] for r in raw.values()})==9600,'Duplicate or unmatched schedule')
    attempts=[project_attempt(raw[key]) for key in sorted(expected,key=lambda k:(conditions.index(k[2]),k[1],models.index(k[0]),k[3]))]
    groups=defaultdict(list)
    for row in attempts:groups[row['model'],row['task'],row['condition']].append(row)
    cells=[make_cell(groups[m,t,c]) for c in conditions for t in tasks for m in models]
    require(len(cells)==2400 and sum(c['measurableCount'] for c in cells)==sum(r['measurable'] for r in attempts)==3831,'Measurable counts differ')
    labels={'gpt-6.1-sol':'GPT-6.1 Sol','gpt-6-sol':'GPT-6 Sol','gpt-6-luna':'GPT-6 Luna','gpt-6-astra':'GPT-6 Astra','claude-haiku-5-5':'Claude Haiku 5.5','claude-sonnet-5-5':'Claude Sonnet 5.5','claude-opus-5-5':'Claude Opus 5.5','claude-fable-5-1':'Claude Fable 5.1'}
    coverage=[]
    for c in conditions:
        for m in models:
            selected=[x for x in cells if x['model']==m and x['condition']==c]
            coverage.append({'model':m,'modelLabel':labels[m],'condition':c,'questionCount':100,'scheduledCount':400,'validCount':sum(x['validCount'] for x in selected),'measurableCount':sum(x['measurableCount'] for x in selected),
                             'validQuestionCount':sum(x['validCount']>0 for x in selected),'measurableQuestionCount':sum(x['rootCount']>0 for x in selected)})
    metadata={r['id']:r for r in meta};require(len(meta)==len(metadata)==100 and set(metadata)==set(tasks),'Question metadata does not cover all100 questions')
    questions=[{'task':t,'title':metadata[t]['title'],'family':metadata[t]['family'],'familyTitle':metadata[t]['family_title'],'selectionGroup':metadata[t]['selection_group'],'subject':metadata[t]['subject'],'sourcePath':metadata[t]['source_path'],'declarationName':metadata[t]['declaration_name']} for t in tasks]
    expected_means={(r['condition'],r['coverage_gate'],r['metric'],r['model']):r for r in prior}
    expected_ranks={(r['condition'],r['coverage_gate'],r['metric'],r['task'],r['model']):r['rank'] for r in prior_questions}
    observed_means={};observed_ranks={};oracles=[]
    for c in conditions:
        for gate in [0,25,50,75]:
            for metric in ['T0','W_B','I0']:
                oracle=ranking(cells,models,tasks,c,gate,metric);oracles.append(oracle)
                check=next(x for x in prior_coverage if x['condition']==c and x['coverage_gate']==gate)
                require(check['models']==oracle['models'] and check['tasks']==oracle['sharedQuestionIds'],'Prior shared-question coverage differs')
                for r in oracle['rows']:
                    k=(c,gate,metric,r['model']);observed_means[k]=r
                    require(k in expected_means and abs(r['meanRank']-expected_means[k]['mean'])<1e-12 and r['questionCount']==expected_means[k]['n_questions'],'Prior aggregate ranks differ')
                for r in oracle['questionRanks']:observed_ranks[c,gate,metric,r['task'],r['model']]=r['midrank']
    require(observed_ranks==expected_ranks and set(observed_means)==set(expected_means),'Prior rank coverage or exact midranks differ')
    default=next(x for x in oracles if x['condition']=='mathematical_compression' and x['coverageGate']==25 and x['metric']=='T0')
    all8=next(x for x in oracles if x['condition']=='mathematical_compression' and x['coverageGate']==0 and x['metric']=='T0')
    require(len(default['models'])==6 and len(default['sharedQuestionIds'])==23 and not all8['rows'] and not all8['sharedQuestionIds'],'Expected default/all-eight availability changed')
    definitions={
      'T0':'(U_S + U_B)/(W_S + W_B); exact dimensionless expanded-size/source-size ratio, available only for accepted measured proofs. Higher T0 means greater measured compression, not universally better mathematics.',
      'I0':'W_B/W_S, exact proof-source size relative to statement-source size. Larger is not an unconditional quality improvement.',
      'W_B':'Canonical proof-source token count from the frozen Lean metric; distinct from API tokens and non-whitespace characters.',
      'scientificDisplays':'Rounded to six significant digits using Decimal arithmetic; logarithms/displays are for presentation only and must never determine ties.',
      'geometricMeans':'Product of successful available measurements raised to 1/rootCount; exact reduced fraction products plus rootCount retain full comparison evidence. No failed/unavailable sample receives zero.',
      'exactComparison':'Compare numA^nB * denB^nA with numB^nA * denA^nB. This compares geometric means with unequal successful sample counts without floating-point rounding.',
      'rankPoints':'For M>=2 models on a shared question: 100*(M-midrank)/(M-1), exact ties share the midrank. Average equally across the same shared questions and same selected roster. This is relative rank points, not percentage compression, success probability, or an absolute quality scale.',
      'missingness':'All9600 scheduled attempts remain present. Unavailable metric fields are null; zero counts denote observed lack of valid/measurable outputs. Fewer than2 models or0 shared questions means no aggregate rank.',
      'coverage':'Number of questions with >=1 accepted measurable proof, over100 scheduled questions per model/prompt. The default threshold25 is an explicit display-cohort selection, not a new performance threshold.'}
    queries={
      'compression_attempts':{'rows':attempts,**source('Per-output exact compression measurements',[DATA],inputs,definitions['T0']+' '+definitions['I0']+' '+definitions['W_B']+' '+definitions['missingness'],'Project all9600 scheduled rows; check T0 and I0 against exact integer formulas; retain one-based sample identity, status and exact ratio strings; no response/reference body text.')},
      'compression_cells':{'rows':cells,**source('Per-question/model/prompt geometric-mean measurements',[DATA],inputs,definitions['geometricMeans']+' '+definitions['exactComparison']+' '+definitions['rankPoints'],'Group exactly4 scheduled attempts by model/question/prompt. Use only accepted available metrics; multiply Fraction values exactly; retain rootCount, arithmetic mean log2 for display, ranges and measured attempt IDs.')},
      'compression_coverage':{'rows':coverage,**source('Measurable-question coverage',[DATA],inputs,definitions['coverage'],'Count complete100-question schedules separately from questions with >=1 measurable proof. Do not remove failed slots or impute unavailable measurements.')},
      'compression_questions':{'rows':questions,**source('Selected-question titles and provenance',[MAIN/'task_metadata.json'],inputs,'All100 selected question IDs/titles, family and source declaration metadata. No proof bodies or curator answer summaries.','Allowlisted metadata projection, validated one-to-one on all100 scheduled question IDs.')},
    }
    addon={'schemaVersion':1,'kind':'compression_explorer_queries_v1','createdAtUTC':datetime.now(timezone.utc).isoformat(),'classification':'observed','inputHashes':inputs,'modelIds':models,'modelLabels':labels,'conditionIds':conditions,'definitions':definitions,
      'defaults':{'condition':'mathematical_compression','coverageGate':25,'modelIds':default['models'],'sharedQuestionIds':default['sharedQuestionIds'],'defaultQuestion':'OM100-007','allEightSharedQuestions':0},'queries':queries}
    for path,record in inputs.items():require(sha(BASE/path)==record['sha256'],'Input changed during projection')
    output=OUTPUT/'queries-addon.json';raw_output=canonical(addon)+b'\n'
    previous_sha=sha(output) if output.exists() else None
    staged=OUTPUT/'queries-addon.json.tmp'
    with staged.open('xb') as f:f.write(raw_output)
    staged.replace(output)
    (OUTPUT/'rank-reconciliation.json').write_text(json.dumps({'cases':oracles,'sourceInputHashes':inputs},indent=2,ensure_ascii=False,allow_nan=False)+'\n')
    receipt={'status':'validated','createdAtUTC':datetime.now(timezone.utc).isoformat(),'scriptSha256':sha(Path(__file__)),'addonSha256':sha(output),'supersedesAddonSha256':previous_sha,'addonBytes':len(raw_output),'attemptRows':len(attempts),'measurableAttempts':3831,'unavailableMetricAttempts':sum(not r['measurable'] for r in attempts),'cellRows':len(cells),'coverageRows':len(coverage),'questionRows':len(questions),'rankCasesReconciled':len(oracles),'aggregateRankRowsReconciled':len(observed_means),'questionRankRowsReconciled':len(observed_ranks),'defaultModels':default['models'],'defaultSharedQuestionCount':23,'allEightCompressionSharedQuestionCount':0,'maxT0ProductNumeratorDigits':max(len(r['t0NumeratorProduct'] or '') for r in cells),'inputHashes':inputs,'modelCalls':0,'leanCalls':0,'apiCalls':0,'inputWrites':0,'rawResponseBodiesExported':False,'referenceProofBodiesExported':False}
    (OUTPUT/'validation.json').write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps(receipt,indent=2))

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package-root',type=Path,default=HERE.parents[1],help='Published analysis/om100-20261014 package root')
    parser.add_argument('--input',type=Path,default=INPUT_RELATIVE,help='Relative input path within package root')
    parser.add_argument('--output',type=Path,required=True,help='New output directory; must not exist or be inside protected inputs/app/tables/source directories')
    args=parser.parse_args()
    main(package_root=args.package_root,output=args.output,input_relative=args.input)

