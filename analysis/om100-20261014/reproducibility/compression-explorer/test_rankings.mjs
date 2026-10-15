import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { MODEL_ROSTER, compareGeometricFractions, aggregateGeometricMean, midranks,
  aggregateRanks, rankQuestion, rankOutputs } from './rankings.mjs';
const ex = (n, d = 1, k = 1) => ({ numeratorProduct: String(n), denominatorProduct: String(d), rootCount: k });
const condition = 'mathematical_compression';
const cell = (model, task, t = 4, wb = 10, k = 1) => ({ model, task, condition,
  rootCount: k, measurableCount: k, validCount: k, scheduledCount: 4,
  t0NumeratorProduct: k ? (BigInt(t) ** BigInt(k)).toString() : null,
  t0DenominatorProduct: k ? '1' : null,
  wbProduct: k ? (BigInt(wb) ** BigInt(k)).toString() : null });
const opts = (models, metric = 'T0') => ({ models, condition, metric });
const approximate = (a, b, e = 1e-10) => assert.ok(Math.abs(a - b) <= e, `${a} != ${b}`);

test('exact rational roots tie across sample counts and unreduced fractions', () => {
  assert.equal(compareGeometricFractions(ex(2, 3), ex(16, 81, 4)), 0);
  assert.equal(compareGeometricFractions(ex(4, 6), ex(2, 3)), 0);
});
test('nearby huge fractions are not tied by rounded logs or display strings', () => {
  const n = 10n ** 100n;
  assert.equal(compareGeometricFractions(ex(n + 1n), ex(n)), 1);
  assert.equal(compareGeometricFractions(ex(n), ex(n + 1n)), -1);
});
test('geometric comparison accounts for unequal roots, not product size alone', () => {
  assert.equal(compareGeometricFractions(ex(16, 1, 4), ex(3)), -1);
  assert.equal(compareGeometricFractions(ex(81, 1, 4), ex(8, 1, 3)), 1);
});
test('unsafe numbers, zero denominators, and invalid roots fail closed', () => {
  assert.throws(() => compareGeometricFractions(ex(0), ex(1)), /positive/);
  assert.throws(() => compareGeometricFractions(ex(1, 0), ex(1)), /positive/);
  assert.throws(() => compareGeometricFractions(ex(1, 1, 0), ex(1)), /Invalid/);
  assert.throws(() => compareGeometricFractions({ numeratorProduct: Number.MAX_SAFE_INTEGER + 1,
    denominatorProduct: '1', rootCount: 1 }, ex(1)), /exact integer/);
});
test('midranks average tied positions and leave failures N/A', () => {
  const out = midranks([{ id: 'a', value: ex(4) }, { id: 'b', value: ex(16, 1, 2) },
    { id: 'c', value: ex(2) }, { id: 'failed', value: null }]);
  assert.deepEqual(out.map(r => r.rank), [1.5, 1.5, 3, null]);
  assert.deepEqual(out.map(r => r.rankPoints), [75, 75, 0, null]);
  assert.equal(out[0].tiedFirst, true); assert.equal(out[0].outrightFirst, false);
});
test('all measured ties give50points and one participant has no competitive points', () => {
  const tied = midranks(['a', 'b', 'c'].map(id => ({ id, value: ex(3) })));
  assert.ok(tied.every(r => r.rank === 2 && r.rankPoints === 50));
  const one = midranks([{ id: 'a', value: ex(3) }, { id: 'b', value: null }]);
  assert.equal(one[0].rank, 1); assert.equal(one[0].rankPoints, null); assert.equal(one[1].rank, null);
});
test('WB ranks lower lengths first, independently of T0 direction', () => {
  const rows = [cell('a', 'q1', 10, 40), cell('b', 'q1', 2, 5)];
  assert.equal(aggregateRanks(rows, opts(['a', 'b'])).rows[0].model, 'a');
  assert.equal(aggregateRanks(rows, opts(['a', 'b'], 'WB')).rows[0].model, 'b');
});
test('fixed roster recomputes common support and never silently drops missing models', () => {
  const rows = [cell('a', 'q1', 10), cell('b', 'q1', 5), cell('c', 'q1', 2),
    cell('a', 'q2', 1), cell('b', 'q2', 1, 1, 0), cell('c', 'q2', 9)];
  const full = aggregateRanks(rows, opts(['a', 'b', 'c']));
  assert.deepEqual(full.sharedTasks, ['q1']); assert.deepEqual(full.models, ['a', 'b', 'c']);
  const subset = aggregateRanks(rows, opts(['a', 'c']));
  assert.deepEqual(subset.sharedTasks, ['q1', 'q2']); assert.equal(subset.rows[0].meanRank, 1.5);
  const selected = aggregateRanks(rows, { ...opts(['a', 'c']), questionIds: ['q2'] });
  assert.equal(selected.rows[0].model, 'c'); assert.deepEqual(selected.sharedTasks, ['q2']);
});
test('empty shared support and fewer than2 selected models have unavailable aggregates', () => {
  const rows = [cell('a', 'q1'), cell('b', 'q1', 1, 1, 0)];
  const empty = aggregateRanks(rows, opts(['a', 'b']));
  assert.equal(empty.reason, 'no_shared_questions'); assert.deepEqual(empty.sharedTasks, []);
  assert.ok(empty.rows.every(r => r.meanRank === null && r.rankPoints === null && r.meanLog2T0 === null));
  assert.equal(aggregateRanks(rows, opts(['a'])).reason, 'fewer_than_two_models');
  assert.equal(aggregateRanks(rows, opts([])).reason, 'fewer_than_two_models');
});
test('aggregate magnitudes weight questions equally despite different valid sample counts', () => {
  const rows = [cell('a', 'q1', 100, 100, 1), cell('a', 'q2', 1, 1, 4),
    cell('b', 'q1', 1, 1, 4), cell('b', 'q2', 1, 1, 1)];
  const a = aggregateRanks(rows, opts(['a', 'b'])).rows.find(r => r.model === 'a');
  approximate(a.meanLog2T0, Math.log2(10)); approximate(a.geomeanWB, 10);
  assert.equal(compareGeometricFractions(a.t0Exact, ex(10)), 0);
  assert.equal(compareGeometricFractions(aggregateGeometricMean([ex(100), ex(1, 1, 4)]), ex(10)), 0);
});
test('outright firsts and shared firsts are separate counts', () => {
  const rows = [cell('a', 'q1', 4), cell('b', 'q1', 4), cell('c', 'q1', 1),
    cell('a', 'q2', 4), cell('b', 'q2', 2), cell('c', 'q2', 1)];
  const a = aggregateRanks(rows, opts(['a', 'b', 'c'])).rows.find(r => r.model === 'a');
  assert.equal(a.firstPlaceCount, 1); assert.equal(a.tiedFirstPlaceCount, 1); assert.equal(a.topRankCount, 2);
  assert.equal(a.meanRankExact, '5/4'); assert.equal(a.rankPointsExact, '175/2');
});
test('single-question excluded models retain raw metrics but never get selected-roster ranks', () => {
  const rows = [cell('a', 'q1', 4), cell('b', 'q1', 1, 1, 0), cell('c', 'q1', 99)];
  const r = rankQuestion(rows, { ...opts(['a', 'b']), task: 'q1' });
  assert.equal(r.shared, false); assert.equal(r.eligibleCount, 1); assert.ok(r.warning);
  const a = r.rows.find(x => x.model === 'a'), b = r.rows.find(x => x.model === 'b'), c = r.rows.find(x => x.model === 'c');
  assert.equal(a.rank, 1); assert.equal(a.rankPoints, null); assert.equal(b.rank, null);
  assert.equal(c.rank, null); assert.equal(c.reason, 'outside_selected_roster'); assert.ok(c.meanLog2T0 > a.meanLog2T0);
});
test('missing cells remain N/A and duplicate/unknown selections are rejected', () => {
  const rows = [cell('a', 'q1'), cell('b', 'q2')];
  assert.equal(rankQuestion(rows, { ...opts(['a', 'b']), task: 'q1' }).rows.find(r => r.model === 'b').rank, null);
  assert.throws(() => aggregateRanks([...rows, rows[0]], opts(['a', 'b'])), /Duplicate/);
  assert.throws(() => aggregateRanks(rows, opts(['a', 'a'])), /unique/);
  assert.throws(() => aggregateRanks(rows, opts(['unknown'])), /Unknown/);
  assert.throws(() => aggregateRanks(rows, { ...opts(['a', 'b']), questionIds: ['typo'] }), /Unknown/);
});
test('input data are not mutated and returned results serialize without BigInt', () => {
  const rows = [cell('a', 'q1'), cell('b', 'q1')].map(Object.freeze); Object.freeze(rows);
  const before = JSON.stringify(rows); JSON.stringify(aggregateRanks(rows, opts(['a', 'b'])));
  assert.equal(JSON.stringify(rows), before);
});
function outputs() {
  return MODEL_ROSTER.flatMap((model, mi) => [1, 2, 3, 4].map(sample => ({
    model, task: 'q1', condition, sample, attemptId: `${model}:${sample}`,
    valid: true, measurable: true, t0Numerator: String(mi * 4 + sample), t0Denominator: '1', W_B: mi * 4 + sample,
  })));
}
test('individual ranks use all8models before filtering and retain failures without zero scores', () => {
  const all = outputs(); Object.assign(all[0], { valid: false, measurable: false, t0Numerator: null, t0Denominator: null, W_B: null });
  const ranked = rankOutputs(all), subset = ranked.filter(r => r.model === MODEL_ROSTER[0]);
  assert.deepEqual(subset.map(r => r.outputRank), [null, 31, 30, 29]);
  assert.ok(subset.every(r => r.outputPopulation === 31 && r.outputScheduledPopulation === 32));
  assert.equal(rankOutputs(all, { metric: 'WB' })[1].outputRank, 1);
  assert.throws(() => rankOutputs(all.filter(r => r.model === MODEL_ROSTER[0])), /complete 32-slot/);
});
test('individual exact ties stay tied and duplicate or incomplete groups are rejected', () => {
  const all = outputs(); all[0].t0Numerator = all[31].t0Numerator;
  const ranked = rankOutputs(all); assert.equal(ranked[0].outputRank, 1.5); assert.equal(ranked[31].outputRank, 1.5);
  assert.equal(ranked[0].outputTieSize, 2);
  assert.throws(() => rankOutputs([...all.slice(1), all[1]]), /Duplicate/);
  assert.throws(() => rankOutputs(all.slice(1)), /complete 32-slot/);
});

const packageRoot = process.env.EXPLORER_PACKAGE_ROOT
  ? pathToFileURL(resolve(process.env.EXPLORER_PACKAGE_ROOT) + '/') : new URL('../../', import.meta.url);
const addonPath = new URL('analysis-report/src/data.json', packageRoot);
const expectedPath = new URL('./fixtures/expected-default.json', import.meta.url);
const parentRanksPath = new URL('tables/main/conditional_ranks.json', packageRoot);
for (const path of [addonPath, expectedPath, parentRanksPath])
  assert.ok(fs.existsSync(path), `Required published package file is missing: ${path}`);
const available = true; // Missing package inputs fail, never silently skip live-data checks.
test('golden six-model23-question default matches all138 independent ranks and Fable75.65', { skip: !available }, () => {
  const addon = JSON.parse(fs.readFileSync(addonPath)), expected = JSON.parse(fs.readFileSync(expectedPath)).default;
  const cells = addon.queries.compression_cells.rows;
  const result = aggregateRanks(cells, { condition: expected.condition, models: expected.cohort, metric: 'T0' });
  assert.equal(result.reason, null); assert.deepEqual(result.sharedTasks, expected.shared_question_ids);
  assert.equal(result.rows.length, 6);
  for (const expectedRow of expected.ranking) {
    const r = result.rows.find(x => x.model === expectedRow.model);
    assert.equal(r.meanRankExact, expectedRow.average_rank_exact);
    assert.equal(r.rankPointsExact, expectedRow.rank_points_exact);
    for (const q of r.perQuestion) assert.equal(q.rank, expectedRow.per_question_midranks[q.task]);
  }
  approximate(result.rows[0].rankPoints, 75.65217391304348);
  assert.equal(result.rows[0].model, 'claude-fable-5-1');
  assert.deepEqual(aggregateRanks(cells, { ...opts(MODEL_ROSTER), metric: 'T0' }).sharedTasks, []);
});
test('live T0 and WB means agree with independent paper conditional-rank tables', { skip: !available }, () => {
  const addon = JSON.parse(fs.readFileSync(addonPath)), expected = JSON.parse(fs.readFileSync(expectedPath)).default;
  const prior = JSON.parse(fs.readFileSync(parentRanksPath));
  for (const metric of ['T0', 'WB']) {
    const r = aggregateRanks(addon.queries.compression_cells.rows, { ...opts(expected.cohort), metric });
    for (const row of r.rows) {
      const old = prior.find(x => x.condition === condition && x.coverage_gate === 25 && x.model === row.model && x.metric === (metric === 'WB' ? 'W_B' : metric));
      assert.ok(old); assert.equal(row.meanRank, old.mean); assert.equal(row.sharedQuestionCount, old.n_questions);
    }
  }
});
test('all9600 live output ranks preserve sample identities and3831 measured records', { skip: !available }, () => {
  const addon = JSON.parse(fs.readFileSync(addonPath)), attempts = addon.queries.compression_attempts.rows;
  const ranked = rankOutputs(attempts);
  assert.equal(ranked.length, 9600); assert.equal(new Set(ranked.map(r => r.attemptId)).size, 9600);
  assert.equal(ranked.filter(r => r.outputRank !== null).length, 3831);
  for (let i = 0; i < ranked.length; i++) {
    assert.equal(ranked[i].attemptId, attempts[i].attemptId);
    if (!attempts[i].valid || !attempts[i].measurable) assert.equal(ranked[i].outputRank, null);
  }
  const one = ranked.filter(r => r.model === MODEL_ROSTER[0] && r.task === 'OM100-065' && r.condition === condition);
  assert.equal(one.length, 4); assert.ok(one.every(r => r.outputScheduledPopulation === 32));
});

test('a shared question outside an explicit aggregate subset is flagged, not silently included', () => {
  const cells = [cell('a', 'q1'), cell('b', 'q1'), cell('a', 'q2'), cell('b', 'q2')];
  const r = rankQuestion(cells, { ...opts(['a', 'b']), task: 'q2', questionIds: ['q1'] });
  assert.equal(r.shared, true); assert.equal(r.inAggregate, false);
  assert.equal(r.reason, 'outside_requested_question_set'); assert.ok(r.warning);
});
test('all failed output groups retain32records, zero measured population and null ranks', () => {
  const all = outputs().map(r => ({ ...r, valid: false, measurable: false, t0Numerator: null, t0Denominator: null, W_B: null }));
  const ranked = rankOutputs(all);
  assert.equal(ranked.length, 32); assert.ok(ranked.every(r => r.outputRank === null && r.outputPopulation === 0));
});
test('scientific displays remain finite for exact metrics beyond floating-point magnitude range', () => {
  const cells = [cell('a', 'q1'), cell('b', 'q1')];
  cells[0].t0NumeratorProduct = (10n ** 1000n).toString();
  const r = aggregateRanks(cells, opts(['a', 'b']));
  assert.equal(r.rows[0].model, 'a'); assert.equal(r.rows[0].geomeanT0Scientific, '1e+1000');
});
// Reconcile against existing published statistical tables, without duplicating
// the large addon or historical full rank-oracle JSON in this source package.
test('all24 T0/WB published table cases agree, including4264 within-question midranks', () => {
  const addon = JSON.parse(fs.readFileSync(addonPath));
  const prior = JSON.parse(fs.readFileSync(parentRanksPath));
  const coverage = JSON.parse(fs.readFileSync(new URL('tables/main/coverage.json', packageRoot)));
  const questionRanks = JSON.parse(fs.readFileSync(new URL('tables/main/conditional_ranks_by_question.json', packageRoot)));
  let cases = 0, checked = 0;
  for (const support of coverage) for (const metric of ['T0', 'W_B']) {
    cases++;
    const result = aggregateRanks(addon.queries.compression_cells.rows,
      { condition: support.condition, models: support.models, metric });
    assert.deepEqual(result.sharedTasks, support.tasks);
    if (!support.tasks.length) {
      assert.ok(result.reason); assert.ok(result.rows.every(r => r.meanRank === null)); continue;
    }
    const expected = questionRanks.filter(x => x.condition === support.condition &&
      x.coverage_gate === support.coverage_gate && x.metric === metric);
    for (const row of result.rows) {
      const baseline = prior.find(x => x.condition === support.condition && x.coverage_gate === support.coverage_gate &&
        x.metric === metric && x.model === row.model);
      assert.ok(baseline); assert.equal(row.meanRank, baseline.mean);
      assert.equal(row.sharedQuestionCount, baseline.n_questions);
      const sumTwice = expected.filter(x => x.model === row.model).reduce((sum, x) => sum + BigInt(2 * x.rank), 0n);
      assert.equal(row.rankSumTwice, sumTwice.toString());
    }
    for (const q of expected) {
      const row = result.rows.find(x => x.model === q.model).perQuestion.find(x => x.task === q.task);
      assert.equal(row.rank, q.rank); checked++;
    }
  }
  assert.equal(cases, 24); assert.equal(checked, 4264);
});
