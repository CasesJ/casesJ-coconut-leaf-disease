/* Render YOLO detections over the protected original image without storing a copy. */
(function attachDetectionOverlay(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.DetectionOverlay = api;
})(typeof window !== 'undefined' ? window : null, function createDetectionOverlay() {
  'use strict';

  const SVG_NS = 'http://www.w3.org/2000/svg';

  function escapeAttribute(value) {
    return String(value ?? '').replace(/[&<>"']/g, character => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    })[character]);
  }

  function dimensions(record) {
    const size = record?.image_dimensions || record?.original_image_dimensions || {};
    const width = Number(size.width || size.image_width);
    const height = Number(size.height || size.image_height);
    return Number.isFinite(width) && width > 0 && Number.isFinite(height) && height > 0
      ? { width, height }
      : null;
  }

  function createNode(document, name, attributes = {}) {
    const node = document.createElementNS(SVG_NS, name);
    Object.entries(attributes).forEach(([key, value]) => node.setAttribute(key, String(value)));
    return node;
  }

  function draw(svg, detections, width, height, sourceDimensions = null) {
    if (!svg || !(width > 0) || !(height > 0)) return;
    const document = svg.ownerDocument;
    svg.replaceChildren();
    svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
    svg.setAttribute('preserveAspectRatio', 'xMidYMid meet');
    const title = createNode(document, 'title');
    title.textContent = 'Annotated detection preview';
    svg.appendChild(title);

    // Keep the preview annotation legible but restrained at every image size.
    // vector-effect below keeps box edges stable on screen as the image scales.
    const strokeWidth = Math.max(3, Math.min(6, Math.min(width, height) * 0.006));
    const fontSize = Math.max(14, Math.min(22, Math.min(width, height) * 0.06));
    const padding = fontSize * 0.35;
    (Array.isArray(detections) ? detections : []).forEach(detection => {
      if (!detection || typeof detection !== 'object') return;
      const normalized = detection.bbox_normalized;
      const raw = detection.bbox;
      let box;
      if (Array.isArray(normalized) && normalized.length === 4) {
        box = normalized.map(Number).map((value, index) => value * (index % 2 === 0 ? width : height));
      } else if (Array.isArray(raw) && raw.length === 4) {
        const sourceWidth = Number(sourceDimensions?.width) || width;
        const sourceHeight = Number(sourceDimensions?.height) || height;
        box = raw.map((value, index) => Number(value)
          * ((index % 2 === 0 ? width : height) / (index % 2 === 0 ? sourceWidth : sourceHeight)));
      } else {
        return;
      }
      if (!box.every(Number.isFinite)) return;
      let [x1, y1, x2, y2] = box;
      x1 = Math.max(0, Math.min(width, x1));
      x2 = Math.max(0, Math.min(width, x2));
      y1 = Math.max(0, Math.min(height, y1));
      y2 = Math.max(0, Math.min(height, y2));
      if (x2 <= x1 || y2 <= y1) return;

      svg.appendChild(createNode(document, 'rect', {
        x: x1, y: y1, width: x2 - x1, height: y2 - y1,
        fill: 'rgba(226, 67, 58, 0.12)', stroke: '#e2433a',
        'stroke-width': strokeWidth, 'vector-effect': 'non-scaling-stroke',
      }));

      const label = String(detection.class || detection.label || 'Detection');
      const confidence = Number(detection.confidence);
      const confidenceLabel = Number.isFinite(confidence)
        ? ` ${Math.round(confidence * 100)}%`
        : '';
      const text = `${label}${confidenceLabel}`;
      const estimatedWidth = Math.min(width - x1, text.length * fontSize * 0.58 + padding * 2);
      const labelHeight = fontSize + padding * 2;
      const labelY = Math.max(0, y1 - labelHeight);
      svg.appendChild(createNode(document, 'rect', {
        x: x1, y: labelY, width: Math.max(estimatedWidth, fontSize * 2), height: labelHeight,
        rx: padding, fill: '#e2433a',
      }));
      const labelText = createNode(document, 'text', {
        x: x1 + padding, y: labelY + fontSize + padding * 0.1,
        fill: '#fff', 'font-size': fontSize,
        'font-family': 'Arial, sans-serif', 'font-weight': '700',
      });
      labelText.textContent = text;
      svg.appendChild(labelText);
    });
  }

  function frameHtml(imageUrl, record = {}, options = {}) {
    if (!imageUrl) return '';
    const className = options.className ? ` ${escapeAttribute(options.className)}` : '';
    const style = options.style ? ` style="${escapeAttribute(options.style)}"` : '';
    const sourceDetections = record.detections || record.predictions;
    const detections = JSON.stringify(Array.isArray(sourceDetections) ? sourceDetections : []);
    const size = dimensions(record);
    const dimensionsData = size ? JSON.stringify(size) : '';
    const source = options.direct
      ? `src="${escapeAttribute(imageUrl)}"`
      : `data-private-src="${escapeAttribute(imageUrl)}"`;
    return `<span class="detection-overlay-frame${className}" data-overlay-detections="${escapeAttribute(detections)}" data-overlay-dimensions="${escapeAttribute(dimensionsData)}"${style}><img data-overlay-image width="180" height="180" data-private-priority="${options.priority === 'high' ? 'high' : 'auto'}" ${source} alt="${escapeAttribute(options.alt || record.filename || 'Detection image')}" /><svg class="detection-overlay-svg" aria-label="YOLO bounding boxes and confidence labels"></svg></span>`;
  }

  function mount(root = document) {
    const frames = [];
    if (root?.matches?.('.detection-overlay-frame')) frames.push(root);
    root?.querySelectorAll?.('.detection-overlay-frame').forEach(frame => frames.push(frame));
    frames.forEach(frame => {
      if (frame.dataset.overlayReady === '1') return;
      const image = frame.querySelector('img[data-overlay-image]');
      const svg = frame.querySelector('.detection-overlay-svg');
      if (!image || !svg) return;
      frame.dataset.overlayReady = '1';
      const render = () => {
        const width = image.naturalWidth || 0;
        const height = image.naturalHeight || 0;
        if (!width || !height) return;
        let detections = [];
        try { detections = JSON.parse(frame.dataset.overlayDetections || '[]'); } catch (_) { detections = []; }
        let sourceDimensions = null;
        try {
          const size = JSON.parse(frame.dataset.overlayDimensions || '{}');
          sourceDimensions = dimensions({ image_dimensions: size });
        } catch (_) { sourceDimensions = null; }
        draw(svg, detections, width, height, sourceDimensions);
      };
      image.addEventListener('load', render);
      if (image.complete && image.naturalWidth) render();
    });
  }

  if (typeof document !== 'undefined' && typeof MutationObserver === 'function') {
    const observer = new MutationObserver(changes => {
      changes.forEach(change => change.addedNodes.forEach(node => {
        if (node.nodeType === 1) mount(node);
      }));
    });
    if (document.documentElement) observer.observe(document.documentElement, { childList: true, subtree: true });
  }

  return { frameHtml, mount, draw };
});
