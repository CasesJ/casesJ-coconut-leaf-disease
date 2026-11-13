const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../../static/app.js'), 'utf8');

function historyHarness(records) {
  const list = {
    html: '',
    set innerHTML(value) { this.html = value; },
    querySelectorAll() { return []; },
  };
  const context = {
    window: {},
    document: { getElementById: id => id === 'verified-history-list' ? list : null },
    escapeHtml: value => String(value ?? ''),
    formatDiseaseClass: value => String(value ?? ''),
    encodedRecordImagePayload: () => 'image-payload',
    encodeInlineValue: value => String(value ?? ''),
    getPrimaryDetection: record => record.detections[0] || { class: 'Unknown', confidence: 0 },
    getRecordKey: record => record.id,
  };
  context.window = context;
  vm.createContext(context);
  const helperStart = source.indexOf('function getEmbeddedRecordGps');
  const helperEnd = source.indexOf('function metadataPinRecordId', helperStart);
  const historyStart = source.indexOf('window.filterVerifiedHistory');
  const historyEnd = source.indexOf('// Verified history is deliberately separate', historyStart);
  vm.runInContext(`${source.slice(helperStart, helperEnd)}\nlet verifiedHistoryRecords = [];\n${source.slice(historyStart, historyEnd)}`, context);
  vm.runInContext(`verifiedHistoryRecords = ${JSON.stringify(records)}; window.filterVerifiedHistory();`, context);
  return list.html;
}

test('Detection Records render no GPS location, map action, recommendation action, or recommendation panel', () => {
  const start = source.indexOf('async function loadUserRecords');
  const end = source.indexOf('// ── Expert', start);
  const body = source.slice(start, end);
  assert.doesNotMatch(body, /record-location/);
  assert.doesNotMatch(body, /View on map/);
  assert.doesNotMatch(body, /View recommendation/);
  assert.doesNotMatch(body, /record-recommendation-\$\{recordKey\}/);
});

test('Verified Disease History displays validated legacy DJI GPS with image, map, and recommendation actions', () => {
  const html = historyHarness([{
    id: 'verified-dji', filename: 'tree.jpg', image_url: '/media/tree.jpg', verified_by_role: 'expert',
    verified_at: '2026-11-12T01:00:00Z', verified_by: 'Expert',
    detections: [{ class: 'Bud Rot', confidence: .91 }],
    gps_data: JSON.stringify({ latitude: '7.20760', longitude: '125.63480', source: 'dji_exif' }),
    recommendation_snapshot: { recommendations: { fertilizer: 'Apply approved fertilizer' } },
  }]);
  assert.match(html, /Location/);
  assert.match(html, /7\.20760, 125\.63480/);
  assert.match(html, /showRecordLocationOnMap\(7\.2076, 125\.6348, 'Bud Rot'\);switchTab\('map'/);
  assert.ok(html.indexOf('View image') < html.indexOf('View on map'));
  assert.ok(html.indexOf('View on map') < html.indexOf('View recommendation'));
});

test('Verified Disease History keeps image and recommendation actions but hides GPS actions without an embedded source', () => {
  const html = historyHarness([{
    id: 'not-embedded', filename: 'tree.jpg', image_url: '/media/tree.jpg', verified_by_role: 'expert',
    verified_at: '2026-11-12T01:00:00Z', verified_by: 'Expert', lat: 7.2076, lng: 125.6348,
    gps_source: 'database', detections: [{ class: 'Bud Rot', confidence: .91 }],
    recommendation_snapshot: { recommendations: { fertilizer: 'Apply approved fertilizer' } },
  }]);
  assert.doesNotMatch(html, /record-location|Location|View on map|showRecordLocationOnMap/);
  assert.match(html, /View image/);
  assert.match(html, /View recommendation/);
});

test('Verified Disease History renders expert recommendation panels with and without embedded GPS', () => {
  const panels = [];
  const makeRow = () => {
    const badge = { replaceWith() {} };
    const details = { parentElement: { classList: { add() {} } } };
    return {
      querySelector(selector) {
        if (selector === '.record-status.verified') return badge;
        if (selector === '.record-detections') return details;
        return null;
      },
      appendChild(panel) { panels.push(panel); },
    };
  };
  const list = {
    html: '',
    set innerHTML(value) { this.html = value; },
    querySelectorAll() { return [makeRow(), makeRow()]; },
  };
  const context = {
    document: {
      getElementById: id => id === 'verified-history-list' ? list : null,
      createElement: () => ({ dataset: {}, style: {}, classList: { add() {} } }),
    },
    escapeHtml: value => String(value ?? ''),
    formatDiseaseClass: value => String(value ?? ''),
    encodedRecordImagePayload: () => 'image-payload',
    encodeInlineValue: value => String(value ?? ''),
    getPrimaryDetection: record => record.detections[0] || { class: 'Unknown', confidence: 0 },
    getRecordKey: record => record.id,
    renderRecommendationCardHtml: (_snapshot, details) => `location:${details.location}`,
  };
  context.window = { DetectionOverlay: { mount() {} } };
  vm.createContext(context);
  const helperStart = source.indexOf('function getEmbeddedRecordGps');
  const helperEnd = source.indexOf('function metadataPinRecordId', helperStart);
  const historyStart = source.indexOf('window.filterVerifiedHistory');
  const historyEnd = source.indexOf('// Verified history is deliberately separate', historyStart);
  vm.runInContext(`${source.slice(helperStart, helperEnd)}\nlet verifiedHistoryRecords = [];\n${source.slice(historyStart, historyEnd)}`, context);
  const records = [
    {
      id: 'with-gps', verified_by_role: 'expert', gps_source: 'exif', lat: 7.2, lng: 125.6,
      detections: [{ class: 'Bud Rot', confidence: .9 }], recommendation_snapshot: { recommendations: {} },
    },
    {
      id: 'without-gps', verified_by_role: 'expert',
      detections: [{ class: 'Bud Rot', confidence: .9 }], recommendation_snapshot: { recommendations: {} },
    },
  ];

  assert.doesNotThrow(() => vm.runInContext(`verifiedHistoryRecords = ${JSON.stringify(records)}; window.filterVerifiedHistory();`, context));
  assert.equal(panels.length, 2);
  assert.match(panels[0].innerHTML, /location:7\.20000, 125\.60000/);
  assert.match(panels[1].innerHTML, /location:/);
});

test('View Recommendation opens and closes a saved expert recommendation', () => {
  const classChanges = [];
  const panel = {
    dataset: { loaded: '1' },
    innerHTML: '<div>Saved expert recommendation</div>',
    style: { display: 'none' },
    closest: () => ({ classList: { add: value => classChanges.push(`add:${value}`), remove: value => classChanges.push(`remove:${value}`) } }),
  };
  const context = {
    window: {},
    document: { getElementById: id => id === 'record-recommendation-history-with-gps' ? panel : null },
    showToast: () => assert.fail('saved recommendation should not show an error'),
  };
  vm.createContext(context);
  const start = source.indexOf('window.toggleRecordRecommendation');
  const end = source.indexOf('// ── Drone', start);
  vm.runInContext(source.slice(start, end), context);

  const button = { textContent: 'View recommendation' };
  context.window.toggleRecordRecommendation('history-with-gps', button);
  assert.equal(panel.style.display, 'block');
  assert.equal(button.textContent, 'Hide Recommendation');
  context.window.toggleRecordRecommendation('history-with-gps', button);
  assert.equal(panel.style.display, 'none');
  assert.equal(button.textContent, 'Recommendation');
  assert.deepEqual(classChanges, ['add:history-recommendation-open', 'remove:history-recommendation-open']);
});
