const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const test = require('node:test');
const source = fs.readFileSync(require('node:path').join(__dirname, '../../static/app.js'), 'utf8');

function harness() {
  const elements = new Map();
  const element = id => {
    if (!elements.has(id)) elements.set(id, { value: '', disabled: false });
    return elements.get(id);
  };
  const context = {
    window: {}, currentToken: 'token', currentUserIsExpert: true,
    expertQueueRecords: [], getCachedExpertRecords: () => null,
    document: { getElementById: element, querySelector: () => element('save') },
    openExpertRecommendationEditor() {}, setExpertRecommendationDisease() {},
    showToast() {}, console, Date,
    fetch: () => { throw new Error('Unexpected request'); },
  };
  const start = source.indexOf('window.loadExpertRecommendation =');
  const end = source.indexOf('window.editExpertRecommendation =', start);
  vm.runInNewContext('let expertRecommendationLoadId = 0; const expertRecommendationCache = new Map();\n' + source.slice(start, end), context);
  return { context, element, load: context.window.loadExpertRecommendation };
}

test('saved record advice is ready immediately without fetching defaults', async () => {
  const h = harness();
  h.context.expertQueueRecords = [{ user_id: 'u', id: 'r', recommendation_snapshot: {
    recommendations: { fertilizer: 'Saved advice' }, note: 'Expert note',
  } }];
  await h.load('stem bleeding', .8, { userId: 'u', recordId: 'r' });
  assert.equal(h.element('expert-fertilizer').value, 'Saved advice');
  assert.equal(h.element('expert-note').value, 'Expert note');
  assert.equal(h.element('save').disabled, false);
});

test('late responses cannot replace the currently selected review', async () => {
  const h = harness();
  const pending = [];
  h.context.fetch = () => new Promise(resolve => pending.push(resolve));
  const first = h.load('stem bleeding', .8);
  assert.equal(h.element('save').disabled, true);
  const second = h.load('bud rot', .9);
  pending[1]({ ok: true, json: async () => ({ recommendations: { fertilizer: 'Second' } }) });
  await second;
  pending[0]({ ok: true, json: async () => ({ recommendations: { fertilizer: 'First' } }) });
  await first;
  assert.equal(h.element('expert-fertilizer').value, 'Second');
  await h.load('bud rot', .9);
  assert.equal(pending.length, 2, 'repeat opens reuse recently fetched advice');
});
