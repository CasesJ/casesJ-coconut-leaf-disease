// Tree identity evidence is independent of disease inference and outcome review.
function monitoringIdentityStatus(review) {
  return ({ confirmed: 'Identity confirmed', uncertain: 'Identity uncertain', mismatch: 'Identity mismatch' })[review?.status] || 'Identity not reviewed';
}

function monitoringIdentityBase(item) {
  return currentUserIsExpert
    ? `/expert/monitoring/cases/${encodeURIComponent(item.farmer_id)}/${encodeURIComponent(item.id)}`
    : `/monitoring/cases/${encodeURIComponent(item.id)}`;
}

window.monitoringResetIdentity = function () {
  monitoringCloseModal();
  monitoringState.detailRequest++;
  monitoringState.cases = [];
  monitoringState.overview = null;
  monitoringState.selected = null;
  const detail = document.getElementById('monitor-detail');
  if (detail) detail.textContent = '';
  const list = document.getElementById('monitor-case-list');
  if (list) list.textContent = '';
};

async function monitoringUploadBaseline(item, file) {
  const body = new FormData();
  body.append('file', file);
  return monitoringRequest(`${monitoringIdentityBase(item)}/tree-reference`, { method: 'POST', body });
}
