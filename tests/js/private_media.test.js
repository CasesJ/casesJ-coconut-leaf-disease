const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(
  path.join(__dirname, '..', '..', 'static', 'private_media.js'),
  'utf8',
);

class FakeImage {
  constructor(imageEvent = 'load') {
    this.dataset = { privateSrc: '/media/detections/record-1/original' };
    this.alt = 'Detection image';
    this.isConnected = true;
    this.complete = false;
    this.naturalWidth = 0;
    this.naturalHeight = 0;
    this.imageEvent = imageEvent;
    this.listeners = new Map();
    this._src = '';
  }

  set src(value) {
    this._src = String(value);
    if (this._src.startsWith('blob:')) {
      queueMicrotask(() => this.dispatch(this.imageEvent));
    }
  }

  get src() { return this._src; }

  removeAttribute(name) {
    if (name === 'src') this._src = '';
  }

  addEventListener(type, listener) {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set());
    this.listeners.get(type).add(listener);
  }

  removeEventListener(type, listener) {
    this.listeners.get(type)?.delete(listener);
  }

  dispatch(type) {
    this.complete = true;
    if (type === 'load') {
      this.naturalWidth = 8;
      this.naturalHeight = 8;
    }
    for (const listener of [...(this.listeners.get(type) || [])]) listener({ type });
  }
}

function response({ status = 200, contentType = 'image/jpeg', blobType = contentType } = {}) {
  return {
    status,
    ok: status >= 200 && status < 300,
    headers: { get: name => name.toLowerCase() === 'content-type' ? contentType : null },
    blob: async () => ({ type: blobType, size: 128 }),
  };
}

function createHarness({ responses = [response()], imageEvent = 'load', tokenProvider } = {}) {
  const image = new FakeImage(imageEvent);
  const calls = [];
  const logs = [];
  const revoked = [];
  let objectUrlNumber = 0;
  let mutationCallback;
  const user = {
    uid: 'farmer-1',
    getIdToken: tokenProvider || (async () => 'test-bearer-token'),
  };
  class FakeMutationObserver {
    constructor(callback) { this.callback = callback; mutationCallback = callback; }
    observe() {}
    disconnect() {}
  }
  const fakeWindow = {
    firebase: { auth: () => ({
      currentUser: user,
      onAuthStateChanged: () => () => {},
    }) },
  };
  const context = {
    window: fakeWindow,
    document: {
      documentElement: {},
      querySelectorAll: () => [image],
    },
    HTMLImageElement: FakeImage,
    MutationObserver: FakeMutationObserver,
    Node: { ELEMENT_NODE: 1 },
    URL: {
      createObjectURL: blob => {
        assert.equal(blob.type, 'image/jpeg');
        return `blob:private-media-${++objectUrlNumber}`;
      },
      revokeObjectURL: url => revoked.push(url),
    },
    fetch: async (url, options) => {
      calls.push({ url, options });
      return responses.shift();
    },
    console: {
      info: (...args) => logs.push(args),
      warn: (...args) => logs.push(args),
    },
    queueMicrotask,
    AbortController,
    setTimeout,
    clearTimeout,
  };
  vm.runInNewContext(source, context, { filename: 'private_media.js' });
  return { image, calls, logs, revoked, window: fakeWindow, mutate: changes => mutationCallback(changes) };
}

function logged(logs, event) {
  return logs.some((entry) => entry[0] === '[private-media]' && entry[1] === event);
}

test('protected image uses Firebase bearer auth and stays sourced until cleanup', async () => {
  const harness = createHarness();
  const loaded = new Promise(resolve => harness.image.addEventListener('load', resolve));

  assert.equal(await harness.window.setPrivateImageSource(
    harness.image, '/media/detections/record-1/original',
  ), true);
  await loaded;

  assert.equal(harness.calls.length, 1);
  assert.equal(harness.calls[0].options.headers.Authorization, 'Bearer test-bearer-token');
  assert.equal(harness.calls[0].options.cache, 'no-store');
  assert.equal(harness.image.dataset.privateState, 'loaded');
  assert.equal(harness.image.src, 'blob:private-media-1');
  assert.deepEqual(harness.revoked, []);
  for (const event of ['fetch:start', 'fetch:response', 'blob:created', 'object-url:assigned', 'image:load']) {
    assert.equal(logged(harness.logs, event), true, `expected diagnostic ${event}`);
  }
  assert.doesNotMatch(JSON.stringify(harness.logs), /test-bearer-token|farmer-1|record-1/);

  harness.window.clearPrivateMediaImages();
  assert.deepEqual(harness.revoked, ['blob:private-media-1']);
  assert.equal(logged(harness.logs, 'object-url:revoke'), true);
});

test('expired Firebase token is refreshed once after a 401 response', async () => {
  const harness = createHarness({
    responses: [response({ status: 401 }), response()],
    tokenProvider: async forceRefresh => forceRefresh ? 'fresh-token' : 'stale-token',
  });

  assert.equal(await harness.window.setPrivateImageSource(
    harness.image, '/expert/media/detections/farmer-1/record-1/original',
  ), true);
  assert.deepEqual(harness.calls.map(call => call.options.headers.Authorization), [
    'Bearer stale-token', 'Bearer fresh-token',
  ]);
});

test('non-image HTTP 200 response is diagnosed before Blob or object URL creation', async () => {
  const harness = createHarness({
    responses: [response({ contentType: 'text/html', blobType: 'text/html' })],
  });

  assert.equal(await harness.window.setPrivateImageSource(
    harness.image, '/media/detections/record-1/original',
  ), false);
  assert.equal(harness.image.dataset.privateState, 'response-error');
  assert.equal(logged(harness.logs, 'fetch:response'), true);
  assert.equal(logged(harness.logs, 'blob:created'), false);
  assert.equal(harness.image.src, '');
});

test('browser image decode errors are diagnosed after the object URL is assigned', async () => {
  const harness = createHarness({ imageEvent: 'error' });

  assert.equal(await harness.window.setPrivateImageSource(
    harness.image, '/expert/media/detections/farmer-1/record-1/annotated',
  ), true);
  await new Promise(resolve => setImmediate(resolve));

  assert.equal(harness.image.dataset.privateState, 'image-error');
  assert.equal(harness.image.dataset.privateError, 'decode');
  assert.equal(logged(harness.logs, 'object-url:assigned'), true);
  assert.equal(logged(harness.logs, 'image:error'), true);
  assert.deepEqual(harness.revoked, []);
});

test('unexpected Blob MIME type is rejected before assigning its object URL', async () => {
  const harness = createHarness({
    responses: [response({ contentType: 'image/jpeg', blobType: 'application/json' })],
  });

  assert.equal(await harness.window.setPrivateImageSource(
    harness.image, '/media/detections/record-1/original',
  ), false);
  assert.equal(harness.image.dataset.privateState, 'response-error');
  assert.equal(logged(harness.logs, 'blob:created'), true);
  assert.equal(logged(harness.logs, 'object-url:assigned'), false);
});


test('hydrating an already loaded image preserves its URL without fetching again', async () => {
  const harness = createHarness();
  const url = harness.image.dataset.privateSrc;
  await harness.window.setPrivateImageSource(harness.image, url);
  await harness.window.setPrivateImageSource(harness.image, url);
  harness.window.loadPrivateMediaImages(harness.image);
  assert.equal(harness.calls.length, 1);
  assert.equal(harness.image.src, 'blob:private-media-1');
  assert.deepEqual(harness.revoked, []);
});


test('same-source attribute notifications and repeated hydration share the in-flight request', async () => {
  let resolveToken;
  const harness = createHarness({ tokenProvider: () => new Promise(resolve => { resolveToken = resolve; }) });
  const image = harness.image;
  const loading = harness.window.setPrivateImageSource(image, image.dataset.privateSrc);
  harness.mutate([{ type: 'attributes', target: image, addedNodes: [], removedNodes: [] }]);
  harness.window.loadPrivateMediaImages(image);
  resolveToken('test-token');
  assert.equal(await loading, true);
  harness.mutate([{ type: 'attributes', target: image, addedNodes: [], removedNodes: [] }]);
  assert.equal(harness.calls.length, 1);
  assert.deepEqual(harness.revoked, []);
  assert.equal(image.listeners.get('load').size, 0);
});

test('replacing a loaded thumbnail revokes its URL and fetches the replacement element', async () => {
  const harness = createHarness({ responses: [response(), response()] });
  const image = harness.image;
  await harness.window.setPrivateImageSource(image, image.dataset.privateSrc);
  image.isConnected = false;
  image.nodeType = 1;
  harness.mutate([{ type: 'childList', addedNodes: [], removedNodes: [image] }]);
  const replacement = new FakeImage();
  await harness.window.setPrivateImageSource(replacement, replacement.dataset.privateSrc);
  assert.equal(harness.calls.length, 2);
  assert.deepEqual(harness.revoked, ['blob:private-media-1']);
  assert.equal(image.src, '');
  assert.equal(replacement.src, 'blob:private-media-2');
});
