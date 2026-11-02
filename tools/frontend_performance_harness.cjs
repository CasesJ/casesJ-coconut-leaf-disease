// Node VM doubles measure application work, not browser paint or MapLibre WebGL.
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function harness(count = 200, sourcePath = path.join(__dirname, '../static/app.js')) {
  const counts = { writes: 0, renders: 0, heatmaps: 0, flights: 0, sessionWrites: 0, geocodes: 0 };
  const pins = Object.fromEntries(Array.from({ length: count }, (_, i) => [String(i), {
    lat: 7.301 + i / 1e7, lng: 125.631, label: 'Healthy', confidence: .9, source: 'Upload',
  }]));
  const storage = new Map();
  const pending = [];
  const source = { _data: { type: 'FeatureCollection', features: [] }, setData(data) { this._data = data; counts.heatmaps++; } };
  const map = { getSource: () => source, flyTo() { counts.flights++; }, fitBounds() { counts.flights++; } };
  class Marker {
    constructor() { this.element = { style: {} }; }
    setLngLat() { return this; } setPopup() { return this; } addTo() { return this; }
    remove() {} getElement() { return this.element; }
  }
  class Popup { setHTML() { return this; } addTo() { return this; } }
  class Bounds { extend() { return this; } }
  const events = { set innerHTML(value) { counts.renders++; } };
  const context = {
    console: { log() {}, warn() {}, error() {} },
    currentUser: { uid: 'farmer-a' }, currentToken: 'token', currentUserIsExpert: false,
    mapsReady: true, mainMap: map, miniMap: map, mainMarkers: [], miniMarkers: [], log: [],
    baseLat: 7.30158, baseLng: 125.63099,
    document: { addEventListener() {}, getElementById: id => id === 'events-list' ? events : null,
      createElement: () => ({ style: {} }) },
    addEventListener() {},
    localStorage: { getItem: () => null },
    sessionStorage: { getItem: key => storage.get(key) || null,
      setItem(key, value) { counts.sessionWrites++; storage.set(key, value); }, removeItem: key => storage.delete(key) },
    firebase: { database: () => ({ ref: () => ({
      once(event, callback) {
        const snapshot = { val: () => pins };
        const promise = Promise.resolve(callback ? callback(snapshot) : snapshot);
        pending.push(promise);
        return promise;
      },
      push() { counts.writes++; return Promise.resolve(); },
    }) }) },
    maplibregl: { Marker, Popup, LngLatBounds: Bounds },
    setTimeout, clearTimeout, AbortController,
    navigator: { onLine: true },
    fetch: async () => { counts.geocodes++; return { ok: true, json: async () => ({ address: {} }) }; },
  };
  context.window = context;
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(sourcePath, 'utf8'), context);
  return { context, counts, pins, storage, pending };
}

module.exports = { harness };
