const assert = require('node:assert/strict');
const test = require('node:test');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const { harness } = require('../../tools/frontend_performance_harness.cjs');

test('existing map-pin persistence has the same-version RTDB SDK and backend database URL', () => {
  const html = fs.readFileSync(path.join(__dirname, '../../static/index.html'), 'utf8');
  const backend = fs.readFileSync(path.join(__dirname, '../../firebase_config.py'), 'utf8');
  const authVersion = html.match(/firebasejs\/([^/]+)\/firebase-auth-compat\.js/)[1];
  assert.ok(html.includes(`firebasejs/${authVersion}/firebase-database-compat.js`));
  const databaseUrl = html.match(/databaseURL:\s*"([^"]+)"/)[1];
  assert.ok(backend.includes(databaseUrl));
});

test('restoring pins preserves every location without writes and batches map work', async () => {
  const h = harness(75);
  await h.context.loadSavedMapPins('farmer-a');
  assert.equal(h.context.log.length, 75);
  assert.equal(h.counts.writes, 0);
  assert.equal(h.counts.heatmaps, 1);
  assert.equal(h.counts.flights, 1);
  assert.equal(h.counts.sessionWrites, 1);
  assert.equal(JSON.parse(h.storage.get('farmPins')).length, 75);
});

test('a new pin still autosaves exactly once', async () => {
  const h = harness(0);
  h.context.addPin(7.301, 125.631, 'Healthy', .9, 'Upload');
  await Promise.resolve();
  assert.equal(h.counts.writes, 1);
  assert.equal(h.context.log.length, 1);
});

test('map pin colors match the detection color key', () => {
  const h = harness(0);
  for (const [label, color] of Object.entries({
    Healthy: '#22c55e', Pestalotiopsis: '#06b6d4', 'Bud Rot': '#eab308', 'Stem Bleeding': '#dc2626',
  })) {
    h.context.addPin(7.301, 125.631, label, .9, 'Upload');
    assert.equal(h.context.log[0].color, color);
  }
});

test('metadata-backed detection records restore pins without writes or geocoding', async () => {
  const h = harness(0);
  await h.context.restoreMetadataRecordPins([{ id: 'metadata-record', detections: [{ class: 'Bud Rot', confidence: .9 }], image_metadata: { gps: { latitude: 7.301, longitude: 125.631 } } }]);
  assert.equal(h.context.log.length, 1);
  assert.equal(h.context.log[0].source, 'Embedded image metadata');
  assert.equal(h.counts.writes, 0);
  assert.equal(h.counts.geocodes, 0);
});

test('offline map restoration does not wait for Firebase', async () => {
  const h = harness(0);
  h.context.navigator.onLine = false;
  h.context.firebase.database = () => assert.fail('Offline restoration must not contact Firebase');
  await h.context.loadSavedMapPins('farmer-a');
});

test('clearing pins during chunked restoration cancels the remaining work', async () => {
  const h = harness(100);
  h.context.showToast = () => {};
  const pending = h.context.loadSavedMapPins('farmer-a');
  await Promise.resolve();
  h.context.clearPins();
  await pending;
  assert.equal(h.context.log.length, 0);
  assert.equal(h.counts.writes, 0);
  assert.equal(h.storage.has('farmPins'), false);
});

test('pins added before map initialization get their markers once maps are ready', async () => {
  const h = harness(0);
  h.context.mapsReady = false;
  h.context.addPin(7.301, 125.631, 'Healthy', .9, 'Upload');
  assert.equal(h.context.mainMarkers.length, 0);
  h.context.mapsReady = true;
  await h.context.attachPendingMapPins();
  await h.context.attachPendingMapPins();
  assert.equal(h.context.mainMarkers.length, 1);
  assert.equal(h.context.miniMarkers.length, 1);
  assert.equal(h.counts.writes, 1);
});

test('map initialization is deferred and repeated navigation reuses both maps', () => {
  const h = harness(0);
  let created = 0;
  h.context.maplibregl.Map = class { constructor() { created++; } on() {} };
  assert.equal(created, 0);
  h.context.initializeMaps();
  h.context.initializeMaps();
  assert.equal(created, 2);
});

test('concurrent geocoding for one coordinate shares one request', async () => {
  const h = harness(0);
  let complete, requests = 0;
  h.context.fetch = () => { requests++; return new Promise(resolve => { complete = resolve; }); };
  const first = h.context.reverseGeocode(8, 126);
  const second = h.context.reverseGeocode(8, 126);
  complete({ ok: true, json: async () => ({ address: { city: 'Test City' } }) });
  assert.deepEqual(await Promise.all([first, second]), ['Test City', 'Test City']);
  assert.equal(await h.context.reverseGeocode(8, 126), 'Test City');
  assert.equal(requests, 1);
});

test('offline geocoding returns immediately and can retry after reconnection', async () => {
  const h = harness(0);
  h.context.navigator.onLine = false;
  assert.equal(await h.context.reverseGeocode(8, 126), '8.0000, 126.0000');
  assert.equal(h.counts.geocodes, 0);
  h.context.navigator.onLine = true;
  await h.context.reverseGeocode(8, 126);
  assert.equal(h.counts.geocodes, 1);
});

for (const expert of [false, true]) {
  test(`${expert ? 'expert' : 'farmer'} records coalesce, survive offline TTL and refresh when reconnected`, async () => {
    const h = harness(0);
    h.context.currentUserIsExpert = expert;
    h.context.showToast = () => {};
    let requests = 0;
    h.context.fetch = async () => { requests++; return { ok: true, json: async () => ({ records: [{ id: requests }] }) }; };
    const fetchRecords = expert ? h.context.fetchExpertRecords : h.context.fetchUserRecords;
    const [first, second] = await Promise.all([fetchRecords(), fetchRecords()]);
    assert.equal(first, second);
    assert.equal(requests, 1);
    vm.runInContext(`${expert ? 'expertRecordsCache' : 'userRecordsCache'}.loadedAt = 0`, h.context);
    h.context.navigator.onLine = false;
    assert.equal(await fetchRecords({ force: true }), first);
    assert.equal(requests, 1);
    h.context.navigator.onLine = true;
    const refreshed = await fetchRecords();
    assert.equal(requests, 2);
    assert.notEqual(refreshed, first);
  });
}

test('invalidating records rejects an older response instead of repopulating the cache', async () => {
  const h = harness(0);
  let complete;
  h.context.fetch = () => new Promise(resolve => { complete = resolve; });
  const request = h.context.fetchUserRecords();
  h.context.clearUserRecordsCache();
  complete({ ok: true, json: async () => ({ records: [{ id: 'old' }] }) });
  await assert.rejects(request, /Records changed/);
  assert.equal(h.context.getCachedUserRecords(), null);
});

test('switching accounts never exposes the previous cached records', async () => {
  const h = harness(0);
  h.context.fetch = async () => ({ ok: true, json: async () => ({ records: [{ id: 'private' }] }) });
  await h.context.fetchUserRecords();
  h.context.currentUser = { uid: 'farmer-b' };
  h.context.navigator.onLine = false;
  await assert.rejects(h.context.fetchUserRecords(), /Offline/);
});

test('unchanged dashboard charts retain instances and changed data rebuilds charts', () => {
  const h = harness(0);
  let renders = 0;
  h.context.aggregateWeeklyDetections = records => ({ labels: records });
  h.context.aggregateClassDistribution = records => ({ data: records });
  h.context.renderWeeklyTrendChart = () => { renders++; };
  h.context.renderHealthDistributionChart = () => { renders++; };
  h.context.renderDetectionColorKey = () => {};
  h.context.renderAnalyticsCharts([1]);
  h.context.renderAnalyticsCharts([1]);
  assert.equal(renders, 2);
  h.context.renderAnalyticsCharts([2]);
  assert.equal(renders, 4);
});
