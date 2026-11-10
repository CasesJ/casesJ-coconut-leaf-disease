const assert = require('node:assert/strict');
const fs = require('node:fs');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync('static/app.js', 'utf8');

function section(start, end) {
  const from = source.indexOf(start);
  const to = source.indexOf(end, from);
  assert.notEqual(from, -1, `missing ${start}`);
  assert.notEqual(to, -1, `missing ${end}`);
  return source.slice(from, to);
}

test('routine map refreshes preserve the current camera', () => {
  const refresh = section('function refreshMapPins()', 'let mapLogRenderTimer');
  const filters = section('window.applyMapFilters =', 'window.viewDetection =');
  const farmLocation = section('function applyFarmLocation(center)', 'async function loadFarmLocation()');

  assert.match(refresh, /applyMapFilters\(\{ focus: false \}\)/);
  assert.match(filters, /if \(focus\) focusFilteredPins\(matchingEntries, area\)/);
  assert.doesNotMatch(farmLocation, /mainMap\.flyTo/);
});

test('By Area data renders only configured farm polygons', () => {
  const context = {};
  vm.runInNewContext(section('function buildAreaFeatures(areas)', 'function pointInFarmBoundary'), context);
  const polygon = id => ({ type: 'Polygon', coordinates: [[[id, 0], [id + .1, 0], [id + .1, .1], [id, .1], [id, 0]]] });
  const areas = [1, 2, 3, 4].map(id => ({
    name: `Area ${id}`, prevalence: 0, diseased: 0, samples: 0, color: '#94a3b8',
    minLng: id, minLat: 0, maxLng: id + .5, maxLat: .5,
    boundary_geojson: id === 1 || id === 3 ? polygon(id) : null,
  }));
  const features = context.buildAreaFeatures(areas);

  assert.equal(features.length, 2);
  assert.deepEqual(JSON.parse(JSON.stringify(features[0].geometry)), areas[0].boundary_geojson);
  assert.deepEqual(JSON.parse(JSON.stringify(features[1].geometry)), areas[2].boundary_geojson);
  assert.match(source, /Promise\.resolve\(loadFarmBoundaries\(\)\)\.finally\(\(\) => loadAreaMonitoring\(\)\)/);
});

test('area selection leaves the base outline source dedicated to the farm outline', () => {
  const selection = section('window.selectFarmArea =', '// Used by the monitoring cards');
  assert.doesNotMatch(selection, /lanticse-farm-src/);
});

test('the default farm location map has no placeholder outline', () => {
  const mapSource = section("mainMap.addSource('lanticse-farm-src'", '// Render the solid filled inner region');
  const farmLocation = section('function applyFarmLocation(center)', 'async function loadFarmLocation()');

  assert.match(mapSource, /FeatureCollection', 'features': \[\]/);
  assert.match(farmLocation, /source\.setData\(\{ type: 'FeatureCollection', features: \[\] \}\)/);
});
