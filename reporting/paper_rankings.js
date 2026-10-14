/* Pure exploratory views of frozen proof measurements. No I/O or DATA mutation. */
(function (root) {
  'use strict';
  const cache = new WeakMap(), TOLERANCE = 1e-12;
  const METRICS = ['T0', 'I0', 'J0', 'W_B'];
  function require(ok, message) { if (!ok) throw new Error(message); }
  function gcd(a, b) { while (b) { const r = a % b; a = b; b = r; } return a; }
  function lcm(a, b) { return a / Number(gcd(BigInt(a), BigInt(b))) * b; }
  function logInteger(n) {
    const bits = n.toString(2).length, shift = Math.max(0, bits - 53);
    return Math.log2(Number(n >> BigInt(shift))) + shift;
  }
  function exact(n, d, degree = 1) {
    const g = gcd(n, d); n /= g; d /= g;
    return { n, d, degree, log2: (logInteger(n) - logInteger(d)) / degree };
  }
  function ratio(value) {
    if (!value || typeof value !== 'object') return null;
    const n = value.numerator_decimal, d = value.denominator_decimal;
    if (typeof n !== 'string' || typeof d !== 'string' || !/^\d+$/.test(n) || !/^\d+$/.test(d)) return null;
    const numerator = BigInt(n), denominator = BigInt(d);
    return numerator > 0n && denominator > 0n ? exact(numerator, denominator) : null;
  }
  function positiveInteger(value) { return Number.isSafeInteger(value) && value > 0 ? value : null; }
  function productMean(values) {
    if (!values.length || values.some(x => !x)) return null;
    // Each value may already be a sample geometric mean. LCM gives every
    // question equal weight, regardless of its number of usable samples.
    const common = values.reduce((a, x) => lcm(a, x.degree), 1);
    let n = 1n, d = 1n;
    for (const x of values) {
      const exponent = BigInt(common / x.degree);
      n *= x.n ** exponent; d *= x.d ** exponent;
      const g = gcd(n, d); n /= g; d /= g;
    }
    return exact(n, d, common * values.length);
  }
  function compareExact(a, b) {
    // Reduce exponents first: aggregate degrees share the question count.
    const common = gcd(BigInt(a.degree), BigInt(b.degree));
    const ae = BigInt(b.degree) / common, be = BigInt(a.degree) / common;
    const left = a.n ** ae * b.d ** be, right = b.n ** be * a.d ** ae;
    return left > right ? -1 : left < right ? 1 : 0; // Higher first.
  }
  function evidence(value) {
    return value ? { numerator_product_decimal: value.n.toString(),
      denominator_product_decimal: value.d.toString(), root_degree: String(value.degree) } : null;
  }
  function magnitude(value) {
    if (!value) return null;
    const result = 2 ** value.log2;
    return Number.isFinite(result) && result > 0 ? result : null;
  }
  function indexData(data) {
    require(data && typeof data === 'object', 'DATA must be an object');
    if (cache.has(data)) return cache.get(data);
    require(Array.isArray(data.models) && Array.isArray(data.tasks) && Array.isArray(data.conditions)
      && Array.isArray(data.attempts), 'DATA lacks the frozen model/task/attempt arrays');
    const models = data.models.map(m => m.id), tasks = [...data.tasks], conditions = [...data.conditions];
    require(new Set(models).size === models.length && new Set(tasks).size === tasks.length
      && new Set(conditions).size === conditions.length, 'Duplicate DATA identities');
    const modelSet = new Set(models), taskSet = new Set(tasks), conditionSet = new Set(conditions);
    const records = [], identities = new Set();
    for (const row of data.attempts) {
      require(modelSet.has(row.model) && taskSet.has(row.task) && conditionSet.has(row.condition)
        && Number.isInteger(row.sample) && row.sample >= 1 && row.sample <= 4, 'Attempt outside the frozen schedule');
      const identity = JSON.stringify([row.model, row.task, row.condition, row.sample]);
      require(!identities.has(identity), 'Duplicate scheduled model/question/condition/sample'); identities.add(identity);
      if (row.status !== 'success' || row.metricStatus !== 'available') continue;
      const m = row.metrics || {}, wb = positiveInteger(m.W_B);
      records.push({ model: row.model, task: row.task, condition: row.condition, sample: row.sample,
        attemptId: typeof row.attemptId === 'string' ? row.attemptId : null, proofTokens: wb,
        T0: ratio(m.T0), I0: ratio(m.I0), W_B: wb === null ? null : exact(BigInt(wb), 1n) });
    }
    const result = { models, tasks, conditions, records, modelSet, taskSet, conditionSet };
    cache.set(data, result); return result;
  }
  function optionsFor(index, supplied) {
    const o = { modelIds: index.models, condition: 'mathematical_compression', metric: 'T0',
      aggregation: 'rank', minCoverage: 0, minSamples: 1, sample: 'all', maxProofTokens: null,
      questionIds: null, beta: 0.5, ...supplied };
    require(Array.isArray(o.modelIds) && new Set(o.modelIds).size === o.modelIds.length
      && o.modelIds.every(id => index.modelSet.has(id)), 'Unknown or duplicate selected model');
    require(index.conditionSet.has(o.condition), 'Unknown prompt condition');
    require(METRICS.includes(o.metric), 'Unknown metric');
    require(['rank', 'magnitude'].includes(o.aggregation), 'Unknown aggregation');
    require(Number.isInteger(o.minCoverage) && o.minCoverage >= 0, 'minCoverage must be a nonnegative question count');
    require(Number.isInteger(o.minSamples) && o.minSamples >= 1 && o.minSamples <= 4, 'minSamples must be 1 through 4');
    require(o.sample === 'all' || Number.isInteger(o.sample) && o.sample >= 1 && o.sample <= 4, 'sample uses display indices 1 through 4');
    require(o.maxProofTokens === null || Number.isSafeInteger(o.maxProofTokens) && o.maxProofTokens >= 0,
      'maxProofTokens must be null or a nonnegative safe integer');
    require(o.questionIds === null || Array.isArray(o.questionIds) && new Set(o.questionIds).size === o.questionIds.length
      && o.questionIds.every(id => index.taskSet.has(id)), 'Unknown or duplicate question selection');
    require(typeof o.beta === 'number' && Number.isFinite(o.beta) && o.beta >= 0 && o.beta <= 1, 'beta must lie in [0,1]');
    return { ...o, modelIds: [...o.modelIds], questionIds: o.questionIds === null ? null : [...o.questionIds] };
  }
  function tieGroups(order, isTie, assign) {
    for (let start = 0; start < order.length;) {
      let end = start + 1;
      // Float J0 groups are anchored to the first score, never transitive
      // tolerance chaining and never a nontransitive sort comparator.
      while (end < order.length && isTie(order[start], order[end])) end++;
      for (let i = start; i < end; i++) assign(order[i], start, end);
      start = end;
    }
  }
  function compute(data, supplied = {}) {
    const index = indexData(data), options = optionsFor(index, supplied), selected = new Set(options.modelIds);
    const requested = options.questionIds === null ? index.taskSet : new Set(options.questionIds);
    const cells = new Map(options.modelIds.map(id => [id, new Map()]));
    let filteredMeasuredAttempts = 0, missingRequiredMetricAttempts = 0;
    for (const row of index.records) {
      if (!selected.has(row.model) || row.condition !== options.condition
        || options.sample !== 'all' && row.sample !== options.sample) continue;
      if (options.maxProofTokens !== null && (row.proofTokens === null || row.proofTokens > options.maxProofTokens)) continue;
      const usable = options.metric === 'J0' ? row.T0 && row.I0 : row[options.metric];
      if (!usable) { missingRequiredMetricAttempts++; continue; }
      filteredMeasuredAttempts++;
      const map = cells.get(row.model); if (!map.has(row.task)) map.set(row.task, []); map.get(row.task).push(row);
    }
    const coverage = {}, qualified = new Map();
    for (const model of options.modelIds) {
      const map = new Map();
      for (const [task, rows] of cells.get(model)) if (rows.length >= options.minSamples) {
        rows.sort((a, b) => a.sample - b.sample);
        map.set(task, { task, samples: rows.length, attemptIds: rows.map(r => r.attemptId),
          sampleIndices: rows.map(r => r.sample), T0: productMean(rows.map(r => r.T0)),
          I0: productMean(rows.map(r => r.I0)), W_B: productMean(rows.map(r => r.W_B)) });
      }
      coverage[model] = map.size; qualified.set(model, map);
    }
    const cohort = options.modelIds.filter(id => coverage[id] >= options.minCoverage);
    const excluded = options.modelIds.filter(id => !cohort.includes(id)).map(id => ({ id, coverage: coverage[id] }));
    const tasks = cohort.length ? index.tasks.filter(task => requested.has(task)
      && cohort.every(model => qualified.get(model).has(task))) : [];
    const blend = options.metric === 'J0', lower = options.metric === 'W_B';
    const metadata = { schemaVersion: 1, kind: 'exploratory_paper_metric_ranking', options,
      withinQuestion: 'geometric_mean_of_all_selected_usable_samples', sampleIndexBasis: 'one_based_display_indices',
      coveragePopulation: 'all_dataset_questions_after_sample_proof_length_and_required_metric_filters',
      questionPopulation: 'requested_questions_intersected_across_the_qualifying_model_cohort',
      metricDirection: lower ? 'lower_first' : 'higher_first',
      rankAggregation: 'equal_question_arithmetic_mean_of_midrank; exact_integer_doubled_rank_sums',
      magnitudeAggregation: blend ? 'equal_question_arithmetic_mean_of_J0' : 'equal_question_geometric_mean; exact_rational_product_roots',
      questionRankingMethod: blend ? 'floating_J0_anchor_ties' : 'exact_rational_product_roots',
      j0Normalization: 'per_question_geometric_T0_and_I0_divided_by_their_respective_cohort_maxima',
      j0Formula: 'beta * normalized_T0 + (1-beta) * normalized_I0',
      j0TieTolerance: blend ? TOLERANCE : null, j0TiePolicy: 'descending_numeric_sort; ties_within_absolute_tolerance_of_group_leader',
      missingMetricPolicy: 'exclude samples lacking a positive exact required metric; never replace with zero',
      numericValuePolicy: 'rounded floating values; null if unrepresentable; exact evidence and log2 retained',
      assumptions: ['I0 is proof length relative to statement length; higher is not concision.',
        'J0 is an exploratory maximum-normalized mix, not PageRank or a frozen primary endpoint.',
        'Changing filters or model cohort can change shared questions and J0 normalization.'],
      filteredMeasuredAttempts, missingRequiredMetricAttempts };
    if (cohort.length < 2 || !tasks.length) return { cohort, excluded, coverage, tasks, rows: [], metadata,
      reason: cohort.length < 2 ? 'fewer_than_two_qualifying_models' : 'no_shared_requested_questions' };
    const questions = new Map(cohort.map(id => [id, []]));
    for (const task of tasks) {
      const group = cohort.map(model => ({ model, cell: qualified.get(model).get(task) }));
      const maxT = blend ? Math.max(...group.map(x => x.cell.T0.log2)) : null;
      const maxI = blend ? Math.max(...group.map(x => x.cell.I0.log2)) : null;
      for (const row of group) {
        const x = row.cell;
        row.normalization = blend ? { beta: options.beta, T0_max_log2: maxT, I0_max_log2: maxI,
          normalized_T0: 2 ** (x.T0.log2 - maxT), normalized_I0: 2 ** (x.I0.log2 - maxI) } : null;
        row.value = blend ? options.beta * row.normalization.normalized_T0
          + (1 - options.beta) * row.normalization.normalized_I0 : magnitude(x[options.metric]);
      }
      const compare = (a, b) => blend ? b.value - a.value : (lower ? -1 : 1) * compareExact(a.cell[options.metric], b.cell[options.metric]);
      group.sort((a, b) => compare(a, b) || a.model.localeCompare(b.model));
      tieGroups(group, (a, b) => blend ? Math.abs(a.value - b.value) <= TOLERANCE : compare(a, b) === 0,
        (row, start, end) => { row.rankTwice = start + 1 + end; });
      for (const row of group) {
        const x = row.cell;
        questions.get(row.model).push({ task, rank: row.rankTwice / 2, rankTwice: row.rankTwice,
          value: row.value, metricLog2: blend ? row.value > 0 ? Math.log2(row.value) : null : x[options.metric].log2,
          t0Log2: x.T0 ? x.T0.log2 : null, i0Log2: x.I0 ? x.I0.log2 : null, wbLog2: x.W_B ? x.W_B.log2 : null,
          samples: x.samples, attemptIds: [...x.attemptIds], sampleIndices: [...x.sampleIndices],
          exact: { T0: evidence(x.T0), I0: evidence(x.I0), W_B: evidence(x.W_B) }, normalization: row.normalization });
      }
    }
    const internals = new Map();
    const rows = cohort.map(model => {
      const q = questions.get(model), rankSumTwice = q.reduce((sum, x) => sum + x.rankTwice, 0);
      const all = { T0: productMean(tasks.map(t => qualified.get(model).get(t).T0)),
        I0: productMean(tasks.map(t => qualified.get(model).get(t).I0)),
        W_B: productMean(tasks.map(t => qualified.get(model).get(t).W_B)) };
      const value = blend ? q.reduce((sum, x) => sum + x.value, 0) / q.length : magnitude(all[options.metric]);
      internals.set(model, all);
      const averageRank = rankSumTwice / (2 * tasks.length);
      return { model, averageRank, rankPoints: 100 * (cohort.length - averageRank) / (cohort.length - 1), place: null,
        metricLog2: blend ? value > 0 ? Math.log2(value) : null : all[options.metric].log2, value,
        t0Log2: all.T0 ? all.T0.log2 : null, i0Log2: all.I0 ? all.I0.log2 : null, wbLog2: all.W_B ? all.W_B.log2 : null,
        qualifyingSamples: q.reduce((sum, x) => sum + x.samples, 0), questions: q,
        exact: { T0: evidence(all.T0), I0: evidence(all.I0), W_B: evidence(all.W_B),
          rank_sum_twice: String(rankSumTwice), question_count: String(tasks.length) } };
    });
    const compareRows = (a, b) => options.aggregation === 'rank' ? Number(BigInt(a.exact.rank_sum_twice) - BigInt(b.exact.rank_sum_twice))
      : blend ? b.value - a.value : (lower ? -1 : 1) * compareExact(internals.get(a.model)[options.metric], internals.get(b.model)[options.metric]);
    rows.sort((a, b) => compareRows(a, b) || a.model.localeCompare(b.model));
    tieGroups(rows, (a, b) => options.aggregation === 'magnitude' && blend ? Math.abs(a.value - b.value) <= TOLERANCE : compareRows(a, b) === 0,
      (row, start) => { row.place = start + 1; });
    return { cohort, excluded, coverage, tasks, rows, metadata, reason: null };
  }
  const api = Object.freeze({ compute, version: 'paper_rankings_v1' });
  root.PaperRankings = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
