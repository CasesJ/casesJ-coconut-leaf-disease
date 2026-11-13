const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../../static/app.js'), 'utf8');

function harness() {
  let replacements = 0;
  const queue = {
    firstChild: null,
    set innerHTML(html) { replacements++; this.firstChild = { html }; },
  };
  const search = { value: '' };
  const count = {};
  const context = {
    window: {},
    document: { getElementById: id => ({
      'expert-pending-list': queue, 'expert-queue-search': search, 'expert-queue-count': count,
    })[id] },
    expertQueueRecords: [{ id: 'rec-1', filename: 'coconut.jpg', email: 'farmer@example.test',
      timestamp: '2026-10-23', detections: [] }],
    currentExpertQueueQuickFilter: 'all',
    getExpertQueueDetails: () => ({ primary: {}, detections: [], confidence: .8, status: 'pending', location: '' }),
    renderExpertRecordsTable: records => JSON.stringify(records),
  };
  const start = source.indexOf('window.filterExpertQueue =');
  const end = source.indexOf('window.setExpertQueueQuickFilter =', start);
  // Include the render cache declaration without executing unrelated application initialization.
  const declaration = source.match(/const expertQueueRenderCache = new WeakMap\(\);/);
  vm.runInNewContext((declaration?.[0] || '') + '\n' + source.slice(start, end), context);
  return { context, queue, search, count, render: context.window.filterExpertQueue,
    replacements: () => replacements };
}

test('unchanged Expert results retain thumbnail nodes across repeated renders', () => {
  const h = harness();
  h.render();
  const table = h.queue.firstChild;
  h.render();
  h.search.value = 'coconut'; // Same matching rows.
  h.render();
  assert.equal(h.replacements(), 1);
  assert.equal(h.queue.firstChild, table);
  assert.equal(h.count.textContent, '1 of 1 records');
});

test('changed record content or matching results still replaces the table', () => {
  const h = harness();
  h.render();
  h.context.expertQueueRecords[0].filename = 'updated.jpg';
  h.render();
  assert.equal(h.replacements(), 2);
  h.search.value = 'no-match';
  h.render();
  assert.equal(h.replacements(), 3);
  assert.equal(h.count.textContent, '0 of 1 records');
});

test('external queue replacement invalidates the cached render', () => {
  const h = harness();
  h.render();
  h.queue.innerHTML = 'Loading';
  h.render();
  assert.equal(h.replacements(), 3);
  assert.notEqual(h.queue.firstChild.html, 'Loading');
});


test('reloading unchanged Expert queue data keeps existing thumbnails during and after refresh', async () => {
  const h = harness();
  h.render();
  const table = h.queue.firstChild;
  Object.assign(h.context, {
    currentToken: 'test-token', currentUserIsExpert: true,
    currentExpertFilter: 'pending', getExpertFilterLabel: () => 'pending uploads',
    fetchExpertRecords: async () => {
      assert.equal(h.queue.firstChild, table, 'refresh must not discard images for a loading message');
      return { records: h.context.expertQueueRecords };
    },
    formatVerificationStatus: () => ({ cls: 'pending' }),
    updateExpertQueueDiseaseOptions: () => {}, renderExpertCommandCenter: () => {},
    fetch: async () => ({ ok: true, json: async () => ({}) }),
    filterExpertQueue: h.render,
    console: { error: (...args) => assert.fail(JSON.stringify(args)) },
  });
  const start = source.indexOf('window.loadExpertReview =');
  const end = source.indexOf('window.loadExpertAuditLog =', start);
  vm.runInNewContext('let expertReviewLoadId = 0;\n' + source.slice(start, end), h.context);
  await h.context.window.loadExpertReview();
  assert.equal(h.replacements(), 1);
  assert.equal(h.queue.firstChild, table);
});

test('a stale Expert queue load cannot replace a newer successful refresh with an error', async () => {
  let rejectStale;
  const queue = { innerHTML: 'Existing pending record' };
  const errors = [];
  let recordUpdates = 0;
  const context = {
    window: {}, currentToken: 'test-token', currentUserIsExpert: true,
    currentExpertFilter: 'pending', expertQueueRecords: [],
    document: {
      getElementById: id => ({
        'expert-pending-list': queue,
        'expert-filter-state': {},
        'expert-recommendation-status': {},
      })[id] || null,
    },
    expertQueueRenderCache: new WeakMap(),
    getExpertFilterLabel: () => 'pending uploads',
    formatVerificationStatus: () => ({ cls: 'pending' }),
    updateExpertQueueDiseaseOptions: () => {}, renderExpertCommandCenter: () => {},
    filterExpertQueue: () => { recordUpdates += 1; queue.innerHTML = 'Remaining pending records'; },
    fetch: async () => ({ ok: true, json: async () => ({}) }),
    console: { error: (...args) => errors.push(args) },
  };
  let requests = 0;
  context.fetchExpertRecords = () => {
    requests += 1;
    if (requests === 1) return new Promise((_, reject) => { rejectStale = reject; });
    return Promise.resolve({ records: [{ id: 'remaining' }] });
  };
  const start = source.indexOf('window.loadExpertReview =');
  const end = source.indexOf('window.loadExpertAuditLog =', start);
  vm.runInNewContext('let expertReviewLoadId = 0;\n' + source.slice(start, end), context);

  const olderLoad = context.window.loadExpertReview();
  const refreshedLoad = context.window.loadExpertReview({ force: true });
  await refreshedLoad;
  rejectStale(new Error('Expert records changed. Please refresh.'));
  await olderLoad;

  assert.equal(recordUpdates, 1);
  assert.equal(queue.innerHTML, 'Remaining pending records');
  assert.equal(errors.length, 0);
});

test('the newest Expert queue load still shows a genuine failure', async () => {
  const queue = { innerHTML: '' };
  const errors = [];
  const context = {
    window: {}, currentToken: 'test-token', currentUserIsExpert: true,
    currentExpertFilter: 'pending',
    document: { getElementById: id => ({ 'expert-pending-list': queue, 'expert-filter-state': {} })[id] || null },
    expertQueueRenderCache: new WeakMap(), getExpertFilterLabel: () => 'pending uploads',
    fetchExpertRecords: async () => { throw new Error('Failed to load records (503).'); },
    console: { error: (...args) => errors.push(args) }, escapeHtml: value => String(value),
  };
  const start = source.indexOf('window.loadExpertReview =');
  const end = source.indexOf('window.loadExpertAuditLog =', start);
  vm.runInNewContext('let expertReviewLoadId = 0;\n' + source.slice(start, end), context);

  await context.window.loadExpertReview();
  assert.match(queue.innerHTML, /Error loading expert queue/);
  assert.match(queue.innerHTML, /Failed to load records \(503\)/);
  assert.equal(errors.length, 1);
});
