// Treatment monitoring uses the existing auth globals and FastAPI routes.
const monitoringState = { cases: [], selected: null, overview: null, expertRecords: [], search: '' };

window.monitoringOpenTree = function (encodedTree, encodedFarmer) {
  monitoringState.requestedTree = { id: decodeURIComponent(encodedTree), farmer: decodeURIComponent(encodedFarmer) };
  switchTab('monitoring', document.getElementById('nav-monitoring-link'));
};

function monitoringText(value) { return escapeHtml(String(value ?? '')); }
function monitoringGpsCoordinates(...candidates) {
  for (const gps of candidates) {
    if (!gps || !['exif', 'dji_exif'].includes(String(gps.source || '').toLowerCase())) continue;
    const latitude = gps.latitude ?? gps.lat;
    const longitude = gps.longitude ?? gps.lng;
    if (latitude == null || longitude == null || String(latitude).trim() === '' || String(longitude).trim() === '') continue;
    const lat = Number(latitude), lng = Number(longitude);
    if (Number.isFinite(lat) && Number.isFinite(lng) && Math.abs(lat) <= 90 && Math.abs(lng) <= 180) {
      return { lat, lng };
    }
  }
  return null;
}
function monitoringGpsCard(label, gps) {
  return `<div><span class="monitor-compare-label">${monitoringText(label)}</span>${gps
    ? `<p class="monitor-treatment-copy"><strong>Latitude:</strong> ${gps.lat.toFixed(6)}<br><strong>Longitude:</strong> ${gps.lng.toFixed(6)}</p><p class="monitor-muted">Source: embedded image GPS</p>`
    : '<p class="monitor-muted">No GPS location available</p>'}</div>`;
}
function monitoringImageUrl(value) {
  const url = String(value || '');
  return /^\/(media|expert\/media|static\/uploads|static\/annotated_uploads)\//.test(url)
    ? monitoringText(url) : '';
}
function monitoringDate(value) {
  if (!value) return 'Not recorded';
  const date = new Date(value.length === 10 ? `${value}T12:00:00+08:00` : value);
  return Number.isNaN(date.getTime()) ? 'Not recorded'
    : new Intl.DateTimeFormat('en-PH', { timeZone: 'Asia/Manila', year: 'numeric', month: 'short', day: 'numeric' }).format(date);
}
function monitoringDateTime(value) {
  if (!value) return 'Not recorded';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? 'Not recorded'
    : new Intl.DateTimeFormat('en-PH', { timeZone: 'Asia/Manila', dateStyle: 'medium', timeStyle: 'short' }).format(date);
}
function monitoringToday() {
  const fields = Object.fromEntries(new Intl.DateTimeFormat('en-US', {
    timeZone: 'Asia/Manila', year: 'numeric', month: '2-digit', day: '2-digit'
  }).formatToParts(new Date()).map(part => [part.type, part.value]));
  return `${fields.year}-${fields.month}-${fields.day}`;
}
function monitoringDayOffset(value, offset) {
  const [year, month, day] = String(value).split('-').map(Number);
  const date = new Date(Date.UTC(year, month - 1, day + offset));
  return `${date.getUTCFullYear()}-${String(date.getUTCMonth() + 1).padStart(2, '0')}-${String(date.getUTCDate()).padStart(2, '0')}`;
}
function monitoringTomorrow() { return monitoringDayOffset(monitoringToday(), 1); }
function monitoringDue(value) {
  if (!value) return { text: 'Not scheduled', cls: '' };
  const parts = new Intl.DateTimeFormat('en-US', { timeZone: 'Asia/Manila', year: 'numeric', month: '2-digit', day: '2-digit' })
    .formatToParts(new Date());
  const fields = Object.fromEntries(parts.map(part => [part.type, part.value]));
  const today = Date.UTC(+fields.year, +fields.month - 1, +fields.day);
  const [year, month, day] = String(value).split('-').map(Number);
  const remaining = Math.round((Date.UTC(year, month - 1, day) - today) / 86400000);
  if (!Number.isFinite(remaining)) return { text: 'Not scheduled', cls: '' };
  if (remaining < 0) return { text: `Overdue by ${-remaining} day${remaining === -1 ? '' : 's'}`, cls: 'overdue' };
  if (remaining === 0) return { text: 'Due today', cls: 'due' };
  return { text: `Due in ${remaining} day${remaining === 1 ? '' : 's'}`, cls: remaining <= 2 ? 'due' : '' };
}
function monitoringStatus(value) {
  return ({ needs_review: 'Needs Review', improving: 'Improving', no_visible_change: 'No Visible Change',
    worsening: 'Worsening' })[value] || 'Needs Review';
}
function monitoringTreatmentLifecycle(overview) {
  const planned = [...(overview.planned_treatments || [])]
    .filter(item => item.status === 'scheduled')
    .sort((a, b) => String(a.created_at || '').localeCompare(String(b.created_at || '')))
    .at(-1);
  if (planned) {
    const due = monitoringDue(planned.planned_date);
    return { text: 'Treatment Scheduled', detail: `${monitoringDate(planned.planned_date)} · ${due.text}`, cls: due.cls };
  }
  const applications = overview.treatment_applications || [];
  if (applications.length) {
    const latest = applications[applications.length - 1];
    return { text: 'Treatment Applied', detail: `Latest application · ${monitoringDate(latest.application_date)}`, cls: '' };
  }
  return { text: 'Awaiting Treatment', detail: 'Expert recommendation is available. No application has been recorded.', cls: '' };
}
function monitoringNextAction(item) {
  if (item.monitoring_status === 'finish') return null;
  const actions = [
    item.next_follow_up_date && { date: item.next_follow_up_date, label: 'Progression check' },
    item.next_post_treatment_follow_up_date && { date: item.next_post_treatment_follow_up_date, label: 'Post-treatment check' },
    item.planned_treatment_date && { date: item.planned_treatment_date, label: 'Treatment' }
  ].filter(Boolean).sort((a, b) => String(a.date).localeCompare(String(b.date)));
  return actions[0] || null;
}
function monitoringCaseReview(item) {
  if (item.monitoring_status === 'finish') return { text: 'Finish / Healthy', cls: '' };
  if (item.latest_review_status === 'expert_confirmed') {
    return { text: `Outcome confirmed: ${monitoringStatus(item.latest_confirmed_outcome)}`, cls: '' };
  }
  if (item.latest_review_status === 'pending_expert_review') return { text: 'Awaiting expert review', cls: 'review' };
  return { text: 'No inspection yet', cls: '' };
}
function monitoringEmptyState(title, description, steps = true) {
  return `<section class="monitor-card monitor-empty-state">
    <div class="monitor-empty-art" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M12 20V10M12 10C7.6 10 5.6 7.3 5.6 4.6 9.1 4.5 11.3 6.6 12 10zm0 0c4.4 0 6.4-2.7 6.4-5.4C14.9 4.5 12.7 6.6 12 10zM7 20h10"/><path d="M5 20h14"/></svg></div>
    <h2>${monitoringText(title)}</h2>
    <p>${monitoringText(description)}</p>
    ${steps ? '<div class="monitor-empty-steps"><span><b>1</b>Verify detection</span><span><b>2</b>Record treatment</span><span><b>3</b>Track inspections</span></div>' : ''}
  </section>`;
}
function monitoringUpdateSummary() {
  const cases = monitoringState.cases;
  const attention = cases.filter(item => {
    const action = monitoringNextAction(item);
    return action && ['due', 'overdue'].includes(monitoringDue(action.date).cls);
  }).length;
  const awaitingReview = cases.filter(item => item.latest_review_status === 'pending_expert_review').length;
  const confirmed = cases.filter(item => item.latest_review_status === 'expert_confirmed').length;
  const summary = [
    ['monitor-total-cases', cases.length],
    ['monitor-attention-cases', attention],
    ['monitor-review-cases', awaitingReview],
    ['monitor-confirmed-cases', confirmed],
    ['monitor-queue-count', cases.length]
  ];
  summary.forEach(([id, value]) => {
    const element = document.getElementById(id);
    if (element) element.textContent = String(value);
  });
}
function monitoringRenderCaseList() {
  const list = document.getElementById('monitor-case-list');
  if (!list) return;
  const query = monitoringState.search.trim().toLowerCase();
  const visibleCases = monitoringState.cases.map((item, index) => ({ item, index })).filter(({ item }) => {
    if (!query) return true;
    return [item.tree_id, item.disease, item.farmer_id].some(value => String(value || '').toLowerCase().includes(query));
  });
  if (!visibleCases.length) {
    list.innerHTML = `<div class="monitor-list-empty">${monitoringState.cases.length ? 'No treatment case matches this search.' : 'No treatment cases yet.'}</div>`;
    return;
  }
  list.innerHTML = visibleCases.map(({ item, index }) => {
    const action = monitoringNextAction(item);
    const due = monitoringDue(action?.date);
    const review = monitoringCaseReview(item);
    const treatmentStatus = ({ awaiting_treatment: 'Awaiting Treatment',
      treatment_scheduled: 'Treatment Scheduled', treatment_applied: 'Treatment Applied' })[item.treatment_status];
    const farmer = currentUserIsExpert && item.farmer_id ? `<span>Farmer ${monitoringText(item.farmer_id)}</span>` : '';
    return `<button type="button" class="monitor-case-button" data-monitor-case="${index}" aria-current="${monitoringState.selected === item.id}">
      <span class="monitor-case-top"><span class="monitor-case-name">${monitoringText(item.tree_id)}</span><span class="monitor-pill ${due.cls}">${monitoringText(action ? `${action.label} · ${due.text}` : due.text)}</span></span>
      <span class="monitor-case-meta"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 20V10M12 10C7.6 10 5.6 7.3 5.6 4.6 9.1 4.5 11.3 6.6 12 10zm0 0c4.4 0 6.4-2.7 6.4-5.4C14.9 4.5 12.7 6.6 12 10z"/></svg><span>${monitoringText(item.disease)}</span>${farmer}</span>
      <span class="monitor-case-footer">${treatmentStatus ? `<span class="monitor-pill">${treatmentStatus}</span>` : ''}<span class="monitor-pill ${review.cls}">${monitoringText(review.text)}</span></span>
    </button>`;
  }).join('');
}
async function monitoringRequest(path, options = {}) {
  if (!currentToken) throw new Error('Please sign in first');
  const requestToken = currentToken;
  const response = await fetch(path, {
    ...options, headers: { Authorization: `Bearer ${currentToken}`, ...(options.headers || {}) }
  });
  const body = await response.json().catch(() => ({}));
  if (currentToken !== requestToken) throw new Error('Sign-in changed. Reload the case.');
  if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : 'Monitoring request failed');
  return body;
}

window.loadMonitoringDashboard = async function loadMonitoringDashboard() {
  const list = document.getElementById('monitor-case-list');
  if (!document.getElementById('monitor-detail') || !currentToken) return;
  if (list) list.innerHTML = '<div class="monitor-list-empty">Loading treatment cases…</div>';
  document.getElementById('monitor-create-panel').style.display = currentUserIsExpert ? '' : 'none';
  try {
    const data = await monitoringRequest(currentUserIsExpert ? '/expert/monitoring/cases' : '/monitoring/cases');
    monitoringState.cases = (data.cases || []).sort((a, b) => String(a.next_follow_up_date || '').localeCompare(String(b.next_follow_up_date || '')));
    monitoringUpdateSummary();
    monitoringRenderCaseList();
    // The creation form must not block the selected care record and images.
    if (currentUserIsExpert) void monitoringLoadBaselineOptions();
    const requested = monitoringState.requestedTree;
    monitoringState.requestedTree = null;
    const selectedIndex = monitoringState.cases.findIndex(item => requested
      ? item.tree_id === requested.id && item.farmer_id === requested.farmer
      : item.id === monitoringState.selected);
    if (requested) {
      monitoringState.search = requested.id;
      const search = document.getElementById('monitor-case-search');
      if (search) search.value = requested.id;
      monitoringRenderCaseList();
      if (selectedIndex < 0) {
        document.getElementById('monitor-detail').innerHTML = monitoringEmptyState('No care record linked',
          'This tree has no accessible monitoring case. Its audit history remains available.', false);
        return;
      }
    }
    if (monitoringState.cases.length) await monitoringSelectCase(selectedIndex >= 0 ? selectedIndex : 0);
    else {
      monitoringState.selected = null;
      monitoringState.overview = null;
      document.getElementById('monitor-detail').innerHTML = monitoringEmptyState(
        'Your tree care record will appear here',
        currentUserIsExpert
          ? 'Create a care record from a verified detection to begin tracking a farmer’s treatment and inspection evidence.'
          : 'When an expert assigns a verified detection to monitoring, you can add inspection photos and follow the treatment plan for that tree.'
      );
    }
  } catch (error) {
    if (list) list.innerHTML = `<div class="monitor-list-empty">${monitoringText(error.message)}</div>`;
    showToast(`Monitoring: ${error.message}`);
  }
};

async function monitoringLoadBaselineOptions() {
  const select = document.getElementById('monitor-baseline');
  if (!select) return;
  try {
    const data = await fetchExpertRecords();
    monitoringState.expertRecords = (data.records || []).filter(record =>
      record.verification_status === 'verified' && record.recommendation_snapshot?.recommendations?.treatment);
    select.innerHTML = '<option value="">Select a verified detection</option>' + monitoringState.expertRecords.map((record, index) =>
      `<option value="${index}">${monitoringText(record.email || record.user_id)} · ${monitoringText(record.filename || record.id)} · ${monitoringText(record.recommendation_snapshot?.disease || '')}</option>`).join('');
  } catch (error) {
    select.innerHTML = '<option value="">Could not load verified detections</option>';
  }
}

async function monitoringSelectCase(index) {
  const item = monitoringState.cases[index];
  if (!item) return;
  monitoringState.selected = item.id;
  document.querySelectorAll('[data-monitor-case]').forEach(button =>
    button.setAttribute('aria-current', String(Number(button.dataset.monitorCase) === index)));
  const detail = document.getElementById('monitor-detail');
  detail.textContent = 'Loading case details...';
  try {
    const path = currentUserIsExpert
      ? `/expert/monitoring/cases/${encodeURIComponent(item.farmer_id)}/${encodeURIComponent(item.id)}/overview`
      : `/monitoring/cases/${encodeURIComponent(item.id)}/overview`;
    const overview = await monitoringRequest(path);
    if (monitoringState.selected !== item.id) return;
    monitoringState.overview = overview;
    monitoringRenderOverview(overview);
  } catch (error) {
    detail.textContent = error.message;
  }
}

function monitoringRenderOverview(overview, selectedFollowUp = null) {
  const item = overview.case;
  const followUps = overview.follow_ups || [];
  const treatmentApplications = overview.treatment_applications || [];
  const followUpSchedules = overview.follow_up_schedules || [];
  const selected = followUps.find(followUp => followUp.id === selectedFollowUp) || followUps[followUps.length - 1] || null;
  const progressionDue = monitoringDue(item.next_follow_up_date);
  const due = progressionDue;
  const latestPostSchedule = [...followUpSchedules]
    .filter(schedule => schedule.schedule_type === 'post_treatment' && schedule.status !== 'superseded')
    .sort((a, b) => String(a.created_at || '').localeCompare(String(b.created_at || ''))).at(-1);
  const postTreatmentDue = monitoringDue(latestPostSchedule?.due_date || item.next_post_treatment_follow_up_date);
  const lifecycle = monitoringTreatmentLifecycle(overview);
  const original = monitoringImageUrl(overview.baseline?.image_url || overview.baseline?.annotated_image_url);
  const after = monitoringImageUrl(selected?.image_url || selected?.annotated_image_url);
  const originalPreview = original
    ? (window.DetectionOverlay?.frameHtml(original, overview.baseline || {}, {
      className: 'monitor-detection-preview', alt: 'Baseline disease image',
    }) || '<img data-private-src="' + original + '" alt="Baseline disease image">')
    : '';
  // Follow-up evidence displays the original upload without detection overlays.
  const afterPreview = after
    ? '<div class="monitor-detection-preview"><img data-private-src="' + monitoringText(after) + '" alt="Original follow-up image"></div>'
    : '';
  const fallback = '<div class="monitor-image-fallback">Image unavailable</div>';
  const timeline = `<li><strong>Baseline detection · ${monitoringDate(overview.baseline?.timestamp || item.created_at)}</strong>
    <p class="monitor-muted">${monitoringText(item.disease)} · ${monitoringText(overview.baseline?.filename || item.baseline_detection_id)}</p></li>` +
    followUps.map((record, index) => `<li><button type="button" data-monitor-follow-up="${index}">
      Inspection ${index + 1} · ${monitoringDate(record.created_at)}</button>
      <p class="monitor-muted">Farmer observation: ${monitoringStatus(record.preliminary_status)} · ${record.review_status === 'expert_confirmed' ? `Outcome confirmed: ${monitoringStatus(record.confirmed_outcome)}` : 'Needs Review'}</p>
      <p class="monitor-muted">${monitoringIdentityStatus(record.identity_review)}</p>
      ${record.expert_note ? `<p class="monitor-muted">Expert note: ${monitoringText(record.expert_note)}</p>` : ''}</li>`).join('');
  const scheduleOptions = followUpSchedules.filter(schedule => schedule.status !== 'superseded').map(schedule => {
    const kind = schedule.schedule_type === 'post_treatment' ? 'Post-treatment' : 'Disease progression';
    const application = treatmentApplications.find(record => record.id === schedule.treatment_application_id);
    const suffix = application ? ` after ${monitoringDate(application.application_date)} treatment` : '';
    return `<option value="${monitoringText(schedule.id)}">${kind} · ${monitoringDate(schedule.due_date)}${suffix}</option>`;
  }).join('');
  const allowance = overview.follow_up_upload_allowance || {
    limit: 3, used: followUps.length, remaining: Math.max(0, 3 - followUps.length)
  };
  const uploadForm = !currentUserIsExpert ? `<section class="monitor-card"><h3 class="monitor-section-heading"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 5h16v14H4zM8 5l1.5-2h5L16 5M8 14l2.5-2.5 2 2L15.5 10l3.5 4"/></svg>Add an inspection</h3><p class="monitor-muted" style="margin-top:7px;">Upload a clear image of this tree. Your observation stays preliminary until an expert reviews it.</p>
    <p class="monitor-muted">${monitoringText(allowance.used)} of ${monitoringText(allowance.limit)} follow-up uploads used for this tree ID. ${monitoringText(allowance.remaining)} remaining.</p>
    ${allowance.remaining === 0 ? '<p class="monitor-muted" role="status">You have reached the limit of 3 follow-up image uploads for this tree ID.</p>' : ''}
    <form id="monitor-upload-form" class="monitor-form"><fieldset style="border:0;padding:0;margin:0;display:contents" ${allowance.remaining === 0 ? 'disabled' : ''}>
      ${scheduleOptions ? `<label>Inspection schedule (optional)<select id="monitor-follow-up-schedule"><option value="">General inspection</option>${scheduleOptions}</select></label>` : ''}
      <label>Disease close-up image<input id="monitor-upload-file" type="file" accept="image/jpeg,image/png" required></label>
      <label>Whole-tree reference photo (optional)<input id="monitor-upload-tree-file" type="file" accept="image/jpeg,image/png"></label>
      <p class="monitor-muted">Include the trunk, crown, and surrounding landmarks of this same tree. A separate whole-tree photo is needed for identity confirmation; GPS is optional.</p>
      <label>Your visual observation<select id="monitor-preliminary"><option value="needs_review">Needs Review</option><option value="improving">Improving</option><option value="no_visible_change">No Visible Change</option><option value="worsening">Worsening</option></select></label>
      <button class="btn btn-p" type="submit">Save inspection</button></fieldset></form></section>` : '';
  const reviewForm = currentUserIsExpert && selected ? `<section class="monitor-card"><h3 class="monitor-section-heading"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 12l5 5L20 6"/></svg>Treatment-outcome review · ${monitoringDate(selected.created_at)}</h3><p class="monitor-muted" style="margin-top:7px;">Compare the evidence and record the visible outcome. Model confidence is not a measure of recovery.</p>
    <form id="monitor-review-form" class="monitor-form" data-follow-up-id="${monitoringText(selected.id)}">
      <label>Confirmed visual outcome<select id="monitor-outcome" required><option value="">Choose an outcome</option><option value="improving">Improving</option><option value="no_visible_change">No Visible Change</option><option value="worsening">Worsening</option></select></label>
      <label>Review note<textarea id="monitor-review-note" rows="3" required maxlength="2000" placeholder="Describe visible evidence and any uncertainty"></textarea></label>
      <button class="btn btn-p" type="submit">Record expert review</button></form></section>` : '';
  const baselineGps = monitoringGpsCoordinates(item.tree_reference?.gps_data, item.baseline_gps_snapshot,
    selected?.identity_gps_evidence?.baseline, overview.baseline?.gps_data);
  const inspectionGps = monitoringGpsCoordinates(selected?.tree_reference?.gps_data, selected?.gps_data);
  const treeLocation = `<section class="monitor-card"><h3 class="monitor-section-heading">Tree GPS location</h3>
    <p class="monitor-muted" style="margin-top:7px;">Tree ID: ${monitoringText(item.tree_id)}. Compare the recorded image locations alongside the whole-tree photos to check tree identity. GPS alone cannot confirm the exact tree.</p>
    <div class="monitor-compare-grid">${monitoringGpsCard('Baseline location', baselineGps)}${monitoringGpsCard(selected ? `Inspection location · ${monitoringDate(selected.created_at)}` : 'Inspection location', inspectionGps)}</div></section>`;
  document.getElementById('monitor-detail').innerHTML = `<section class="monitor-card">
    <div class="monitor-treatment-state"><span class="monitor-pill ${lifecycle.cls}">${monitoringText(lifecycle.text)}</span><span class="monitor-muted">${monitoringText(lifecycle.detail)}</span></div>
    <div class="monitor-head"><div><p class="monitor-case-label">${monitoringText(item.tree_id)} · ${monitoringText(item.disease)}</p><h2>Care record</h2>
    <p class="monitor-muted" style="margin-top:5px;">Baseline ${monitoringDate(overview.baseline?.timestamp || item.created_at)} · ${followUps.length} follow-up${followUps.length === 1 ? '' : 's'} recorded</p></div>
    <div class="monitor-next-check"><span class="monitor-pill ${due.cls}">${monitoringText(due.text)}</span><small>Next check · ${monitoringDate(item.next_follow_up_date)}</small></div></div>
    <div class="monitor-comparison"><figure>${original ? `${originalPreview}` : fallback}<figcaption>Baseline image</figcaption></figure>
    <figure>${after ? `${afterPreview}` : fallback}<figcaption>${selected ? `Latest inspection · ${monitoringDate(selected.created_at)}` : 'Awaiting first inspection'}</figcaption></figure></div>
    <div class="monitor-care-status">
      <div><p class="monitor-muted">Status: <strong>${item.monitoring_status === 'finish' ? 'Finish / Healthy - confirmed by expert' : 'Waiting'}</strong></p>
      ${item.monitoring_status === 'finish' ? `<p class="monitor-muted">Confirmed ${monitoringDateTime(item.finished_at)}</p>` : ''}</div>
      ${item.monitoring_status !== 'finish' && currentUserIsExpert ? '<button type="button" class="btn btn-p" data-monitor-finish>Finish - confirm tree is healthy</button>' : ''}
    </div></section>
    <section class="monitor-card"><h3 class="monitor-section-heading"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 4v16M5 12h5l2-3 2 3h5"/></svg>Inspection timeline</h3><ol class="monitor-timeline">${timeline}</ol></section>
    ${treeLocation}
    ${uploadForm}${reviewForm}`;
}

document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('monitor-case-search')?.addEventListener('input', event => {
    monitoringState.search = event.target.value;
    monitoringRenderCaseList();
  });
  document.getElementById('monitor-case-list')?.addEventListener('click', event => {
    const button = event.target.closest('[data-monitor-case]');
    if (button) monitoringSelectCase(Number(button.dataset.monitorCase));
  });
  document.getElementById('monitor-detail')?.addEventListener('click', async event => {
    const finish = event.target.closest('[data-monitor-finish]');
    if (finish && currentUserIsExpert && monitoringState.overview) {
      const item = monitoringState.overview.case;
      finish.disabled = true;
      try {
        await monitoringRequest(`${monitoringIdentityBase(item)}/finish`, { method: 'PUT' });
        await loadMonitoringDashboard();
      } catch (error) {
        alert(error.message);
        finish.disabled = false;
      }
      return;
    }
    const button = event.target.closest('[data-monitor-follow-up]');
    if (button && monitoringState.overview) monitoringRenderOverview(monitoringState.overview,
      monitoringState.overview.follow_ups[Number(button.dataset.monitorFollowUp)]?.id);
  });
  document.getElementById('monitor-baseline')?.addEventListener('change', async event => {
    const select = document.getElementById('monitor-tree-select');
    const value = event.target.value;
    const record = value === '' ? null : monitoringState.expertRecords[Number(value)];
    select.disabled = true;
    select.innerHTML = '<option value="">Loading trees...</option>';
    if (!record) return;
    try {
      const data = await monitoringRequest(`/expert/monitoring/trees/${encodeURIComponent(record.user_id)}`);
      if (event.target.value !== value) return;
      const ids = [...new Set([...(data.trees || []).map(tree => tree.id), record.tree_id].filter(Boolean))];
      select.innerHTML = record.tree_id
        ? `<option value="${monitoringText(record.tree_id)}">${monitoringText(record.tree_id)} (linked detection)</option>`
        : '<option value="">Choose a physical tree</option><option value="__new__">Register a new tree</option>' + ids.map(id => `<option value="${monitoringText(id)}">${monitoringText(id)}</option>`).join('');
      select.required = true;
      select.disabled = false;
    } catch (error) { select.innerHTML = '<option value="">Could not load trees</option>'; showToast(error.message); }
  });
  document.getElementById('monitor-create-form')?.addEventListener('submit', async event => {
    event.preventDefault();
    const form = event.target, button = form.querySelector('button[type=submit]');
    const record = monitoringState.expertRecords[Number(document.getElementById('monitor-baseline').value)];
    if (!record || document.getElementById('monitor-tree-select').disabled) return;
    button.disabled = true;
    try {
      const created = await monitoringRequest('/expert/monitoring/cases', { method: 'POST',
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ farmer_id: record.user_id,
          tree_id: document.getElementById('monitor-tree-select').value === '__new__' ? null : document.getElementById('monitor-tree-select').value, baseline_detection_id: record.id,
          follow_up_interval_days: Number(document.getElementById('monitor-interval').value) }) });
      monitoringState.selected = created.id;
      const referenceFile = document.getElementById('monitor-create-tree-file')?.files[0];
      form.reset();
      document.getElementById('monitor-tree-select').disabled = true;
      if (referenceFile) {
        try {
          await monitoringUploadBaseline(created, referenceFile);
          showToast('Monitoring case and tree reference created');
        } catch (error) {
          showToast(`Case created; tree reference was not confirmed saved. Reload this case before retrying: ${error.message}`);
        }
      } else showToast('Monitoring case created. Add a tree reference when available.');
      await loadMonitoringDashboard();
    } catch (error) { showToast(`Could not create case: ${error.message}`); }
    finally { button.disabled = false; }
  });
  document.getElementById('monitor-detail')?.addEventListener('submit', async event => {
    const form = event.target;
    if (!['monitor-treatment-application-form', 'monitor-treatment-schedule-form', 'monitor-upload-form', 'monitor-review-form'].includes(form.id)) return;
    event.preventDefault();
    const item = monitoringState.overview?.case;
    if (!item) return;
    const button = form.querySelector('button[type=submit]');
    button.disabled = true;
    try {
      if (form.id === 'monitor-treatment-schedule-form') {
        await monitoringRequest(`/monitoring/cases/${encodeURIComponent(item.id)}/planned-treatments`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({
            planned_date: document.getElementById('monitor-planned-date').value,
            notes: document.getElementById('monitor-planned-notes').value.trim() || null
          })
        });
        showToast('Planned treatment date saved');
      } else if (form.id === 'monitor-treatment-application-form') {
        const plannedSelect = document.getElementById('monitor-application-plan');
        await monitoringRequest(`/monitoring/cases/${encodeURIComponent(item.id)}/treatment-applications`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({
            treatment_performed: document.getElementById('monitor-treatment-performed').value.trim(),
            application_date: document.getElementById('monitor-application-date').value,
            notes: document.getElementById('monitor-application-notes').value.trim() || null,
            planned_treatment_id: plannedSelect?.value || null
          })
        });
        showToast('Treatment application recorded and follow-up scheduled');
      } else if (form.id === 'monitor-upload-form') {
        const body = new FormData();
        body.append('file', document.getElementById('monitor-upload-file').files[0]);
        body.append('preliminary_status', document.getElementById('monitor-preliminary').value);
        const scheduleId = document.getElementById('monitor-follow-up-schedule')?.value;
        if (scheduleId) body.append('follow_up_schedule_id', scheduleId);
        const treePhoto = document.getElementById('monitor-upload-tree-file').files[0];
        if (treePhoto) body.append('tree_reference_file', treePhoto);
        await monitoringRequest(`/monitoring/cases/${encodeURIComponent(item.id)}/follow-ups`, { method: 'POST', body });
        showToast('Follow-up saved for expert review');
      } else if (form.id === 'monitor-review-form') {
        await monitoringRequest(`/expert/monitoring/cases/${encodeURIComponent(item.farmer_id)}/${encodeURIComponent(item.id)}/follow-ups/${encodeURIComponent(form.dataset.followUpId)}/review`,
          { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({
            confirmed_outcome: document.getElementById('monitor-outcome').value,
            note: document.getElementById('monitor-review-note').value.trim() }) });
        showToast('Treatment-outcome review recorded');
      }
      await loadMonitoringDashboard();
      if (form.dataset.followUpId && monitoringState.overview?.case.id === item.id) {
        monitoringRenderOverview(monitoringState.overview, form.dataset.followUpId);
      }
    } catch (error) { showToast(`Monitoring update failed: ${error.message}`); }
    finally { button.disabled = false; }
  });
});
