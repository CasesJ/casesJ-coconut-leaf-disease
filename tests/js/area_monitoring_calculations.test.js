const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const calculations = require('../../static/area_monitoring_calculations.js');

test('summary totals diseased detections across every area', () => {
  const metrics = calculations.dashboardMetrics([
    { areaId: 1, name: 'Area 1', diseased: 1, samples: 1 },
    { areaId: 2, name: 'Area 2', diseased: 0, samples: 0 },
    { areaId: 3, name: 'Area 3', diseased: 0, samples: 0 },
    { areaId: 4, name: 'Area 4', diseased: 3, samples: 3 },
  ]);

  assert.equal(metrics.totalDiseasedDetections, 4);
  assert.equal(metrics.priorityArea, 'Area 4');
});

test('priority area uses diseased detection counts and the lowest area number on ties', () => {
  assert.equal(calculations.priorityArea([
    { areaId: 1, name: 'Area 1', diseased: 1 },
    { areaId: 4, name: 'Area 4', diseased: 3 },
  ]), 'Area 4');
  assert.equal(calculations.priorityArea([
    { areaId: 3, name: 'Area 3', diseased: 2 },
    { areaId: 1, name: 'Area 1', diseased: 2 },
  ]), 'Area 1');
  assert.equal(calculations.priorityArea([
    { areaId: 1, name: 'Area 1', diseased: 0 },
    { areaId: 2, name: 'Area 2', diseased: 0 },
  ]), 'None');
});

test('healthy detections are excluded while a disease finding makes a record diseased', () => {
  const isHealthy = label => String(label).toLowerCase() === 'healthy';
  assert.equal(calculations.hasDiseasedDetection([{ class: 'Healthy' }], isHealthy), false);
  assert.equal(calculations.hasDiseasedDetection([{ class: 'Healthy' }, { class: 'Bud Rot' }], isHealthy), true);
  assert.equal(calculations.hasDiseasedDetection([], isHealthy), false);
});

test('common disease counts every non-healthy detection and resolves ties predictably', () => {
  const isHealthy = label => String(label).toLowerCase() === 'healthy';
  assert.deepEqual(calculations.diseaseDetections([
    { class: 'Healthy' }, { class: 'Bud Rot' }, { disease_name: 'Bud Rot' }, { class: 'Leaf Spot' },
  ], isHealthy), ['Bud Rot', 'Bud Rot', 'Leaf Spot']);
  assert.equal(calculations.commonDisease({ 'Leaf Spot': 2, 'Bud Rot': 2 }), 'Bud Rot');
  assert.equal(calculations.commonDisease({}), 'No disease detected');
});

test('each refreshed area snapshot produces current totals without prevalence', () => {
  const first = calculations.dashboardMetrics([{ areaId: 1, name: 'Area 1', diseased: 1 }]);
  const refreshed = calculations.dashboardMetrics([{ areaId: 1, name: 'Area 1', diseased: 2 }]);
  assert.equal(first.totalDiseasedDetections, 1);
  assert.equal(refreshed.totalDiseasedDetections, 2);
  assert.equal('diseasePrevalence' in refreshed, false);
});

test('area monitoring renders counts once per card and no prevalence percentage', () => {
  const source = fs.readFileSync(path.join(__dirname, '../../static/app.js'), 'utf8');
  const start = source.indexOf('window.loadAreaMonitoring');
  const body = source.slice(start, source.indexOf('\nfunction flyTo', start));
  assert.match(body, /<h4>Total Diseased Detections<\/h4>/);
  assert.match(body, /<div class="area-main-label">Diseased Detections<\/div>/);
  assert.match(body, /\$\{area\.samples\}<\/strong>Mapped locations/);
  assert.match(body, /Common disease detected/);
  assert.doesNotMatch(body, /Severity status|area\.severity/);
  assert.doesNotMatch(body, /Disease Prevalence|prevalenceLabel|area-bar|% disease prevalence/);
});

test('verified history exposes expert recommendations only after expert verification', () => {
  const source = fs.readFileSync(path.join(__dirname, '../../static/app.js'), 'utf8');
  const start = source.indexOf('window.filterVerifiedHistory');
  const body = source.slice(start, source.indexOf('// Verified history is deliberately separate', start));
  assert.match(body, /View recommendation/);
  assert.match(body, /record\.verified_by_role/);
  assert.match(body, /recommendationSnapshot\.recommendations/);
});
