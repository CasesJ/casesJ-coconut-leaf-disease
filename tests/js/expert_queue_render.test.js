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
  vm.runInNewContext(source.slice(start, end), h.context);
  await h.context.window.loadExpertReview();
  assert.equal(h.replacements(), 1);
  assert.equal(h.queue.firstChild, table);
});
