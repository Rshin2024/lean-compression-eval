/** Exact, pure ranking helpers for the reviewed compression explorer data.
 * No provider calls, Node dependencies, input mutations, or floating-point ties.
 * Display logarithms and magnitudes are approximate; every ordering is exact.
 */
export const MODEL_ROSTER = Object.freeze([
  'gpt-6.1-sol', 'gpt-6-sol', 'gpt-6-luna', 'gpt-6-astra',
  'claude-haiku-5-5', 'claude-sonnet-5-5', 'claude-opus-5-5', 'claude-fable-5-1',
]);
export const VERSION = 'compression_explorer_exact_ranks_v1';
const require = (ok, message) => { if (!ok) throw new Error(message); };
const stringId = x => typeof x === 'string' && x.length > 0;
const key = (...xs) => JSON.stringify(xs);
const gcd = (a, b) => { while (b) [a, b] = [b, a % b]; return a; };
const metricName = name => {
  require(name === 'T0' || name === 'WB' || name === 'W_B', 'Metric must be T0 or WB');
  return name === 'W_B' ? 'WB' : name;
};
function positiveInteger(value, field) {
  require((typeof value === 'string' && /^[0-9]+$/.test(value)) ||
    (typeof value === 'number' && Number.isSafeInteger(value)), `${field} must be an exact integer`);
  const n = BigInt(value);
  require(n > 0n, `${field} must be positive`);
  return n;
}
function fraction(n, d, rootCount = 1) {
  require(n > 0n && d > 0n && Number.isSafeInteger(rootCount) && rootCount > 0,
    'Invalid positive rational root');
  const g = gcd(n, d);
  return { n: n / g, d: d / g, rootCount };
}
function parseExact(value) {
  require(value && typeof value === 'object', 'Exact value is required');
  return fraction(positiveInteger(value.numeratorProduct, 'numeratorProduct'),
    positiveInteger(value.denominatorProduct, 'denominatorProduct'), value.rootCount);
}
function exactEvidence(x) {
  return x ? { numeratorProduct: x.n.toString(), denominatorProduct: x.d.toString(),
    rootCount: x.rootCount } : null;
}
function compare(a, b) {
  const common = gcd(BigInt(a.rootCount), BigInt(b.rootCount));
  const ae = BigInt(b.rootCount) / common, be = BigInt(a.rootCount) / common;
  const left = a.n ** ae * b.d ** be;
  const right = b.n ** be * a.d ** ae;
  return left < right ? -1 : left > right ? 1 : 0;
}
/** Conventional ascending comparison: -1 if a<b, +1 if a>b, 0 only for exact ties. */
export function compareGeometricFractions(a, b) { return compare(parseExact(a), parseExact(b)); }
function log2Integer(n) {
  const shift = Math.max(0, n.toString(2).length - 53);
  return Math.log2(Number(n >> BigInt(shift))) + shift;
}
function log2Value(x) { return (log2Integer(x.n) - log2Integer(x.d)) / x.rootCount; }
function scientific(log2) {
  if (log2 === null) return null;
  const log10 = log2 * Math.LOG10E * Math.LN2;
  let exponent = Math.floor(log10);
  let mantissa = Number((10 ** (log10 - exponent)).toPrecision(6));
  if (mantissa >= 10) { mantissa /= 10; exponent++; }
  return `${mantissa}e${exponent >= 0 ? '+' : ''}${exponent}`;
}
function productMean(values) {
  if (!values.length) return null;
  // Equal question weights even when their cells have different sample counts.
  let degree = 1n;
  for (const x of values) degree = degree / gcd(degree, BigInt(x.rootCount)) * BigInt(x.rootCount);
  let n = 1n, d = 1n;
  for (const x of values) {
    const power = degree / BigInt(x.rootCount);
    n *= x.n ** power; d *= x.d ** power;
    const g = gcd(n, d); n /= g; d /= g;
  }
  const rootCount = Number(degree) * values.length;
  require(Number.isSafeInteger(rootCount), 'Aggregate root degree exceeds exact range');
  return fraction(n, d, rootCount);
}
/** Geometric mean of positive rational roots, with equal weight per supplied value. */
export function aggregateGeometricMean(values) {
  return exactEvidence(productMean(values.map(parseExact)));
}
function display(t0, wb) {
  const meanLog2T0 = t0 ? log2Value(t0) : null, meanLog2WB = wb ? log2Value(wb) : null;
  const numericWB = wb ? 2 ** meanLog2WB : null;
  return { meanLog2T0, meanLog2WB, geomeanT0Scientific: scientific(meanLog2T0),
    geomeanWB: numericWB !== null && Number.isFinite(numericWB) && numericWB > 0 ? numericWB : null,
    geomeanWBScientific: scientific(meanLog2WB),
    t0Exact: exactEvidence(t0), wbExact: exactEvidence(wb) };
}
function rationalString(n, d) {
  const g = gcd(n < 0n ? -n : n, d); n /= g; d /= g;
  return d === 1n ? n.toString() : `${n}/${d}`;
}
function cellValues(row) {
  require(Number.isInteger(row.rootCount) && row.rootCount >= 0 && row.rootCount <= 4,
    'Cell rootCount must be 0 through 4');
  require(row.measurableCount === row.rootCount, 'Cell rootCount must equal measurableCount');
  require(Number.isInteger(row.validCount) && row.validCount >= row.measurableCount && row.validCount <= 4,
    'Invalid cell validCount');
  require(row.scheduledCount === 4, 'Cells must retain four scheduled responses');
  if (!row.rootCount) {
    require(row.t0NumeratorProduct === null && row.t0DenominatorProduct === null && row.wbProduct === null,
      'Unmeasured cell values must remain null');
    return null;
  }
  return {
    T0: fraction(positiveInteger(row.t0NumeratorProduct, 't0NumeratorProduct'),
      positiveInteger(row.t0DenominatorProduct, 't0DenominatorProduct'), row.rootCount),
    WB: fraction(positiveInteger(row.wbProduct, 'wbProduct'), 1n, row.rootCount),
  };
}
function indexCells(cells) {
  require(Array.isArray(cells), 'cells must be an array');
  const map = new Map(), models = new Set(), tasks = new Set(), conditions = new Set();
  for (const row of cells) {
    require(row && [row.model, row.task, row.condition].every(stringId), 'Cell identity is missing');
    const id = key(row.model, row.task, row.condition);
    require(!map.has(id), 'Duplicate model/question/condition cell');
    map.set(id, { row, values: cellValues(row) });
    models.add(row.model); tasks.add(row.task); conditions.add(row.condition);
  }
  return { map, models: [...models], tasks: [...tasks].sort(), conditions };
}
function select(index, options) {
  require(options && stringId(options.condition), 'A prompt condition is required');
  require(index.conditions.has(options.condition), 'Unknown condition');
  require(Array.isArray(options.models) && options.models.every(stringId) &&
    new Set(options.models).size === options.models.length, 'Selected model roster must be unique');
  require(options.models.every(m => index.models.includes(m)), 'Unknown selected model');
  const questionIds = options.questionIds ?? index.tasks;
  require(Array.isArray(questionIds) && new Set(questionIds).size === questionIds.length &&
    questionIds.every(t => index.tasks.includes(t)), 'Unknown or duplicate selected question');
  return { models: [...options.models], condition: options.condition,
    metric: metricName(options.metric ?? 'T0'), questionIds: [...questionIds].sort() };
}
function rankEntries(entries, metric) {
  const direction = metric === 'T0' ? -1 : 1;
  const sorted = entries.map(x => ({ ...x })).sort((a, b) =>
    direction * compare(a.values[metric], b.values[metric]) || a.id.localeCompare(b.id));
  for (let start = 0; start < sorted.length;) {
    let end = start + 1;
    while (end < sorted.length && compare(sorted[start].values[metric], sorted[end].values[metric]) === 0) end++;
    const rankTwice = start + 1 + end;
    for (let i = start; i < end; i++) Object.assign(sorted[i], {
      rank: rankTwice / 2, rankTwice, tieSize: end - start,
      rankPoints: sorted.length > 1 ? 100 * (sorted.length - rankTwice / 2) / (sorted.length - 1) : null,
      outrightFirst: start === 0 && end - start === 1,
      tiedFirst: start === 0 && end - start > 1,
    });
    start = end;
  }
  return sorted;
}
/** Generic exact midranks; missing values stay N/A, including missing rank points. */
export function midranks(entries, { higherIsBetter = true } = {}) {
  require(Array.isArray(entries), 'entries must be an array');
  require(entries.every(x => stringId(x.id)) && new Set(entries.map(x => x.id)).size === entries.length,
    'Entries require unique ids');
  const metric = higherIsBetter ? 'T0' : 'WB';
  const measured = entries.filter(x => x.value !== null).map(x =>
    ({ id: x.id, values: { [metric]: parseExact(x.value) } }));
  const ranks = new Map(rankEntries(measured, metric).map(x => [x.id, x]));
  return entries.map(x => {
    const r = ranks.get(x.id);
    return { ...x, rank: r?.rank ?? null, rankPoints: r?.rankPoints ?? null,
      rankTwice: r?.rankTwice ?? null, tieSize: r?.tieSize ?? null,
      outrightFirst: r?.outrightFirst ?? false, tiedFirst: r?.tiedFirst ?? false };
  });
}
/** Fixed-roster shared-question aggregation; never drops a model to create overlap. */
export function aggregateRanks(cells, options) {
  const index = indexCells(cells), o = select(index, options);
  const get = (m, t) => index.map.get(key(m, t, o.condition));
  const coverage = Object.fromEntries(o.models.map(m => [m, index.tasks.filter(t => get(m, t)?.values).length]));
  const sharedTasks = o.models.length >= 2 ? o.questionIds.filter(t => o.models.every(m => get(m, t)?.values)) : [];
  const reason = o.models.length < 2 ? 'fewer_than_two_models' : !sharedTasks.length ? 'no_shared_questions' : null;
  const rows = o.models.map(model => ({ model, meanRank: null, meanRankExact: null,
    rankPoints: null, rankPointsExact: null, firstPlaceCount: 0, tiedFirstPlaceCount: 0, topRankCount: 0,
    sharedQuestionCount: sharedTasks.length, measuredQuestionCount: coverage[model], perQuestion: [],
    ...display(null, null) }));
  if (!reason) {
    const byModel = new Map(rows.map(r => [r.model, r]));
    for (const task of sharedTasks) {
      const ranked = rankEntries(o.models.map(model => ({ id: model, values: get(model, task).values })), o.metric);
      for (const r of ranked) {
        const target = byModel.get(r.id), cell = get(r.id, task).row;
        target.perQuestion.push({ task, rank: r.rank, rankTwice: r.rankTwice, rankPoints: r.rankPoints,
          tieSize: r.tieSize, outrightFirst: r.outrightFirst, tiedFirst: r.tiedFirst,
          measurableCount: cell.measurableCount, validCount: cell.validCount, scheduledCount: 4,
          ...display(get(r.id, task).values.T0, get(r.id, task).values.WB) });
        target.firstPlaceCount += Number(r.outrightFirst); target.tiedFirstPlaceCount += Number(r.tiedFirst);
        target.topRankCount += Number(r.outrightFirst || r.tiedFirst);
      }
    }
    for (const row of rows) {
      const sum = row.perQuestion.reduce((s, x) => s + BigInt(x.rankTwice), 0n), q = BigInt(sharedTasks.length);
      row.rankSumTwice = sum.toString();
      row.meanRank = Number(sum) / (2 * sharedTasks.length);
      row.meanRankExact = rationalString(sum, 2n * q);
      const pn = 100n * (2n * BigInt(o.models.length) * q - sum), pd = 2n * q * BigInt(o.models.length - 1);
      row.rankPoints = Number(pn) / Number(pd); row.rankPointsExact = rationalString(pn, pd);
      Object.assign(row, display(productMean(sharedTasks.map(t => get(row.model, t).values.T0)),
        productMean(sharedTasks.map(t => get(row.model, t).values.WB))));
    }
    rows.sort((a, b) => {
      const x = BigInt(a.rankSumTwice), y = BigInt(b.rankSumTwice);
      return (x < y ? -1 : x > y ? 1 : 0) || a.model.localeCompare(b.model);
    });
    // These are ties in equal-question rank sums, not rounded display score strings.
    for (let start = 0; start < rows.length;) {
      let end = start + 1;
      while (end < rows.length && rows[start].meanRankExact === rows[end].meanRankExact) end++;
      for (let i = start; i < end; i++) { rows[i].place = start + 1; rows[i].aggregateTieSize = end - start; }
      start = end;
    }
  }
  return { version: VERSION, condition: o.condition, metric: o.metric, models: o.models,
    sharedTasks, rows, reason, coverage, requestedQuestionCount: o.questionIds.length,
    firstPlaceDefinition: 'firstPlaceCount is outright best; tiedFirstPlaceCount counts shared best; topRankCount is their sum',
    aggregation: 'Equal question mean of exact midranks; both magnitude aggregates use this identical shared set',
    missingPolicy: 'No invented metric, rank or point value for failed or unmeasured outcomes' };
}
/** All question rows retained; only measured selected models receive ranks. */
export function rankQuestion(cells, options) {
  const index = indexCells(cells), o = select(index, options);
  require(index.tasks.includes(options.task), 'Unknown question');
  const task = options.task, selected = new Set(o.models);
  const get = m => index.map.get(key(m, task, o.condition));
  const eligible = o.models.filter(m => get(m)?.values);
  const ranked = new Map(rankEntries(eligible.map(model => ({ id: model, values: get(model).values })), o.metric).map(r => [r.id, r]));
  const shared = o.models.length >= 2 && eligible.length === o.models.length;
  const inAggregate = shared && o.questionIds.includes(task);
  const reason = o.models.length < 2 ? 'fewer_than_two_models' : !eligible.length ? 'no_measured_selected_models' : !shared ? 'not_shared_by_selected_roster' : !inAggregate ? 'outside_requested_question_set' : null;
  const rows = index.models.map(model => {
    const c = get(model), r = ranked.get(model);
    return { ...(c?.row ?? {}), model, task, condition: o.condition, selected: selected.has(model),
      eligible: !!r, rank: r?.rank ?? null, rankPoints: r?.rankPoints ?? null, tieSize: r?.tieSize ?? null,
      measurableCount: c?.row.measurableCount ?? 0, validCount: c?.row.validCount ?? 0,
      scheduledCount: c?.row.scheduledCount ?? null,
      reason: !selected.has(model) ? 'outside_selected_roster' : !c?.values ? 'no_measured_proof' : null,
      ...display(c?.values?.T0 ?? null, c?.values?.WB ?? null) };
  });
  rows.sort((a, b) => (a.rank === null) - (b.rank === null) ||
    (a.rank !== null && b.rank !== null ? a.rank - b.rank : 0) || a.model.localeCompare(b.model));
  return { version: VERSION, task, condition: o.condition, metric: o.metric, models: o.models,
    rows, eligibleCount: eligible.length, shared, inAggregate, reason,
    warning: reason ? 'Question ranks use only measured selected models; this question is not in a fixed-roster shared-question aggregate.' : null,
    rankScope: 'Measured selected models on this question; outside-roster rows remain unranked' };
}
/** Full 8-model x 4-response question/prompt groups only. Filter AFTER this call. */
export function rankOutputs(attempts, { metric = 'T0' } = {}) {
  metric = metricName(metric);
  require(Array.isArray(attempts), 'attempts must be an array');
  const groups = new Map(), identities = new Set(), ids = new Set();
  for (let i = 0; i < attempts.length; i++) {
    const row = attempts[i];
    require(row && MODEL_ROSTER.includes(row.model) && stringId(row.task) && stringId(row.condition) &&
      Number.isInteger(row.sample) && row.sample >= 1 && row.sample <= 4, 'Output identity outside the fixed population');
    const identity = key(row.model, row.task, row.condition, row.sample);
    require(!identities.has(identity), 'Duplicate output slot'); identities.add(identity);
    if (row.attemptId !== undefined) { require(stringId(row.attemptId) && !ids.has(row.attemptId), 'Duplicate or invalid attemptId'); ids.add(row.attemptId); }
    require(typeof row.valid === 'boolean' && typeof row.measurable === 'boolean' && (!row.measurable || row.valid),
      'Invalid output validity/measurement flags');
    let values = null;
    if (row.valid && row.measurable) values = {
      T0: fraction(positiveInteger(row.t0Numerator, 't0Numerator'), positiveInteger(row.t0Denominator, 't0Denominator')),
      WB: fraction(positiveInteger(row.W_B, 'W_B'), 1n),
    };
    else require(row.t0Numerator == null && row.t0Denominator == null && row.W_B == null,
      'Failed/unmeasured output metrics must remain null');
    const groupKey = key(row.task, row.condition);
    if (!groups.has(groupKey)) groups.set(groupKey, []);
    groups.get(groupKey).push({ id: identity, index: i, values });
  }
  const result = attempts.map(row => ({ ...row, outputRank: null, outputPopulation: null,
    outputScheduledPopulation: 32, outputTieSize: null, outputRankMetric: metric,
    outputRankScope: 'All measured outputs among eight models and four scheduled samples, before display filters' }));
  for (const group of groups.values()) {
    require(group.length === 32, 'rankOutputs requires complete 32-slot question/prompt groups; filter only after ranking');
    // Unique identities plus the fixed 8x4 allowed domain imply every slot is present.
    const eligible = group.filter(x => x.values), ranks = rankEntries(eligible, metric);
    for (const x of group) result[x.index].outputPopulation = eligible.length;
    for (const r of ranks) Object.assign(result[r.index], { outputRank: r.rank, outputTieSize: r.tieSize });
  }
  return result;
}
