// Tree identity evidence is independent of disease inference and outcome review.
const identityMedia = { urls: [], controller: null };

function monitoringIdentityStatus(review) {
  return ({ confirmed: 'Identity confirmed', uncertain: 'Identity uncertain', mismatch: 'Identity mismatch' })[review?.status] || 'Identity not reviewed';
}

function monitoringIdentityBase(item) {
  return currentUserIsExpert
    ? `/expert/monitoring/cases/${encodeURIComponent(item.farmer_id)}/${encodeURIComponent(item.id)}`
    : `/monitoring/cases/${encodeURIComponent(item.id)}`;
}

function monitoringClearIdentityMedia() {
  identityMedia.controller?.abort();
  identityMedia.urls.forEach(url => URL.revokeObjectURL(url));
  identityMedia.urls = [];
  document.querySelectorAll('[data-tree-media] img').forEach(img => img.removeAttribute('src'));
}

window.monitoringResetIdentity = function () {
  monitoringClearIdentityMedia();
  monitoringState.cases = [];
  monitoringState.overview = null;
  monitoringState.selected = null;
  const detail = document.getElementById('monitor-detail');
  if (detail) detail.textContent = '';
  const list = document.getElementById('monitor-case-list');
  if (list) list.textContent = '';
};

function monitoringIdentityPanel(overview, selected) {
  const item = overview.case, baseline = item.tree_reference, photo = selected?.tree_reference;
  const review = selected?.identity_review;
  const gps = selected?.identity_gps_evidence;
  const canConfirm = Boolean(baseline && photo);
  const base = monitoringIdentityBase(item);
  const figure = (reference, title, followUpId = null) => {
    const path = reference ? `${base}/tree-references/${encodeURIComponent(reference.id)}${followUpId ? `?follow_up_id=${encodeURIComponent(followUpId)}` : ''}` : '';
    return `<figure><div class="monitor-image-fallback" ${reference ? `data-tree-media="${monitoringText(path)}" data-tree-alt="${monitoringText(title)}"` : ''}>${reference ? 'Loading protected photograph…' : 'Reference photo missing'}</div>
      <figcaption>${monitoringText(title)}</figcaption>${reference ? `<p class="monitor-muted">Registered ${monitoringDateTime(reference.created_at)}</p>` : ''}</figure>`;
  };
  const gpsLabel = ({ within_tolerance: 'Within tolerance', outside_tolerance: 'Outside tolerance',
    inconclusive_accuracy: 'Inconclusive: GPS uncertainty overlaps tolerance' })[gps?.status] || 'GPS unavailable';
  const evidenceLabel = value => ({ baseline_whole_tree: 'baseline whole-tree photo', baseline_disease: 'baseline disease record',
    follow_up_whole_tree: 'follow-up whole-tree photo', follow_up_disease: 'follow-up disease photo' })[value] || 'unknown source';
  const coordinates = (value, label) => value ? `<span>${label}: ${Number(value.latitude).toFixed(6)}, ${Number(value.longitude).toFixed(6)} (${monitoringText(value.source)})</span>` : `<span>${label}: unavailable</span>`;
  const baselineForm = currentUserIsExpert && !baseline ? `<form id="monitor-tree-reference-form" class="monitor-form">
    <label>Register baseline whole-tree photograph<input id="monitor-tree-baseline-file" type="file" accept="image/jpeg,image/png" required></label>
    <p class="monitor-muted">Choose the correct tree before saving. This baseline is permanent and separate from the disease close-up. Include the trunk, crown, and recognizable surroundings.</p>
    <button class="btn btn-p" type="submit">Register tree reference</button></form>` : '';
  const reviewForm = currentUserIsExpert && selected ? `<form id="monitor-identity-review-form" class="monitor-form" data-follow-up-id="${monitoringText(selected.id)}" data-revision="${review?.revision || 0}" data-baseline-id="${monitoringText(baseline?.id || '')}">
    <label>Tree identity decision<select id="monitor-identity-status" required><option value="">Choose a decision</option>
      <option value="confirmed" ${canConfirm ? '' : 'disabled'}>Confirmed</option><option value="uncertain">Uncertain</option><option value="mismatch">Mismatch</option></select></label>
    ${canConfirm ? '' : '<p class="monitor-muted">Both whole-tree photographs are required to confirm identity. Historical cases remain available.</p>'}
    <label>Explanation (optional)<textarea id="monitor-identity-explanation" rows="3" maxlength="2000" placeholder="Describe tree features, surroundings, or uncertainty"></textarea></label>
    <button class="btn btn-p" type="submit">Save identity review</button></form>` : '';
  return `<section class="monitor-card monitor-identity" aria-labelledby="monitor-identity-title">
    <div class="monitor-history-head"><div><h3 id="monitor-identity-title" class="monitor-section-heading">Tree identity verification</h3>
    <p class="monitor-muted">Tree ${monitoringText(item.tree_id)}${selected ? ` · Inspection ${monitoringDateTime(selected.created_at)}` : ''}</p></div>
    <span class="monitor-pill ${review?.status === 'mismatch' ? 'overdue' : review?.status === 'confirmed' ? '' : 'review'}">${monitoringIdentityStatus(review)}</span></div>
    <p class="monitor-muted">Compare the trunk, crown, and surrounding landmarks. Matching Tree IDs and nearby GPS coordinates do not confirm identity. This decision is separate from the treatment outcome.</p>
    <div class="monitor-comparison">${figure(baseline, 'Baseline whole-tree reference')}${figure(photo, 'Selected follow-up whole-tree reference', selected?.id)}</div>
    <div class="monitor-gps-evidence"><strong>GPS supporting evidence: ${gpsLabel}</strong>
      ${gps?.distance_m != null ? `<p>${monitoringText(gps.distance_m)} m between camera positions · ${monitoringText(gps.tolerance_m)} m tolerance${gps.accuracy_known ? '' : ' · Accuracy in metres unknown'}</p>
      <p>Comparing ${evidenceLabel(gps.baseline_evidence)} with ${evidenceLabel(gps.follow_up_evidence)}.</p>` : '<p>Missing GPS does not imply a mismatch and does not prevent expert review.</p>'}
      <div class="monitor-gps-coordinates">${coordinates(gps?.baseline, 'Baseline')}${coordinates(gps?.follow_up, 'Follow-up')}</div></div>
    ${review ? `<p class="monitor-muted">Reviewed by ${monitoringText(review.reviewed_by_expert_uid)} · ${monitoringDateTime(review.reviewed_at)} · Revision ${review.revision}</p>${review.explanation ? `<p class="monitor-identity-note">${monitoringText(review.explanation)}</p>` : ''}` : ''}
    ${review && baseline && review.baseline_reference_id !== baseline.id ? '<p class="monitor-muted">This decision predates the registered baseline photograph. Review the new evidence before updating the decision.</p>' : ''}
    ${selected ? `<details id="monitor-identity-history" data-history-path="${monitoringText(`${base}/follow-ups/${encodeURIComponent(selected.id)}/identity-reviews`)}"><summary>Identity review history</summary><div class="monitor-muted" data-identity-history>Open to load review history.</div></details>` : ''}
    ${baselineForm}${reviewForm}</section>`;
}

async function monitoringLoadIdentityMedia() {
  const controller = new AbortController();
  identityMedia.controller = controller;
  const token = currentToken;
  await Promise.allSettled([...document.querySelectorAll('[data-tree-media]')].map(async element => {
    try {
      const response = await fetch(element.dataset.treeMedia, { headers: { Authorization: `Bearer ${token}` },
        signal: controller.signal, cache: 'no-store' });
      if (!response.ok) throw new Error('Image access failed');
      const blob = await response.blob();
      if (controller.signal.aborted || currentToken !== token || !element.isConnected) return;
      const url = URL.createObjectURL(blob);
      identityMedia.urls.push(url);
      const img = document.createElement('img');
      img.alt = element.dataset.treeAlt;
      img.src = url;
      element.classList.remove('monitor-image-fallback');
      element.replaceChildren(img);
    } catch (error) {
      if (error.name !== 'AbortError' && element.isConnected) element.textContent = 'Photograph unavailable. Reload the case to retry.';
    }
  }));
}

function monitoringBindIdentityHistory() {
  const details = document.getElementById('monitor-identity-history');
  details?.addEventListener('toggle', async () => {
    if (!details.open || details.dataset.loaded) return;
    details.dataset.loaded = 'loading';
    const target = details.querySelector('[data-identity-history]');
    target.textContent = 'Loading history…';
    try {
      const data = await monitoringRequest(details.dataset.historyPath);
      if (!details.isConnected) return;
      target.innerHTML = data.identity_reviews?.length ? `<ol>${data.identity_reviews.map(review => `<li>
        ${monitoringIdentityStatus(review)} · ${monitoringDateTime(review.reviewed_at)} · ${monitoringText(review.reviewed_by_expert_uid)}
        ${review.explanation ? `<p>${monitoringText(review.explanation)}</p>` : ''}</li>`).join('')}</ol>` : 'Identity not reviewed.';
    } catch (error) { target.textContent = error.message; delete details.dataset.loaded; }
  });
}

async function monitoringUploadBaseline(item, file) {
  const body = new FormData();
  body.append('file', file);
  return monitoringRequest(`${monitoringIdentityBase(item)}/tree-reference`, { method: 'POST', body });
}
