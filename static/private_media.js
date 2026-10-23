/* Authenticated image delivery for owner-checked FastAPI media endpoints. */
(() => {
  const objectUrls = new Set();
  const pending = new WeakMap();
  let activeUid = null;
  let observer = null;

  function signedInUser() {
    try { return window.firebase?.auth?.().currentUser || null; } catch (_) { return null; }
  }

  function clearPrivateMedia() {
    observer?.disconnect();
    objectUrls.forEach((url) => URL.revokeObjectURL(url));
    objectUrls.clear();
    document.querySelectorAll('img[data-private-src]').forEach((image) => {
      image.removeAttribute('src');
      delete image.dataset.privateObjectUrl;
    });
    if (observer) document.querySelectorAll('img[data-private-src]').forEach((image) => observer.observe(image));
    activeUid = null;
  }

  function releaseImage(image) {
    observer?.unobserve(image);
    const objectUrl = image.dataset.privateObjectUrl;
    if (objectUrl) {
      URL.revokeObjectURL(objectUrl);
      objectUrls.delete(objectUrl);
      delete image.dataset.privateObjectUrl;
    }
    image.removeAttribute('src');
    pending.delete(image);
  }

  async function setPrivateImageSource(image, path) {
    if (!(image instanceof HTMLImageElement)) return false;
    const url = String(path || '').trim();
    if (!url || !url.startsWith('/') || url.startsWith('//')
        || !/^\/(media|expert\/media|static\/uploads|static\/annotated_uploads)\//.test(url)) {
      image.removeAttribute('src');
      return false;
    }
    const user = signedInUser();
    if (!user) {
      image.removeAttribute('src');
      return false;
    }
    if (activeUid && activeUid !== user.uid) clearPrivateMedia();
    activeUid = user.uid;
    observer?.unobserve(image);
    image.dataset.privateSrc = url;
    const previous = pending.get(image);
    if (previous?.path === url) return previous.promise;
    if (image.dataset.privateObjectUrl) {
      URL.revokeObjectURL(image.dataset.privateObjectUrl);
      objectUrls.delete(image.dataset.privateObjectUrl);
      delete image.dataset.privateObjectUrl;
    }
    image.removeAttribute('src');
    const promise = (async () => {
      try {
        const token = await user.getIdToken();
        const response = await fetch(url, {
          headers: { Authorization: `Bearer ${token}` },
          cache: 'no-store',
        });
        if (!response.ok) return false;
        const contentType = (response.headers.get('content-type') || '').split(';')[0].trim().toLowerCase();
        if (!['image/jpeg', 'image/png'].includes(contentType)) return false;
        const blob = await response.blob();
        if (!image.isConnected || signedInUser()?.uid !== user.uid || image.dataset.privateSrc !== url) return false;
        const objectUrl = URL.createObjectURL(blob);
        objectUrls.add(objectUrl);
        image.dataset.privateObjectUrl = objectUrl;
        image.src = objectUrl;
        return true;
      } catch (_) {
        return false;
      } finally {
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
