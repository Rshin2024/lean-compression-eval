"""Build a private comparison from reconciled, completed eight-model reports.

Only aggregate results, proof measurements and cost evidence enter the page.
Captured responses, proof bodies and private references are never embedded.
"""
import argparse
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import math
import os
from pathlib import Path

MODELS = ['gpt-6.1-sol', 'gpt-6-sol', 'gpt-6-luna', 'gpt-6-astra', 'claude-haiku-5-5',
          'claude-sonnet-5-5', 'claude-opus-5-5', 'claude-fable-5-1']
CONDS = ['standard', 'mathematical_compression', 'source_concision']
METRICS = ['W_S', 'W_B', 'U_S_decimal', 'U_B_decimal', 'T0', 'I0', 'D', 'log2_T0', 'log2_I0']
USAGE = ['input_tokens', 'output_tokens', 'reasoning_tokens', 'generation_elapsed_seconds',
         'validation_elapsed_seconds', 'extraction_elapsed_seconds']
COST_FIELDS = ['status', 'total_usd', 'currency', 'usage_hash', 'pricing_hash', 'pricing_as_of',
               'assumptions', 'reasons', 'input_total_tokens', 'long_context_applied']


def require(condition, message):
    if not condition:
        raise ValueError(message)


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
        ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def exact_geometric_t0(rows):
    """Retain the product/count representation; taking a floating root loses ties."""
    numerator, denominator = 1, 1
    if not rows:
        return None
    for row in rows:
        ratio = row['metrics'].get('T0') or {}
        num, den = ratio.get('numerator_decimal'), ratio.get('denominator_decimal')
        if num is None or den is None:
            return None  # Only legacy reports without exact ratios may use the log fallback.
        require(isinstance(num, str) and num.isascii() and num.isdecimal()
                and isinstance(den, str) and den.isascii() and den.isdecimal(), 'Malformed exact T0 ratio')
        num, den = int(num), int(den)
        require(num > 0 and den > 0, 'Compression ratios must be positive')
        numerator *= num
        denominator *= den
        factor = math.gcd(numerator, denominator)
        numerator //= factor
        denominator //= factor
    return {'numerator_product_decimal': str(numerator),
            'denominator_product_decimal': str(denominator), 'sample_count': len(rows)}


def write_question_viewer(manifests, output, task_ids, samples):
    """Export only the actual frozen message text, never task/reference metadata."""
    questions, seen_slots, run_hashes, limits = {}, set(), {}, set()
    for model, path, expected_run_hash in manifests:
        manifest = json.loads(path.read_text())
        require(manifest.get('run_hash') == expected_run_hash == canonical_hash(
            {key: value for key, value in manifest.items() if key != 'run_hash'}), 'Frozen question manifest hash mismatch')
        require(manifest.get('model_snapshot') == model, 'Frozen question model mismatch')
        run_hashes[model] = expected_run_hash
        config = manifest['config']
        limits.add((config['validation'].get('cpu_quota'), config['validation'].get('memory_mib'),
                    config['validation'].get('wall_seconds'), config['extraction'].get('wall_seconds')))
        base = manifest.get('sample_index_base', 0)
        for slot in manifest['slots']:
            task, condition = slot['task_id'], slot['condition']
            key = (task, condition)
            identity = (model, task, condition, slot['sample_index'])
            require(identity not in seen_slots and task in task_ids and condition in CONDS
                    and type(slot['sample_index']) is int and slot['sample_index'] in range(base, base + samples),
                    'Frozen question schedule differs from the comparison')
            seen_slots.add(identity)
            messages = slot['messages']
            require(isinstance(messages, list) and all(isinstance(m, dict) and set(m) == {'role', 'content'}
                and isinstance(m['role'], str) and isinstance(m['content'], str) for m in messages),
                'Frozen messages are not simple role/content text')
            require(canonical_hash(messages) == slot['prompt_hash'], 'Frozen prompt text differs from its hash')
            value = {'task_id': task, 'condition': condition, 'messages': messages, 'prompt_hash': slot['prompt_hash']}
            if key in questions:
                require(questions[key] == value, 'Prompts differ across models or samples; refusing to collapse them')
            questions[key] = value
    require(len(manifests) == 8 and len(seen_slots) == len(task_ids) * len(CONDS) * samples * 8,
            'Frozen question schedule is incomplete')
    require(len(questions) == len(task_ids) * len(CONDS), 'Frozen question/condition coverage is incomplete')
    payload = {'schema_version': 1, 'classification': 'observed_frozen_model_inputs',
               'task_ids': task_ids, 'conditions': CONDS, 'samples_per_model': samples,
               'source_run_hashes': run_hashes, 'questions': [questions[k] for k in sorted(questions)]}
    template = '''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Frozen evaluation questions</title><style>body{max-width:1150px;margin:2rem auto;padding:0 20px;font:16px system-ui;color:#182a39}select{font:inherit;padding:8px;margin:8px}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f3f6f8;padding:20px;border-radius:8px;font:14px/1.6 ui-monospace,monospace}small{overflow-wrap:anywhere}a{color:#2867b2}</style><h1>What the models were given</h1><p>Exact frozen message text, identical across the eight models and repeated samples. These are the supplied system and user messages; provider-internal instructions are not included. Private curator material and reference proofs are not exported.</p><p><a href="index.html#compression">Back to model comparison</a> · <a href="questions-data.json">Download exact frozen messages and hashes</a></p><label>Question <select id="question"></select></label><label>Prompt condition <select id="condition"></select></label><p><small id="hash"></small></p><div id="messages"></div><script type="application/json" id="data">__QUESTIONS__</script><script>const data=JSON.parse(document.getElementById('data').textContent),q=document.getElementById('question'),c=document.getElementById('condition');data.task_ids.forEach(v=>q.add(new Option(v,v)));data.conditions.forEach(v=>c.add(new Option(v.replaceAll('_',' '),v)));function show(){const row=data.questions.find(r=>r.task_id===q.value&&r.condition===c.value);document.getElementById('hash').textContent='Prompt SHA-256: '+row.prompt_hash;const box=document.getElementById('messages');box.replaceChildren();for(const m of row.messages){const h=document.createElement('h2'),p=document.createElement('pre');h.textContent=m.role;p.textContent=m.content;box.append(h,p)}}q.onchange=c.onchange=show;show();</script></html>'''
    output.mkdir(parents=True, exist_ok=True)
    (output / 'questions-data.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n')
    (output / 'questions.html').write_text(template.replace('__QUESTIONS__', json.dumps(payload, ensure_ascii=False).replace('<', '\\u003c')))
    return {'available': True, 'path': 'questions.html', 'dataPath': 'questions-data.json',
            'messageSets': len(questions), 'runtimeLimits': list(next(iter(limits))) if len(limits) == 1 else None}


def report_directory(suite, model):
    state_file = suite / (model + '.json')
    report = suite / ('report-' + model)
    if state_file.exists():
        state = json.loads(state_file.read_text())
        if state.get('report'):
            candidate = Path(state['report'])
            if not candidate.is_absolute():
                candidate = suite / candidate
            if candidate.exists():
                report = candidate
    require(report.resolve().parent == suite.resolve(), 'Model reports must be direct children of the suite')
    return report


def cost_map(report, analysis, model, expected_count):
    path = report / 'costs.json'
    source = json.loads(path.read_text()) if path.exists() else analysis.get('costs')
    if source is None:
        return {}, None
    require(isinstance(source, dict), 'Malformed cost report')
    rows = source.get('attempts', [])
    require(len(rows) == expected_count, 'Cost report must cover every scheduled attempt')
    costs = {}
    for row in rows:
        require(row.get('model_snapshot') == model, 'Cost row model mismatch')
        key = (row.get('task_id'), row.get('condition'), row.get('sample_index'))
        require(key not in costs, 'Duplicate cost schedule identity')
        value = row.get('cost') or {}
        require(value.get('status') in {'estimated', 'unavailable'}, 'Unknown cost status')
        if value['status'] == 'estimated':
            amount = value.get('total_usd')
            require(isinstance(amount, str), 'Cost must retain its exact decimal string')
            number = Decimal(amount)
            require(number.is_finite() and number >= 0, 'Invalid cost amount')
            require(row.get('generated') is True, 'Priced attempt has no generation record')
        else:
            require(value.get('total_usd') is None, 'Unavailable costs must not carry totals')
        costs[key] = {**{k: value.get(k) for k in COST_FIELDS}, 'generated': row.get('generated') is True}
        if value['status'] == 'estimated':
            costs[key]['total_usd'] = format(number, 'f')
    summary = source.get('summary') or {}
    require(summary.get('scheduled_attempts') == expected_count, 'Cost denominator mismatch')
    priced = [c for c in costs.values() if c['status'] == 'estimated']
    require(summary.get('priced_attempts') == len(priced), 'Priced count mismatch')
    observed = summary.get('observed_sum_usd')
    if priced:
        require(observed is not None and
                sum((Decimal(c['total_usd']) for c in priced), Decimal(0)) == Decimal(observed),
                'Cost subtotal does not reconcile')
    else:
        require(observed is None, 'Missing cost observations must remain unavailable')
    return costs, {
        'path': report.name + ('/costs.json' if path.exists() else '/analysis.json'),
        'sha256': hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None,
        'pricingHash': source.get('pricing_hash'), 'pricingAsOf': source.get('pricing_as_of'),
        'pricingBasis': source.get('pricing_basis', 'frozen_run_pricing' if source.get('pricing_hash') else 'no_frozen_pricing'),
        'summary': {k: summary.get(k) for k in ['status', 'scheduled_attempts', 'generated_attempts',
            'not_generated_attempts', 'priced_attempts', 'unavailable_generated_attempts',
            'total_usd', 'generated_total_usd', 'observed_sum_usd', 'complete']}}


def build(suite, *, output=None, classification='observed', runs_root=None, cloud_costs=None):
    suite = Path(suite).resolve()
    output = Path(output).resolve() if output is not None else suite
    require(classification in {'observed', 'synthetic_test_fixture'}, 'Unknown data classification')
    data = {'models': [], 'conditions': CONDS, 'attempts': [], 'sources': [],
            'built': datetime.now(timezone.utc).isoformat(), 'buildStatus': 'complete',
            'classification': classification}
    population, generation_settings, manifests = None, set(), []
    runs_root = Path(runs_root) if runs_root is not None else Path(__file__).resolve().parents[1] / 'runs'
    for model in MODELS:
        report = report_directory(suite, model)
        ap = report / 'analysis.json'
        raw = ap.read_bytes()
        analysis = json.loads(raw)
        require(model in analysis.get('models', {}), 'Expected model absent from analysis')
        m = analysis['models'][model]
        require(m.get('run_complete') is True, 'All model verification must finish before comparison')
        tasks = sorted(m['per_task'])
        n, k = analysis['population']['task_count'], analysis['samples_per_goal_condition']
        require(type(n) is int and n > 0 and type(k) is int and k > 0 and len(tasks) == n,
                'Invalid population or sampling')
        if population is None:
            population = (tasks, n, k)
        require(population == (tasks, n, k), 'Models must use identical questions and sampling')
        if n == 100:
            require(tasks == [f'OM100-{i:03d}' for i in range(1, 101)] and k == 4,
                    'OM100 must retain every selected question and four samples')
        manifest = runs_root / (('om100-' if n == 100 else 'smoke-') + model + '-high') / 'manifest.json'
        if manifest.is_file() and classification == 'observed':
            manifests.append((model, manifest, analysis.get('run_provenance', {}).get('run_hash')))
        per_condition, per_model = n * k, n * k * len(CONDS)
        conditions = {}
        for c in CONDS:
            v = m['conditions'][c]
            require(v['scheduled_attempts'] == per_condition, 'Condition denominator mismatch')
            conditions[c] = {key: v[key] for key in ['scheduled_attempts', 'valid_attempts',
                'measurable_valid_attempts', 'measurable_task_count', 'failure_statuses', 'attainment']}
            observed = v.get('resources', {}).get('measurements', {})
            conditions[c]['usage'] = {key: observed.get(key, {'sum': None, 'observed_count': 0,
                'missing_count': per_condition, 'invalid_count': 0}) for key in USAGE}
        contrasts = {name: {'T0': v['mean_log2_T0_difference'], 'WB': v['mean_log2_W_B_difference'],
                            'tasks': v['paired_population']['task_count']} for name, v in m['contrasts'].items()}
        costs, evidence = cost_map(report, analysis, model, per_model)
        data['models'].append({'id': model, 'conditions': conditions, 'perTask': m['per_task'],
            'contrasts': contrasts, 'report': report.name + '/report.html', 'costs': evidence})
        data['sources'].append({'model': model, 'path': report.name + '/analysis.json',
            'sha256': hashlib.sha256(raw).hexdigest(),
            'description': f'Completed frozen-run analysis; {per_model:,} attempts; {n} questions.'})
        source_rows = json.loads((report / 'all_scheduled_attempts.json').read_text())
        require(len(source_rows) == per_model, 'Attempt file must cover the full schedule')
        seen, model_rows = set(), []
        base = analysis.get('sample_index_base', 0)
        for row in source_rows:
            key = (row['task_id'], row['condition'], row['sample_index'])
            require(key not in seen, 'Duplicate scheduled attempt')
            seen.add(key)
            require(row.get('model_snapshot', model) == model, 'Attempt model mismatch')
            require(key[0] in tasks and key[1] in CONDS and type(key[2]) is int and key[2] in range(base, base + k),
                    'Unexpected attempt identity')
            metric, generation = row.get('metrics') or {}, row.get('generation') or {}
            settings = generation.get('generation_settings') or {}
            generation_settings.add((settings.get('reasoning_setting'), settings.get('max_output_tokens')))
            if costs:
                require(key in costs, 'Attempt missing from cost evidence')
            cost = costs.get(key, {'status': 'unavailable', 'total_usd': None,
                'generated': row.get('generation') is not None, 'reasons': ['cost_report_unavailable']})
            require(cost['generated'] == (row.get('generation') is not None),
                    'Cost and attempt generation coverage differ')
            model_rows.append({'model': model, 'task': key[0], 'condition': key[1], 'sample': key[2] - base + 1,
                'status': row['validity_status'], 'metricStatus': row.get('metric_status'),
                'metrics': {field: metric.get(field) for field in METRICS}, 'cost': cost})
        for c in CONDS:
            rows = [r for r in model_rows if r['condition'] == c]
            require(len(rows) == per_condition, 'Attempt condition coverage mismatch')
            require(sum(r['status'] == 'success' for r in rows) == conditions[c]['valid_attempts'],
                    'Validity does not reconcile')
            require(sum(r['status'] == 'success' and r['metricStatus'] == 'available' for r in rows) ==
                    conditions[c]['measurable_valid_attempts'], 'Metric coverage does not reconcile')
            for task in tasks:
                task_rows = [r for r in rows if r['task'] == task]
                measured = [r for r in task_rows if r['status'] == 'success' and r['metricStatus'] == 'available']
                stats = m['per_task'][task][c]
                require(stats['measurable_count'] == len(measured), 'Question metric coverage does not reconcile')
                stats['exact_geometric_T0'] = exact_geometric_t0(measured)
                require(n != 100 or not measured or stats['exact_geometric_T0'] is not None,
                        'OM100 measured attempts require exact compression ratios')
                for field in ['log2_T0', 'log2_W_B']:
                    values = [r['metrics'].get(field) for r in measured]
                    if field == 'log2_W_B':
                        values = [math.log2(r['metrics']['W_B']) for r in measured]
                    expected = sum(values) / len(values) if values else None
                    actual = stats['mean_' + field]
                    require(actual is None if expected is None else actual is not None and
                            math.isclose(actual, expected, rel_tol=1e-10, abs_tol=1e-10),
                            'Question ranking metric does not reconcile')
        data['attempts'].extend(model_rows)
    tasks, n, k = population
    require(len(data['attempts']) == n * k * len(CONDS) * len(MODELS), 'Cohort schedule mismatch')
    data.update(taskCount=n, samplesPerQuestion=k, attemptsPerCondition=n * k,
        attemptsPerModel=n * k * len(CONDS), totalAttempts=len(data['attempts']), tasks=tasks,
        cohortKind='om100' if n == 100 else 'smoke', rankCoverageDefault=0 if n == 100 else min(5, n),
        magnitudePopulationDefault='shared' if n == 100 else 'available')
    effort, cap = next(iter(generation_settings)) if len(generation_settings) == 1 else (None, None)
    data['generation'] = {'reasoningEffort': effort, 'outputTokenCap': cap}
    data['questionViewer'] = (write_question_viewer(manifests, output, tasks, k) if len(manifests) == 8 else
        {'available': False, 'reason': 'All eight matching frozen manifests were not supplied, or this is synthetic QA.'})
    cloud_path = Path(cloud_costs) if cloud_costs is not None else suite / 'cloud-costs.json'
    data['cloudCosts'] = None
    if cloud_path.is_file():
        cloud_raw = cloud_path.read_bytes()
        cloud = json.loads(cloud_raw)
        data['cloudCosts'] = {'asOf': cloud.get('as_of_utc'), 'currency': cloud.get('currency'),
            'status': cloud.get('status'), 'computeEstimatedUsd': (cloud.get('compute') or {}).get('estimated_usd'),
            'computeComplete': (cloud.get('compute') or {}).get('complete_allocation_estimate'),
            'computeBasis': (cloud.get('compute') or {}).get('basis'),
            'unknownAllocationIntervals': (cloud.get('compute') or {}).get('unknown_allocation_intervals', []),
            'totalUsd': cloud.get('total_cloud_cost_usd'), 'unknownItems': cloud.get('unknown_cost_items', []),
            'diskRate': {k: (cloud.get('managed_disk') or {}).get(k) for k in ['retail_rate_usd', 'unit', 'cost_usd', 'note']},
            'publicIpRate': {k: (cloud.get('public_ip') or {}).get(k) for k in ['retail_rate_usd', 'unit', 'cost_usd', 'note']},
            'sourceSha256': hashlib.sha256(cloud_raw).hexdigest()}
    if output != suite:
        prefix = Path(os.path.relpath(suite, output)).as_posix()
        for model in data['models']:
            model['report'] = prefix + '/' + model['report']
            if model['costs']:
                model['costs']['path'] = prefix + '/' + model['costs']['path']
        for source in data['sources']:
            source['path'] = prefix + '/' + source['path']
    template = Path(__file__).with_name('comparison.html').read_text()
    require(template.count('__REVIEWED_DATA__') == 1, 'Template marker missing or duplicated')
    payload = json.dumps(data, ensure_ascii=False, allow_nan=False).replace('<', '\\u003c')
    output.mkdir(parents=True, exist_ok=True)
    (output / 'comparison-data.json').write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
    (output / 'index.html').write_text(template.replace('__REVIEWED_DATA__', payload))
    return data


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--suite', type=Path, default=Path(__file__).resolve().parents[1] / 'runs/smoke-suite-high-20261012')
    parser.add_argument('--output', type=Path, help='Separate QA output; source reports remain read-only')
    parser.add_argument('--classification', choices=['observed', 'synthetic_test_fixture'], default='observed')
    parser.add_argument('--runs-root', type=Path, help='Frozen run manifests used for the exact question viewer')
    parser.add_argument('--cloud-costs', type=Path, help='Separate shared cloud cost ledger, never attributed to models')
    args = parser.parse_args()
    d = build(args.suite, output=args.output, classification=args.classification,
              runs_root=args.runs_root, cloud_costs=args.cloud_costs)
    print(json.dumps({'output': str((args.output or args.suite) / 'index.html'), 'models': len(d['models']),
                      'questions': d['taskCount'], 'attempts': len(d['attempts']), 'classification': d['classification']}))
