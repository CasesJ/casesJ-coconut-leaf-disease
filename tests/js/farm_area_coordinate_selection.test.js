const assert = require('node:assert/strict');
const fs = require('node:fs');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync('static/app.js', 'utf8');
const start = source.indexOf('async function loadFarmBoundaries()');
const end = source.indexOf('const DEFAULT_FARM_LOCATION', start);

assert.notEqual(start, -1, 'missing farm-boundary loader');
assert.notEqual(end, -1, 'missing farm-location defaults');

function polygon(areaId) {
  return {
    type: 'Polygon',
    coordinates: [[[125 + areaId, 7], [125 + areaId + .01, 7], [125 + areaId + .01, 7.01], [125 + areaId, 7.01], [125 + areaId, 7]]],
  };
}

function createContext(areas) {
  const elements = {
    'farm-name-input': {},
    'farm-lat-input': {},
    'farm-lng-input': {},
  };
  const context = {
    currentToken: 'test-token',
    currentUserIsExpert: false,
    selectedFarmArea: 1,
    farmBoundaries: areas,
    farmLocation: { latitude: 7.30158, longitude: 125.63099 },
    DEFAULT_FARM_LOCATION: { latitude: 7.30158, longitude: 125.63099 },
    boundaryDraft: null,
    boundaryDrawing: false,
    mapsReady: false,
    mainMap: null,
    document: { getElementById: id => elements[id] || null },
    clearVertexMarkers() {},
    renderFarmBoundaries() {},
    updateBoundaryStatus() {},
    loadAreaMonitoring() {},
    showToast() {},
    requestAnimationFrame() {},
    selectedBoundary() { return context.farmBoundaries.find(area => area.area_id === context.selectedFarmArea); },
    console,
  };
  context.window = context;
  vm.runInNewContext(source.slice(start, end), context);
  return { context, elements };
}

test('each saved farm area shows its own coordinates without disease detections', () => {
  const areas = [1, 2, 3, 4].map(areaId => ({
    area_id: areaId,
    farm_name: `Area ${areaId}`,
    center_latitude: 7 + areaId / 100,
    center_longitude: 125 + areaId / 100,
    boundary_geojson: polygon(areaId),
    detections: [],
  }));
  const originalPolygons = structuredClone(areas.map(area => area.boundary_geojson));
  const { context, elements } = createContext(areas);

  for (const area of areas) {
    context.selectFarmArea(area.area_id, { focus: false });
    assert.equal(Number(elements['farm-lat-input'].value), area.center_latitude);
    assert.equal(Number(elements['farm-lng-input'].value), area.center_longitude);
  }

  assert.deepEqual(JSON.parse(JSON.stringify(areas.map(area => area.boundary_geojson))), originalPolygons);
});

test('a farm-area refresh retains the selected area coordinates', async () => {
  const savedAreas = [1, 2, 3, 4].map(areaId => ({
    area_id: areaId,
    center_latitude: 7.2 + areaId / 100,
    center_longitude: 125.6 + areaId / 100,
    boundary_geojson: polygon(areaId),
  }));
  const { context, elements } = createContext([]);
  context.selectedFarmArea = 4;
  context.fetch = async () => ({ ok: true, json: async () => ({ areas: savedAreas }) });

  await context.loadFarmBoundaries();

  assert.equal(Number(elements['farm-lat-input'].value), savedAreas[3].center_latitude);
  assert.equal(Number(elements['farm-lng-input'].value), savedAreas[3].center_longitude);
  assert.deepEqual(JSON.parse(JSON.stringify(context.farmBoundaries)), savedAreas);
});

test('saving a polygon persists its selected center coordinates and reselects the area', async () => {
  const area = { area_id: 2, farm_name: 'Area 2', boundary_geojson: null, initial_boundary_confirmed: false, changes_remaining: 2 };
  const { context, elements } = createContext([area]);
  context.selectedFarmArea = 2;
  context.boundaryDraft = polygon(2);
  context.confirm = () => true;
  elements['farm-name-input'].value = 'Saved Area 2';
  elements['farm-lat-input'].value = '7.4567';
  elements['farm-lng-input'].value = '125.6789';
  let request;
  context.fetch = async (url, options) => {
    request = { url, ...options, body: JSON.parse(options.body) };
    return { ok: true, json: async () => ({ ...area, ...request.body }) };
  };

  await context.saveFarmBoundary();

  assert.equal(request.url, '/farm/areas/2');
  assert.equal(request.body.center_latitude, 7.4567);
  assert.equal(request.body.center_longitude, 125.6789);
  assert.equal(Number(elements['farm-lat-input'].value), 7.4567);
  assert.equal(Number(elements['farm-lng-input'].value), 125.6789);
});
