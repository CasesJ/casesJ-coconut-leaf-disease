const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const test = require('node:test');
const source = fs.readFileSync(require('node:path').join(__dirname, '../../static/app.js'), 'utf8');

function harness() {
  const elements = new Map();
  const element = id => {
    if (!elements.has(id)) elements.set(id, { value: '', disabled: false });
    return elements.get(id);
  };
  const context = {
    window: {}, currentToken: 'token', currentUserIsExpert: true,
    expertQueueRecords: [], getCachedExpertRecords: () => null,
    document: { getElementById: element, querySelector: () => element('save') },
    openExpertRecommendationEditor() {}, setExpertRecommendationDisease() {},
    showToast() {}, console, Date,
    fetch: () => { throw new Error('Unexpected request'); },
  };
  const start = source.indexOf('window.loadExpertRecommendation =');
  const end = source.indexOf('window.editExpertRecommendation =', start);
  vm.runInNewContext('let expertRecommendationLoadId = 0; const expertRecommendationCache = new Map();\n' + source.slice(start, end), context);
  return { context, element, load: context.window.loadExpertRecommendation };
}

test('saved record advice is ready immediately without fetching defaults', async () => {
  const h = harness();
  h.context.expertQueueRecords = [{ user_id: 'u', id: 'r', recommendation_snapshot: {
    recommendations: { fertilizer: 'Saved advice' }, note: 'Expert note',
  } }];
  await h.load('stem bleeding', .8, { userId: 'u', recordId: 'r' });
  assert.equal(h.element('expert-fertilizer').value, 'Saved advice');
  assert.equal(h.element('expert-note').value, 'Expert note');
  assert.equal(h.element('save').disabled, false);
});

test('late responses cannot replace the currently selected review', async () => {
  const h = harness();
  const pending = [];
  h.context.fetch = () => new Promise(resolve => pending.push(resolve));
  const first = h.load('stem bleeding', .8);
  assert.equal(h.element('save').disabled, true);
  const second = h.load('bud rot', .9);
  pending[1]({ ok: true, json: async () => ({ recommendations: { fertilizer: 'Second' } }) });
  await second;
  pending[0]({ ok: true, json: async () => ({ recommendations: { fertilizer: 'First' } }) });
  await first;
  assert.equal(h.element('expert-fertilizer').value, 'Second');
  await h.load('bud rot', .9);
  assert.equal(pending.length, 2, 'repeat opens reuse recently fetched advice');
});

test('recommendation preview uses the selected optimized thumbnail when switching JPEG, PNG, and DJI rows', async () => {
  const previews = [];
  const recommendations = [];
  const jpeg = {
    user_id: 'farmer-2', id: 'jpeg-record', filename: 'leaf.jpg',
    thumbnail_url: '/expert/media/detections/farmer-2/jpeg-record/thumbnail',
    image_url: '/expert/media/detections/farmer-2/jpeg-record/original',
  };
  const png = {
    user_id: 'farmer-2', id: 'png-record', filename: 'leaf.png',
    thumbnail_url: '/expert/media/detections/farmer-2/png-record/thumbnail',
    image_url: '/expert/media/detections/farmer-2/png-record/original',
  };
  const dji = {
    user_id: 'farmer-2', id: 'dji-mpo-derived', filename: 'drone-image.jpg',
    thumbnail_url: '/expert/media/detections/farmer-2/dji-mpo-derived/thumbnail',
    image_url: '/expert/media/detections/farmer-2/dji-mpo-derived/original',
    annotated_image_url: '/expert/media/detections/farmer-2/dji-mpo-derived/annotated',
    detections: [{ class: 'Bud Rot' }],
  };
  const context = {
    window: {}, currentToken: 'token', currentUserIsExpert: true,
    expertQueueRecords: [jpeg, png, dji], getCachedExpertRecords: () => null,
    setExpertRecommendationPreview: (urls, label, record) => { previews.push({ urls, label, record }); },
    loadExpertRecommendation: async (...args) => { recommendations.push(args); },
  };
  const start = source.indexOf('window.editExpertRecommendation =');
  const end = source.indexOf('window.saveExpertRecommendation =', start);
  vm.runInNewContext(source.slice(start, end), context);

  for (const record of [jpeg, png, dji]) {
    await context.window.editExpertRecommendation(
      'bud rot', .91, 'farmer-2', record.id,
      encodeURIComponent('/wrong/original'), encodeURIComponent('/wrong/annotated'), encodeURIComponent(record.filename)
    );
  }

  for (const [index, record] of [jpeg, png, dji].entries()) {
    assert.equal(previews[index].record, record, 'record identity comes from the selected queue row');
    assert.equal(previews[index].urls[0], record.thumbnail_url, 'optimized queue preview is the first request');
    assert.equal(previews[index].urls[1], record.image_url, 'the original remains a fallback');
    assert.equal(previews[index].label, record.filename);
    assert.equal(recommendations[index][2].recordId, record.id);
  }
});

test('recommendation preview immediately reuses an already decoded queue thumbnail', async () => {
  const image = {
    dataset: {}, classList: { add() {}, remove() {} },
    addEventListener() {}, removeEventListener() {}, removeAttribute() {},
  };
  const empty = { textContent: '', classList: { add() {}, remove() {} } };
  const frame = { dataset: {} };
  const queueThumbnail = {
    dataset: {
      privateSrc: '/expert/media/detections/farmer-2/dji/thumbnail',
      privateState: 'loaded',
    },
    naturalWidth: 480, src: 'blob:already-loaded-thumbnail',
  };
  let privateFetches = 0;
  const context = {
    window: {
      DetectionOverlay: { mount() {} },
      setPrivateImageSource: async () => { privateFetches += 1; return true; },
    },
    document: {
      getElementById: id => ({
        'expert-recommendation-image': image,
        'expert-recommendation-image-empty': empty,
        'expert-recommendation-frame': frame,
      })[id],
      querySelectorAll: () => [queueThumbnail],
    },
  };
  const start = source.indexOf('function setExpertRecommendationPreview');
  const end = source.indexOf('window.openExpertRecommendationImage =', start);
  vm.runInNewContext(`${source.slice(start, end)}\nwindow.setPreviewForTest = setExpertRecommendationPreview;`, context);

  context.window.setPreviewForTest([queueThumbnail.dataset.privateSrc], 'drone-image.jpg', { detections: [] });
  await new Promise(resolve => setImmediate(resolve));

  assert.equal(image.src, queueThumbnail.src);
  assert.equal(privateFetches, 0, 'a visible queue thumbnail does not make a second protected-media request');
});
