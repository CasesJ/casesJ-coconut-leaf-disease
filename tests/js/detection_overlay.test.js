const assert = require('node:assert/strict');
const test = require('node:test');

const overlay = require('../../static/detection_overlay.js');

function fakeNode() {
  return {
    attributes: {},
    children: [],
    setAttribute(name, value) { this.attributes[name] = value; },
    appendChild(child) { this.children.push(child); },
    replaceChildren(...children) { this.children = children; },
  };
}

test('metadata overlay uses image coordinates and confidence labels', () => {
  const document = { createElementNS: () => fakeNode() };
  const svg = { ...fakeNode(), ownerDocument: document };

  overlay.draw(svg, [{
    class: 'Bud Rot',
    confidence: 0.82,
    bbox: [10, 20, 900, 450],
    bbox_normalized: [0.1, 0.2, 0.9, 0.9],
  }], 1000, 500);

  assert.equal(svg.attributes.viewBox, '0 0 1000 500');
  assert.equal(svg.attributes.preserveAspectRatio, 'xMidYMid meet');
  const rectangle = svg.children.find(node => node.attributes.stroke === '#e2433a');
  assert.equal(rectangle.attributes.x, '100');
  assert.equal(rectangle.attributes.y, '100');
  assert.equal(rectangle.attributes.width, '800');
  assert.equal(rectangle.attributes.height, '350');
  assert.ok(svg.children.some(node => node.textContent === 'Bud Rot 82%'));
});

test('raw original coordinates scale over a reduced storage derivative', () => {
  const document = { createElementNS: () => fakeNode() };
  const svg = { ...fakeNode(), ownerDocument: document };

  overlay.draw(svg, [{ class: 'Bud Rot', confidence: 0.8, bbox: [500, 250, 2500, 1250] }],
    800, 600, { width: 4000, height: 3000 });

  const rectangle = svg.children.find(node => node.attributes.stroke === '#e2433a');
  assert.equal(rectangle.attributes.x, '100');
  assert.equal(rectangle.attributes.y, '50');
  assert.equal(rectangle.attributes.width, '400');
  assert.equal(rectangle.attributes.height, '200');
});

test('overlay markup retains normalized detections and protected image paths', () => {
  const html = overlay.frameHtml('/media/detections/record-1/original', {
    filename: 'leaf.jpg',
    detections: [{ class: 'Pestalotiopsis', confidence: 0.9, bbox_normalized: [0, 0, 1, 1] }],
    image_dimensions: { width: 1600, height: 900 },
  });

  assert.ok(html.includes('data-private-src="/media/detections/record-1/original"'));
  assert.match(html, /bbox_normalized/);
  assert.match(html, /detection-overlay-svg/);
});
