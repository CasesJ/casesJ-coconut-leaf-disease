const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../../static/monitoring.js'), 'utf8');

function dates(now) {
  class FixedDate extends Date {
    constructor(...args) { super(...(args.length ? args : [now])); }
  }
  const context = { window: {}, document: { addEventListener() {} }, Date: FixedDate };
  vm.createContext(context);
  vm.runInContext(source, context);
  return context;
}

test('monitoring schedules roll over at Philippine midnight', () => {
  const before = dates('2026-10-24T15:59:59Z');
  const after = dates('2026-10-24T16:00:00Z');
  assert.equal(before.monitoringToday(), '2026-10-24');
  assert.equal(after.monitoringToday(), '2026-10-25');
  assert.equal(after.monitoringDue('2026-10-25').text, 'Due today');
  assert.equal(after.monitoringDue('2026-10-24').text, 'Overdue by 1 day');
});

test('monitoring offsets preserve leap days and year boundaries', () => {
  const context = dates('2024-02-28T16:00:00Z');
  assert.equal(context.monitoringToday(), '2024-02-29');
  assert.equal(context.monitoringTomorrow(), '2024-03-01');
  assert.equal(context.monitoringDayOffset('2026-12-31', 1), '2027-01-01');
});

test('monitoring due labels retain urgency and missing-date fallbacks', () => {
  const context = dates('2026-10-24T16:00:00Z');
  assert.equal(context.monitoringDue('2026-10-27').cls, 'due');
  assert.equal(context.monitoringDue('2026-10-28').cls, '');
  assert.equal(context.monitoringDue('2026-10-23').text, 'Overdue by 2 days');
  assert.equal(context.monitoringDue('').text, 'Not scheduled');
  assert.equal(context.monitoringDue('invalid').text, 'Not scheduled');
});
