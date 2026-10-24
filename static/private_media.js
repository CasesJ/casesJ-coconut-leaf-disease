/* Authenticated image delivery for owner-checked FastAPI media endpoints. */
(() => {
  const objectUrls = new Map();
  const pending = new WeakMap();
  const imageLoadHandlers = new WeakMap();
  let activeUid = null;
  let observer = null;
  let nextImageId = 0;

  function diagnostic(event, image, details = {}) {
    image.dataset.privateDebugId ||= `image-${++nextImageId}`;
    console.info('[private-media]', event, {
      imageId: image.dataset.privateDebugId,
      state: image.dataset.privateState || 'created',
      ...details,
    });
  }

  function routeKind(path) {
    if (path.startsWith('/expert/media/')) return 'expert';
    if (path.startsWith('/media/')) return 'farmer';
    if (path.startsWith('/static/')) return 'legacy';
    return 'unknown';
  }

  function removeImageLoadHandlers(image) {
    const handlers = imageLoadHandlers.get(image);
    if (!handlers) return;
    image.removeEventListener('load', handlers.onLoad);
    image.removeEventListener('error', handlers.onError);
    imageLoadHandlers.delete(image);
  }

  function revokeObjectUrl(url, reason, image = objectUrls.get(url)) {
    if (!url) return;
    if (image) {
      diagnostic('object-url:revoke', image, {
        reason,
        connected: image.isConnected,
        stillAssigned: image.src === url,
        complete: Boolean(image.complete),
        naturalWidth: image.naturalWidth || 0,
      });
    }
    URL.revokeObjectURL(url);
    objectUrls.delete(url);
  }

  function watchImageLoad(image, objectUrl) {
    removeImageLoadHandlers(image);
    const onLoad = () => {
      const dimensions = {
        width: image.naturalWidth || 0,
        height: image.naturalHeight || 0,
        complete: Boolean(image.complete),
      };
      image.dataset.privateState = dimensions.width > 0 ? 'loaded' : 'image-error';
      if (dimensions.width > 0) {
        diagnostic('image:load', image, dimensions);
      } else {
        image.dataset.privateError = 'decode';
        image.alt = 'Image unavailable (invalid image data)';
        diagnostic('image:error', image, { reason: 'zero-natural-width', ...dimensions });
      }
      removeImageLoadHandlers(image);
    };
    const onError = () => {
      image.dataset.privateState = 'image-error';
      image.dataset.privateError = 'decode';
      image.alt = 'Image unavailable (invalid image data)';
      diagnostic('image:error', image, {
        reason: 'browser-decode-failed',
        complete: Boolean(image.complete),
        naturalWidth: image.naturalWidth || 0,
      });
      removeImageLoadHandlers(image);
    };
    image.addEventListener('load', onLoad, { once: true });
    image.addEventListener('error', onError, { once: true });
  imageLoadHandlers.set(image, { onLoad, onError });
  }

  function signedInUser() {
    try { return window.firebase?.auth?.().currentUser || null; } catch (_) { return null; }
  }

  function clearPrivateMedia() {
    observer?.disconnect();
    [...objectUrls.entries()].forEach(([url, image]) => revokeObjectUrl(url, 'auth-reset', image));
    document.querySelectorAll('img[data-private-src]').forEach((image) => {
      removeImageLoadHandlers(image);
      image.removeAttribute('src');
      delete image.dataset.privateObjectUrl;
      image.dataset.privateState = 'auth-reset';
    });
    if (observer) document.querySelectorAll('img[data-private-src]').forEach((image) => observer.observe(image));
    activeUid = null;
  }

  function releaseImage(image) {
    observer?.unobserve(image);
    const objectUrl = image.dataset.privateObjectUrl;
    if (objectUrl) {
      revokeObjectUrl(objectUrl, 'image-node-removed', image);
      delete image.dataset.privateObjectUrl;
    }
    removeImageLoadHandlers(image);
    image.removeAttribute('src');
    image.dataset.privateState = 'released';
    pending.delete(image);
  }

  function markUnavailable(image, reason, label) {
    image.dataset.privateAlt ||= image.alt;
    image.dataset.privateError = reason;
    image.dataset.privateState = 'unavailable';
    image.alt = label;
    image.removeAttribute('src');
  }

  async function setPrivateImageSource(image, path) {
    if (!(image instanceof HTMLImageElement)) return false;
    const url = String(path || '').trim();
    if (!url) {
      markUnavailable(image, 'missing-url', 'No image reference available');
      console.warn('[private-media] Image record has no media URL');
      return false;
    }
    if (!url.startsWith('/') || url.startsWith('//')
        || !/^\/(media|expert\/media|static\/uploads|static\/annotated_uploads)\//.test(url)) {
      markUnavailable(image, 'unsupported-url', 'Image URL is not a protected media route');
      console.warn('[private-media] Refused a media URL outside the protected API');
      return false;
    }
    const user = signedInUser();
    if (!user) {
      markUnavailable(image, 'not-signed-in', 'Sign in to view this image');
      diagnostic('fetch:skipped', image, { reason: 'firebase-user-unavailable', route: routeKind(url) });
      return false;
    }
    if (activeUid && activeUid !== user.uid) clearPrivateMedia();
    activeUid = user.uid;
    image.dataset.privateAlt ||= image.alt;
    image.alt = image.dataset.privateAlt;
    delete image.dataset.privateError;
    observer?.unobserve(image);
    const previous = pending.get(image);
    if (previous?.path === url) return previous.promise;
    if (image.dataset.privateSrc === url && image.dataset.privateObjectUrl
        && ['loading', 'loaded'].includes(image.dataset.privateState)) return true;
    if (image.dataset.privateSrc !== url) image.dataset.privateSrc = url;
    if (image.dataset.privateObjectUrl) {
      revokeObjectUrl(image.dataset.privateObjectUrl, 'source-replaced', image);
      delete image.dataset.privateObjectUrl;
    }
    removeImageLoadHandlers(image);
    image.removeAttribute('src');
    image.dataset.privateState = 'fetching';
    const promise = (async () => {
      let objectUrl = null;
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 20000);
      try {
        const fetchWithToken = token => fetch(url, {
          headers: { Authorization: `Bearer ${token}` },
          cache: 'no-store',
          signal: controller.signal,
        });
        const token = await user.getIdToken();
        diagnostic('fetch:start', image, { route: routeKind(url), tokenPresent: Boolean(token) });
        let response = await fetchWithToken(token);
        if (response.status === 401) {
          diagnostic('fetch:refresh-token', image, { route: routeKind(url) });
          response = await fetchWithToken(await user.getIdToken(true));
        }
        const contentType = (response.headers.get('content-type') || '').split(';')[0].trim().toLowerCase();
        diagnostic('fetch:response', image, { route: routeKind(url), status: response.status, contentType });
        if (!response.ok) {
          image.dataset.privateError = String(response.status);
          image.dataset.privateState = 'fetch-error';
          image.alt = `Image unavailable (HTTP ${response.status})`;
          console.warn('[private-media] Protected image request failed', {
            imageId: image.dataset.privateDebugId, status: response.status, route: routeKind(url),
          });
          return false;
        }
        if (!['image/jpeg', 'image/png'].includes(contentType)) {
          image.dataset.privateError = `content-type:${contentType || 'missing'}`;
          image.dataset.privateState = 'response-error';
          image.alt = 'Image unavailable (invalid response type)';
          console.warn('[private-media] Protected image response was not an image', {
            imageId: image.dataset.privateDebugId, contentType, route: routeKind(url),
          });
          return false;
        }
        const blob = await response.blob();
        diagnostic('blob:created', image, { type: blob.type || '', size: blob.size });
        const blobType = String(blob.type || '').split(';')[0].trim().toLowerCase();
        if (blobType && !['image/jpeg', 'image/png'].includes(blobType)) {
          image.dataset.privateError = `blob-type:${blobType}`;
          image.dataset.privateState = 'response-error';
          image.alt = 'Image unavailable (invalid response type)';
          console.warn('[private-media] Protected image Blob has an invalid type', {
            imageId: image.dataset.privateDebugId, blobType, route: routeKind(url),
          });
          return false;
        }
        if (!image.isConnected || signedInUser()?.uid !== user.uid || image.dataset.privateSrc !== url) {
          image.dataset.privateState = 'stale-response';
          diagnostic('blob:discarded', image, { route: routeKind(url), connected: image.isConnected });
          return false;
        }
        objectUrl = URL.createObjectURL(blob);
        objectUrls.set(objectUrl, image);
        image.dataset.privateObjectUrl = objectUrl;
        image.dataset.privateState = 'loading';
        watchImageLoad(image, objectUrl);
        image.src = objectUrl;
        diagnostic('object-url:assigned', image, { route: routeKind(url), blobType: blob.type || '', blobSize: blob.size });
        return true;
      } catch (error) {
        image.dataset.privateError = controller.signal.aborted ? 'timeout' : 'network';
        image.dataset.privateState = 'fetch-error';
        image.alt = controller.signal.aborted ? 'Image request timed out. Reopen this record to retry.' : 'Image unavailable (network error)';
        if (objectUrl) {
          revokeObjectUrl(objectUrl, 'source-error', image);
          delete image.dataset.privateObjectUrl;
        }
        console.warn('[private-media] Protected image fetch failed', {
          imageId: image.dataset.privateDebugId,
          route: routeKind(url),
          errorType: error?.name || 'Error',
        });
        return false;
      } finally {
        clearTimeout(timeout);
        if (pending.get(image)?.path === url) pending.delete(image);
      }
    })();
    pending.set(image, { path: url, promise });
    return promise;
  }

  function hydratePrivateImages(root = document) {
    const images = [];
    if (root instanceof HTMLImageElement && root.dataset.privateSrc) images.push(root);
    root.querySelectorAll?.('img[data-private-src]').forEach((image) => images.push(image));
    images.forEach((image) => {
      if (image.closest?.('.monitor-comparison')) {
        image.loading = 'eager';
        void setPrivateImageSource(image, image.dataset.privateSrc);
        return;
      }
      image.loading = 'lazy';
      if (observer) observer.observe(image);
      else void setPrivateImageSource(image, image.dataset.privateSrc);
    });
  }

  function releaseRemovedNode(node) {
    if (node instanceof HTMLImageElement && node.dataset.privateSrc) releaseImage(node);
    node.querySelectorAll?.('img[data-private-src]').forEach(releaseImage);
  }

  window.setPrivateImageSource = setPrivateImageSource;
  window.loadPrivateMediaImages = hydratePrivateImages;
  window.clearPrivateMediaImages = clearPrivateMedia;
  const mutationObserver = new MutationObserver((changes) => {
    for (const change of changes) {
      if (change.type === 'attributes' && change.target instanceof HTMLImageElement) {
        hydratePrivateImages(change.target);
      }
      change.addedNodes.forEach((node) => {
        if (node.nodeType === Node.ELEMENT_NODE) hydratePrivateImages(node);
      });
      change.removedNodes.forEach((node) => {
        if (node.nodeType === Node.ELEMENT_NODE) releaseRemovedNode(node);
      });
    }
  });
  mutationObserver.observe(document.documentElement, {
    subtree: true, childList: true, attributes: true, attributeFilter: ['data-private-src'],
  });
  observer = typeof IntersectionObserver === 'function' ? new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (!entry.isIntersecting) return;
      observer.unobserve(entry.target);
      void setPrivateImageSource(entry.target, entry.target.dataset.privateSrc);
    });
  }, { rootMargin: '160px' }) : null;
  try {
    window.firebase?.auth?.().onAuthStateChanged((user) => {
      if (!user || (activeUid && activeUid !== user.uid)) clearPrivateMedia();
      if (user) queueMicrotask(() => hydratePrivateImages(document));
    });
  } catch (_) { /* Firebase may not have initialized before this script. */ }
})();
