const assert = require('node:assert/strict');
const fs = require('node:fs');
const test = require('node:test');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../../static/app.js'), 'utf8');

function harness(events, query = '') {
  const list = {};
  const context = { expertAuditEvents: events, Date,
    document: { getElementById: id => id === 'expert-audit-list' ? list : { value: query } },
    encodeInlineValue: encodeURIComponent,
    escapeHtml: value => String(value).replaceAll('<', '&lt;'),
    formatVerificationStatus: () => ({ label: 'Verified' }) };
  vm.runInNewContext(source.slice(source.indexOf('function renderExpertAuditTable('),
    source.indexOf('window.filterExpertAuditLog =')), context);
  context.renderExpertAuditLog();
  return list.innerHTML;
}

test('audit columns follow the requested order and old events remain Unassigned', () => {
  const html = harness([{ tree_id: 'TREE-ABC' }, { target: {} }]);
  assert.match(html, /<thead><tr><th>Date and time<\/th><th>Action<\/th><th>Record<\/th><th>Uploaded by<\/th><th>TREE ID<\/th><th>Status<\/th><\/tr><\/thead>/);
  assert.match(html, /TREE-ABC/);
  assert.match(html, /Unassigned/);
  for (const column of ['Action', 'Record', 'Uploaded by'])
    assert.ok(html.includes(`<th>${column}</th>`));
});

test('each row shows the detection uploader rather than the acting expert', () => {
  const html = harness([{ action: 'verify_record', actor_email: 'expert@example.com',
    upload: { filename: 'coconut.jpg', email: 'farmer@example.com' },
    target: { user_id: 'farmer-1', tree_id: 'TREE-ABC' } }]);
  assert.match(html, /data-label="Record">coconut.jpg<\/td><td data-label="Uploaded by">farmer@example.com<\/td><td data-label="Tree ID">/);
  assert.doesNotMatch(html, /expert@example.com/);
  assert.match(harness([{ target: { user_id: 'farmer-1' } }]), /data-label="Uploaded by">farmer-1<\/td>/);
});

test('Tree IDs are searchable without hiding historical events by default', () => {
  const events = [{ tree_id: 'TREE-ABC' }, { target: {} }];
  const html = harness(events, 'tree-abc');
  assert.match(html, /TREE-ABC/);
  assert.doesNotMatch(html, /Unassigned/);
  assert.match(harness(events, 'unassigned'), /Unassigned/);
});

test('linked Tree IDs navigate to their owner?s monitoring details', () => {
  const html = harness([{ tree_id: 'TREE-ABC', target: { user_id: 'farmer-1' } }]);
  assert.match(html, /monitoringOpenTree\('TREE-ABC','farmer-1'\)/);
});

test('audit status waits for explicit expert completion', () => {
  assert.match(harness([{ tree_id: 'TREE-A' }]), /Waiting/);
  assert.match(harness([{ tree_id: 'TREE-A', monitoring_status: 'finish' }]), /Finish/);
});
