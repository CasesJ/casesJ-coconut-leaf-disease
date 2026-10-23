// Treatment monitoring uses the existing auth globals and FastAPI routes.
const monitoringState = { cases: [], selected: null, overview: null, expertRecords: [], search: '' };

function monitoringText(value) { return escapeHtml(String(value ?? '')); }
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
  const actions = [
    item.next_follow_up_date && { date: item.next_follow_up_date, label: 'Progression check' },
    item.next_post_treatment_follow_up_date && { date: item.next_post_treatment_follow_up_date, label: 'Post-treatment check' },
    item.planned_treatment_date && { date: item.planned_treatment_date, label: 'Treatment' }
  ].filter(Boolean).sort((a, b) => String(a.date).localeCompare(String(b.date)));
  return actions[0] || null;
}
function monitoringCaseReview(item) {
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
  if (!list || !currentToken) return;
  list.innerHTML = '<div class="monitor-list-empty">Loading treatment cases…</div>';
  document.getElementById('monitor-list-title').textContent = currentUserIsExpert ? 'Farmer treatment cases' : 'My treatment cases';
  document.getElementById('monitor-role-caption').textContent = currentUserIsExpert
    ? 'Review care records and follow-up evidence.' : 'Select a tree to view its care record.';
  document.getElementById('monitor-create-panel').style.display = currentUserIsExpert ? '' : 'none';
  try {
    const data = await monitoringRequest(currentUserIsExpert ? '/expert/monitoring/cases' : '/monitoring/cases');
    monitoringState.cases = (data.cases || []).sort((a, b) => String(a.next_follow_up_date || '').localeCompare(String(b.next_follow_up_date || '')));
    monitoringUpdateSummary();
    monitoringRenderCaseList();
    if (currentUserIsExpert) await monitoringLoadBaselineOptions();
    const selectedIndex = monitoringState.cases.findIndex(item => item.id === monitoringState.selected);
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
    list.innerHTML = `<div class="monitor-list-empty">${monitoringText(error.message)}</div>`;
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
  monitoringClearIdentityMedia();
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
  monitoringClearIdentityMedia();
  const item = overview.case;
  const followUps = overview.follow_ups || [];
  const treatmentApplications = overview.treatment_applications || [];
  const plannedTreatments = overview.planned_treatments || [];
  const followUpSchedules = overview.follow_up_schedules || [];
  const selected = followUps.find(followUp => followUp.id === selectedFollowUp) || followUps[followUps.length - 1] || null;
  const progressionDue = monitoringDue(item.next_follow_up_date);
  const due = progressionDue;
  const latestPostSchedule = [...followUpSchedules]
    .filter(schedule => schedule.schedule_type === 'post_treatment' && schedule.status !== 'superseded')
    .sort((a, b) => String(a.created_at || '').localeCompare(String(b.created_at || ''))).at(-1);
  const postTreatmentDue = monitoringDue(latestPostSchedule?.due_date || item.next_post_treatment_follow_up_date);
  const lifecycle = monitoringTreatmentLifecycle(overview);
  const advice = overview.treatment?.recommendation_snapshot || {};
  const original = monitoringImageUrl(overview.baseline?.image_url || overview.baseline?.annotated_image_url);
  const after = monitoringImageUrl(selected?.image_url || selected?.annotated_image_url);
  const originalPreview = original
    ? (window.DetectionOverlay?.frameHtml(original, overview.baseline || {}, {
      className: 'monitor-detection-preview', alt: 'Baseline disease image',
    }) || '<img data-private-src="' + original + '" alt="Baseline disease image">')
    : '';
  const afterPreview = after
    ? (window.DetectionOverlay?.frameHtml(after, selected || {}, {
      className: 'monitor-detection-preview', alt: 'Latest follow-up image',
    }) || '<img data-private-src="' + after + '" alt="Latest follow-up image">')
    : '';
  const fallback = '<div class="monitor-image-fallback">Image unavailable</div>';
  const outcomeReview = selected ? (selected.review_status === 'expert_confirmed'
    ? `Treatment outcome confirmed: ${monitoringStatus(selected.confirmed_outcome)}` : 'Treatment outcome needs review') : 'No follow-up yet';
  const review = `${lifecycle.text}. ${lifecycle.detail} ${outcomeReview}`;
  const timeline = `<li><strong>Baseline detection · ${monitoringDate(overview.baseline?.timestamp || item.created_at)}</strong>
    <p class="monitor-muted">${monitoringText(item.disease)} · ${monitoringText(overview.baseline?.filename || item.baseline_detection_id)}</p></li>` +
    followUps.map((record, index) => `<li><button type="button" data-monitor-follow-up="${index}">
      Inspection ${index + 1} · ${monitoringDate(record.created_at)}</button>
      <p class="monitor-muted">Farmer observation: ${monitoringStatus(record.preliminary_status)} · ${record.review_status === 'expert_confirmed' ? `Outcome confirmed: ${monitoringStatus(record.confirmed_outcome)}` : 'Needs Review'}</p>
      <p class="monitor-muted">${monitoringIdentityStatus(record.identity_review)}</p>
      ${record.expert_note ? `<p class="monitor-muted">Expert note: ${monitoringText(record.expert_note)}</p>` : ''}</li>`).join('');
  const applicationHistory = treatmentApplications.length ? treatmentApplications.map(application => `<li>
      <div class="monitor-application-top"><strong>${monitoringText(application.treatment_performed)}</strong><time datetime="${monitoringText(application.application_date)}">${monitoringDate(application.application_date)}</time></div>
      ${application.notes ? `<p>${monitoringText(application.notes)}</p>` : ''}
      ${application.first_follow_up_due_date ? `<p class="monitor-follow-up-date">Post-treatment inspection due ${monitoringDate(application.first_follow_up_due_date)} (${monitoringText(application.follow_up_interval_days || item.follow_up_interval_days || 6)} days after application)</p>` : ''}
      <small>Logged ${monitoringDateTime(application.created_at)}</small></li>`).join('')
    : '<li class="monitor-history-empty">No treatment application has been recorded yet.</li>';
  const plannedHistory = plannedTreatments.length ? plannedTreatments.map(planned => {
    const due = monitoringDue(planned.planned_date);
    const status = planned.status === 'applied' ? 'Applied'
      : planned.status === 'superseded' ? 'Rescheduled'
        : due.cls === 'overdue' ? 'Overdue' : 'Scheduled';
    return `<li><div class="monitor-application-top"><strong>${status}</strong><time datetime="${monitoringText(planned.planned_date)}">${monitoringDate(planned.planned_date)}</time></div>
      ${planned.notes ? `<p>${monitoringText(planned.notes)}</p>` : ''}
      <small>Planned ${monitoringDateTime(planned.created_at)}${planned.applied_application_id ? ` · Application ${monitoringText(planned.applied_application_id)}` : ''}</small></li>`;
  }).join('') : '<li class="monitor-history-empty">No treatment date is scheduled. You can keep this recommendation for later.</li>';
  const plannedOptions = plannedTreatments.filter(plan => plan.status === 'scheduled').map(plan =>
    `<option value="${monitoringText(plan.id)}" ${item.planned_treatment_id === plan.id ? 'selected' : ''}>${monitoringDate(plan.planned_date)}${plan.notes ? ` · ${monitoringText(plan.notes)}` : ''}</option>`).join('');
  const scheduleOptions = followUpSchedules.filter(schedule => schedule.status !== 'superseded').map(schedule => {
    const kind = schedule.schedule_type === 'post_treatment' ? 'Post-treatment' : 'Disease progression';
    const application = treatmentApplications.find(record => record.id === schedule.treatment_application_id);
    const suffix = application ? ` after ${monitoringDate(application.application_date)} treatment` : '';
    return `<option value="${monitoringText(schedule.id)}">${kind} · ${monitoringDate(schedule.due_date)}${suffix}</option>`;
  }).join('');
  const scheduleHistory = followUpSchedules.length ? followUpSchedules.map(schedule => {
    const kind = schedule.schedule_type === 'post_treatment' ? 'Post-treatment inspection' : 'Disease-progression inspection';
    const status = schedule.status === 'superseded' ? 'Superseded'
      : schedule.status === 'inspection_recorded' ? 'Inspection recorded' : monitoringDue(schedule.due_date).text;
    const application = treatmentApplications.find(record => record.id === schedule.treatment_application_id);
    return `<li><strong>${kind} · ${monitoringDate(schedule.due_date)}</strong><p class="monitor-muted">${status}${application ? ` · Application on ${monitoringDate(application.application_date)}` : ''}</p></li>`;
  }).join('') : item.next_follow_up_date
    ? `<li><strong>Disease-progression inspection · ${monitoringDate(item.next_follow_up_date)}</strong><p class="monitor-muted">Existing schedule retained from this monitoring case.</p></li>`
    : '<li class="monitor-history-empty">No inspection is scheduled. A treatment is not required to schedule disease monitoring.</li>';
  const applicationForm = !currentUserIsExpert ? `<section class="monitor-card"><h3 class="monitor-section-heading"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 20V10M12 10C7.6 10 5.6 7.3 5.6 4.6 9.1 4.5 11.3 6.6 12 10zm0 0c4.4 0 6.4-2.7 6.4-5.4C14.9 4.5 12.7 6.6 12 10zM7 20h10"/></svg>Confirm treatment applied</h3><p class="monitor-muted" style="margin-top:7px;">Record only what you actually applied. Saving this creates a separate post-treatment inspection schedule.</p>
    <form id="monitor-treatment-application-form" class="monitor-form">
      <label>Treatment actually applied<input id="monitor-treatment-performed" type="text" maxlength="1000" required placeholder="For example: removed affected fronds and applied fungicide"></label>
      <label>Application date<input id="monitor-application-date" type="date" max="${monitoringToday()}" required></label>
      ${plannedOptions ? `<label>Scheduled treatment (optional)<select id="monitor-application-plan"><option value="">Not linked to a scheduled treatment</option>${plannedOptions}</select></label>` : ''}
      <label>Notes (optional)<textarea id="monitor-application-notes" rows="3" maxlength="2000" placeholder="Product, dosage, weather, or observations"></textarea></label>
      <button class="btn btn-p" type="submit">Confirm treatment applied</button></form></section>` : '';
  const plannedForm = !currentUserIsExpert ? `<section class="monitor-card"><h3 class="monitor-section-heading"><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="4" y="5" width="16" height="15" rx="2"/><path d="M8 3v4M16 3v4M7 11h10"/></svg>Schedule treatment</h3><p class="monitor-muted" style="margin-top:7px;">Scheduling is optional. The expert recommendation stays available while you decide when to act.</p>
    <form id="monitor-treatment-schedule-form" class="monitor-form">
      <label>Planned treatment date<input id="monitor-planned-date" type="date" min="${monitoringTomorrow()}" required></label>
      <label>Notes (optional)<textarea id="monitor-planned-notes" rows="2" maxlength="2000" placeholder="Plan or reminder"></textarea></label>
      <button class="btn btn-o" type="submit">Save planned date</button></form></section>` : '';
  const uploadForm = !currentUserIsExpert ? `<section class="monitor-card"><h3 class="monitor-section-heading"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 5h16v14H4zM8 5l1.5-2h5L16 5M8 14l2.5-2.5 2 2L15.5 10l3.5 4"/></svg>Add an inspection</h3><p class="monitor-muted" style="margin-top:7px;">Upload a clear image of this tree. Your observation stays preliminary until an expert reviews it.</p>
    <form id="monitor-upload-form" class="monitor-form">
      ${scheduleOptions ? `<label>Inspection schedule (optional)<select id="monitor-follow-up-schedule"><option value="">General inspection</option>${scheduleOptions}</select></label>` : ''}
      <label>Disease close-up image<input id="monitor-upload-file" type="file" accept="image/jpeg,image/png" required></label>
      <label>Whole-tree reference photo (optional)<input id="monitor-upload-tree-file" type="file" accept="image/jpeg,image/png"></label>
      <p class="monitor-muted">Include the trunk, crown, and surrounding landmarks of this same tree. A separate whole-tree photo is needed for identity confirmation; GPS is optional.</p>
      <label>Your visual observation<select id="monitor-preliminary"><option value="needs_review">Needs Review</option><option value="improving">Improving</option><option value="no_visible_change">No Visible Change</option><option value="worsening">Worsening</option></select></label>
      <button class="btn btn-p" type="submit">Save inspection</button></form></section>` : '';
  const reviewForm = currentUserIsExpert && selected ? `<section class="monitor-card"><h3 class="monitor-section-heading"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 12l5 5L20 6"/></svg>Treatment-outcome review · ${monitoringDate(selected.created_at)}</h3><p class="monitor-muted" style="margin-top:7px;">Compare the evidence and record the visible outcome. Model confidence is not a measure of recovery.</p>
    <form id="monitor-review-form" class="monitor-form" data-follow-up-id="${monitoringText(selected.id)}">
      <label>Confirmed visual outcome<select id="monitor-outcome" required><option value="">Choose an outcome</option><option value="improving">Improving</option><option value="no_visible_change">No Visible Change</option><option value="worsening">Worsening</option></select></label>
      <label>Review note<textarea id="monitor-review-note" rows="3" required maxlength="2000" placeholder="Describe visible evidence and any uncertainty"></textarea></label>
      <button class="btn btn-p" type="submit">Record expert review</button></form></section>` : '';
  const latestApplication = treatmentApplications[treatmentApplications.length - 1] || null;
  const recommendationCompare = `<section class="monitor-card monitor-treatment-compare"><h3 class="monitor-section-heading">Recommendation and actual treatment</h3>
    <div class="monitor-compare-grid"><div><span class="monitor-compare-label">Expert recommendation</span><p class="monitor-treatment-copy">${monitoringText(advice.recommendations?.treatment || 'No treatment text is available for this care record.')}</p></div>
    <div><span class="monitor-compare-label">Latest farmer-reported application</span>${latestApplication
      ? `<p class="monitor-treatment-copy">${monitoringText(latestApplication.treatment_performed)}</p><p class="monitor-muted">Applied ${monitoringDate(latestApplication.application_date)}</p>`
      : '<p class="monitor-muted">No completed treatment has been recorded. The recommendation remains pending.</p>'}</div></div></section>`;
  const scheduleHistoryPanel = `<section class="monitor-card"><h3 class="monitor-section-heading">Inspection schedules</h3><p class="monitor-muted">Disease-progression checks remain separate from inspections scheduled after an application.</p><ol class="monitor-treatment-history">${scheduleHistory}</ol></section>`;
  const plannedHistoryPanel = `<section class="monitor-card"><h3 class="monitor-section-heading">Planned treatment dates</h3><ol class="monitor-treatment-history">${plannedHistory}</ol></section>`;
  const progressionScheduleForm = currentUserIsExpert ? `<section class="monitor-card"><h3 class="monitor-section-heading">Disease-progression schedule</h3><p class="monitor-muted" style="margin-top:7px;">This tracks untreated disease progression separately from post-treatment inspections. The configurable interval also sets the post-treatment interval; the default is six days.</p><form id="monitor-schedule-form" class="monitor-form">
    <label>Next progression inspection<input id="monitor-next-date" type="date" value="${monitoringText(item.next_follow_up_date || '')}" required></label>
    <label>Interval in days<input id="monitor-schedule-interval" type="number" min="1" max="90" value="${monitoringText(item.follow_up_interval_days || 6)}" required></label>
    <button class="btn btn-o" type="submit">Update schedule</button></form></section>` : '';
  document.getElementById('monitor-detail').innerHTML = `<section class="monitor-card">
    <div class="monitor-treatment-state"><span class="monitor-pill ${lifecycle.cls}">${monitoringText(lifecycle.text)}</span><span class="monitor-muted">${monitoringText(lifecycle.detail)}</span></div>
    <div class="monitor-head"><div><p class="monitor-case-label">${monitoringText(item.tree_id)} · ${monitoringText(item.disease)}</p><h2>Care record</h2>
    <p class="monitor-muted" style="margin-top:5px;">Baseline ${monitoringDate(overview.baseline?.timestamp || item.created_at)} · ${followUps.length} follow-up${followUps.length === 1 ? '' : 's'} recorded</p></div>
    <div class="monitor-next-check"><span class="monitor-pill ${due.cls}">${monitoringText(due.text)}</span><small>Next check · ${monitoringDate(item.next_follow_up_date)}</small></div></div>
    <div class="monitor-comparison"><figure>${original ? `${originalPreview}` : fallback}<figcaption>Baseline image</figcaption></figure>
    <figure>${after ? `${afterPreview}` : fallback}<figcaption>${selected ? `Latest inspection · ${monitoringDate(selected.created_at)}` : 'Awaiting first inspection'}</figcaption></figure></div>
    <p class="monitor-observation"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/></svg><span>${monitoringText(review)}. Use the image comparison as inspection evidence; model confidence does not establish severity or recovery.</span></p></section>
    <div class="monitor-detail-grid"><section class="monitor-card"><h3 class="monitor-section-heading"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 20V10M12 10C7.6 10 5.6 7.3 5.6 4.6 9.1 4.5 11.3 6.6 12 10zm0 0c4.4 0 6.4-2.7 6.4-5.4C14.9 4.5 12.7 6.6 12 10zM7 20h10"/></svg>Recorded treatment plan</h3><p class="monitor-muted" style="margin-top:7px;">Saved with the verified baseline · ${monitoringDate(overview.treatment?.recorded_at)} · ${monitoringText(advice.model_used || advice.source || 'Record snapshot')}</p>
    <p class="monitor-treatment-copy">${monitoringText(advice.recommendations?.treatment || 'No treatment text is available for this care record.')}</p>
    ${advice.note ? `<p class="monitor-observation"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/></svg><span>${monitoringText(advice.note)}</span></p>` : ''}</section>
    <section class="monitor-card"><h3 class="monitor-section-heading"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 4v16M5 12h5l2-3 2 3h5"/></svg>Inspection timeline</h3><ol class="monitor-timeline">${timeline}</ol></section></div>
    <section class="monitor-card monitor-applied-history"><div class="monitor-history-head"><div><h3 class="monitor-section-heading"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 20V10M12 10C7.6 10 5.6 7.3 5.6 4.6 9.1 4.5 11.3 6.6 12 10zm0 0c4.4 0 6.4-2.7 6.4-5.4C14.9 4.5 12.7 6.6 12 10zM7 20h10"/></svg>Applied treatment history</h3><p class="monitor-muted">Actual applications recorded by the farmer, shown separately from the treatment plan.</p></div><span class="monitor-pill">${treatmentApplications.length} logged</span></div><ol class="monitor-treatment-history">${applicationHistory}</ol></section>
    ${monitoringIdentityPanel(overview, selected)}
    ${recommendationCompare}${plannedHistoryPanel}${scheduleHistoryPanel}
    ${plannedForm}${applicationForm}${uploadForm}${reviewForm}${progressionScheduleForm}`;
  monitoringLoadIdentityMedia();
  monitoringBindIdentityHistory();
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
  document.getElementById('monitor-detail')?.addEventListener('click', event => {
    const button = event.target.closest('[data-monitor-follow-up]');
    if (button && monitoringState.overview) monitoringRenderOverview(monitoringState.overview,
      monitoringState.overview.follow_ups[Number(button.dataset.monitorFollowUp)]?.id);
  });
  document.getElementById('monitor-create-form')?.addEventListener('submit', async event => {
    event.preventDefault();
    const form = event.target, button = form.querySelector('button[type=submit]');
    const record = monitoringState.expertRecords[Number(document.getElementById('monitor-baseline').value)];
    if (!record) return;
    button.disabled = true;
    try {
      const created = await monitoringRequest('/expert/monitoring/cases', { method: 'POST',
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ farmer_id: record.user_id,
          tree_id: document.getElementById('monitor-tree-id').value.trim(), baseline_detection_id: record.id,
          follow_up_interval_days: Number(document.getElementById('monitor-interval').value) }) });
      monitoringState.selected = created.id;
      const referenceFile = document.getElementById('monitor-create-tree-file')?.files[0];
      form.reset();
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
    if (!['monitor-treatment-application-form', 'monitor-treatment-schedule-form', 'monitor-upload-form', 'monitor-review-form', 'monitor-schedule-form', 'monitor-tree-reference-form', 'monitor-identity-review-form'].includes(form.id)) return;
    event.preventDefault();
    const item = monitoringState.overview?.case;
    if (!item) return;
    const button = form.querySelector('button[type=submit]');
    button.disabled = true;
    try {
      if (form.id === 'monitor-tree-reference-form') {
        await monitoringUploadBaseline(item, document.getElementById('monitor-tree-baseline-file').files[0]);
        showToast('Baseline whole-tree reference registered');
      } else if (form.id === 'monitor-identity-review-form') {
        await monitoringRequest(`${monitoringIdentityBase(item)}/follow-ups/${encodeURIComponent(form.dataset.followUpId)}/identity-review`, {
          method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({
            status: document.getElementById('monitor-identity-status').value,
            explanation: document.getElementById('monitor-identity-explanation').value.trim() || null,
            expected_revision: Number(form.dataset.revision),
            baseline_reference_id: form.dataset.baselineId || null
          })
        });
        showToast('Tree identity review saved');
      } else if (form.id === 'monitor-treatment-schedule-form') {
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
      } else {
        await monitoringRequest(`/expert/monitoring/cases/${encodeURIComponent(item.farmer_id)}/${encodeURIComponent(item.id)}/schedule`,
          { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({
            next_follow_up_date: document.getElementById('monitor-next-date').value,
            follow_up_interval_days: Number(document.getElementById('monitor-schedule-interval').value) }) });
        showToast('Follow-up schedule updated');
      }
      await loadMonitoringDashboard();
      if (form.dataset.followUpId && monitoringState.overview?.case.id === item.id) {
        monitoringRenderOverview(monitoringState.overview, form.dataset.followUpId);
      }
    } catch (error) { showToast(`Monitoring update failed: ${error.message}`); }
    finally { button.disabled = false; }
  });
});
