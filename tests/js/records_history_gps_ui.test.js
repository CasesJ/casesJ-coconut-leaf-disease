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
