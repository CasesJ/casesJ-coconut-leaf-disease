const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../../static/monitoring.js'), 'utf8');

function harness(expert = false) {
  const elements = new Map();
  const element = id => {
    if (!elements.has(id)) elements.set(id, { innerHTML: '', textContent: '', value: '', style: {},
      open: false, focus() {}, replaceChildren() { this.innerHTML = ''; },
      showModal() { this.open = true; }, close() { this.open = false; },
      setAttribute() {}, addEventListener() {} });
    return elements.get(id);
  };
  const context = { window: {}, currentUserIsExpert: expert, currentToken: 'fixture-token',
    escapeHtml: value => String(value).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]),
    document: { getElementById: element, querySelectorAll: () => [], addEventListener() {},
      documentElement: { classList: { add() {}, remove() {} } } },
    showToast() {}, monitoringIdentityStatus: () => 'Identity not reviewed',
    fetchExpertRecords: async () => ({ records: [] }), Date, Intl };
  vm.createContext(context);
  vm.runInContext(source, context);
  return { context, element, state: vm.runInContext('monitoringState', context) };
}

test('search, status filters and sorting retain original case indexes for detail actions', () => {
  const h = harness();
  h.state.cases = [
    { id: 'a', tree_id: 'TREE-A', disease: 'Bud Rot', treatment_status: 'awaiting_treatment', created_at: '2026-10-01' },
    { id: 'b', tree_id: 'TREE-B', disease: 'Stem Bleeding', treatment_status: 'treatment_applied', last_inspection_at: '2026-11-02', created_at: '2026-09-01' }
  ];
  h.context.monitoringRenderCaseList();
  let html = h.element('monitor-case-list').innerHTML;
  assert.ok(html.indexOf('data-monitor-case="1"') < html.indexOf('data-monitor-case="0"'));
  assert.doesNotMatch(html, /<img|Care record/);
  h.state.search = 'tree-b';
  h.context.monitoringRenderCaseList();
  html = h.element('monitor-case-list').innerHTML;
  assert.match(html, /data-monitor-case="1"/);
  assert.doesNotMatch(html, /TREE-A/);
  h.state.filter = 'awaiting';
  h.context.monitoringRenderCaseList();
  assert.match(h.element('monitor-case-list').innerHTML, /No matching treatment cases/);
});

test('only explicit completion produces Completed and missing statuses remain unrecorded', () => {
  const h = harness();
  assert.equal(h.context.monitoringTreatmentBadge({ latest_review_status: 'expert_confirmed' }).text, 'Not recorded');
  assert.equal(h.context.monitoringTreatmentBadge({ monitoring_status: 'finish' }).text, 'Completed');
  const dueYesterday = h.context.monitoringDayOffset(h.context.monitoringToday(), -1);
  assert.equal(h.context.monitoringInspectionBadge({ next_post_treatment_follow_up_date: dueYesterday }).text, 'Needs Inspection');
  assert.equal(h.context.monitoringInspectionBadge({ planned_treatment_date: dueYesterday }).text, 'Awaiting Inspection');
});

test('dashboard loads existing case list without automatically fetching or opening details', async () => {
  const h = harness();
  const requests = [];
  h.context.fetch = async url => { requests.push(url); return { ok: true, json: async () => ({ cases: [{ id: 'a' }] }) }; };
  await h.context.window.loadMonitoringDashboard();
  assert.deepEqual(requests, ['/monitoring/cases']);
  assert.equal(h.element('monitor-case-modal').open, false);
});

test('late case responses cannot replace a newer case or repopulate a closed modal', async () => {
  const h = harness();
  h.state.cases = [{ id: 'a' }, { id: 'b' }];
  const pending = [];
  h.context.fetch = () => new Promise(resolve => pending.push(overview => resolve({ ok: true, json: async () => overview })));
  const rendered = [];
  h.context.monitoringRenderOverview = overview => rendered.push(overview.case.id);
  const first = h.context.monitoringSelectCase(0);
  const second = h.context.monitoringSelectCase(1);
  pending[1]({ case: { id: 'b' } });
  await second;
  pending[0]({ case: { id: 'a' } });
  await first;
  assert.deepEqual(rendered, ['b']);
  const third = h.context.monitoringSelectCase(0);
  h.context.monitoringCloseModal();
  pending[2]({ case: { id: 'a' } });
  await third;
  assert.deepEqual(rendered, ['b']);
  assert.equal(h.state.overview, null);
});

test('detail view preserves real recommendations and role-specific controls without invented history', () => {
  const overview = { case: { id: 'a', tree_id: 'TREE-A', disease: 'Bud Rot', monitoring_status: 'waiting', treatment_status: 'awaiting_treatment' },
    treatment: { recommendation_snapshot: { source: 'default', recommendations: { treatment: '<script>unsafe</script>', prevention: ['Keep recorded guidance'] } } } };
  const farmer = harness();
  farmer.context.monitoringRenderOverview(overview);
  let html = farmer.element('monitor-detail').innerHTML;
  assert.match(html, /Default recommendation/);
  assert.match(html, /&lt;script&gt;unsafe&lt;\/script&gt;/);
  assert.match(html, /No follow-up image yet/);
  assert.match(html, /Not recorded/);
  assert.doesNotMatch(html, /<script>|Inspection 1|Treatment completed|Provided by expert/);
  for (const id of ['monitor-treatment-schedule-form', 'monitor-treatment-application-form', 'monitor-upload-form']) assert.ok(html.includes(id));
  assert.doesNotMatch(html, /data-monitor-finish|monitor-review-form/);
  const expert = harness(true);
  overview.follow_ups = [{ id: 'follow-1', created_at: '2026-10-23', expert_note: 'Recorded note' }];
  expert.context.monitoringRenderOverview(overview);
  html = expert.element('monitor-detail').innerHTML;
  assert.match(html, /monitor-review-form/);
  assert.match(html, /data-monitor-finish/);
  assert.doesNotMatch(html, /monitor-upload-form|monitor-treatment-application-form/);
});

test('recorded application and inspection details remain available in the modal', () => {
  const h = harness();
  h.context.monitoringRenderOverview({ case: { id: 'a', monitoring_status: 'finish', finished_at: '2026-10-30', latest_review_status: 'expert_confirmed' },
    treatment_applications: [{ application_date: '2026-10-24', treatment_performed: 'Recorded application', notes: 'Actual notes' }],
    follow_ups: [{ id: 'inspection-a', created_at: '2026-10-27', expert_note: 'Visible improvement', review_status: 'expert_confirmed', confirmed_outcome: 'improving' }] });
  const html = h.element('monitor-detail').innerHTML;
  for (const text of ['Actual notes', 'Recorded application', 'Visible improvement', 'Treatment completed', 'Inspection 1']) assert.ok(html.includes(text));
  assert.doesNotMatch(html, /monitor-upload-form|monitor-treatment-application-form|monitor-treatment-schedule-form/);
});
