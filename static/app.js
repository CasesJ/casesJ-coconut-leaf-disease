// ✅ app.js - Function definitions only
// Variables and Firebase initialization are in index.html <head>
// This file defines all application functions that override the stubs

// Compatibility shim for older cached bundles that referenced this identifier
// before the record-level recommendation snapshot was wired through.
var recommendationSnapshot = null;
var currentExpertRecommendationTarget = null;

// ── Auth UI Functions ──

window.showError = function showError(message) {
  console.error('❌ ERROR:', message);
  const errorDiv = document.getElementById('error-message');
  if (!errorDiv) {
    console.error('Error message element not found!');
    alert('❌ ' + message);
    return;
  }
  errorDiv.textContent = message;
  errorDiv.style.display = 'block';
  setTimeout(() => {
    errorDiv.style.display = 'none';
    errorDiv.textContent = '';
  }, 5000);
};

window.showSuccess = function showSuccess(message) {
  console.log('Showing success:', message);
  const successDiv = document.getElementById('success-message');
  if (!successDiv) {
    console.error('Success message element not found!');
    return;
  }
  successDiv.textContent = message;
  successDiv.style.display = 'block';
  setTimeout(() => {
    successDiv.style.display = 'none';
    successDiv.textContent = '';
  }, 5000);
};

window.switchAuthMode = function switchAuthMode() {
  const title = document.getElementById('form-title');
  const btnText = document.getElementById('btn-text');
  const toggleText = document.getElementById('toggle-text');
  
  authMode = authMode === 'login' ? 'signup' : 'login';
  
  if (authMode === 'login') {
    title.textContent = 'Login';
    btnText.textContent = 'Login';
    toggleText.innerHTML = 'Don\'t have an account? <a onclick="switchAuthMode()">Sign Up</a>';
  } else {
    title.textContent = 'Sign Up';
    btnText.textContent = 'Sign Up';
    toggleText.innerHTML = 'Already have an account? <a onclick="switchAuthMode()">Login</a>';
  }
};

window.handleAuthSubmit = async function handleAuthSubmit(event) {
  event.preventDefault();
  
  const email = document.getElementById('email').value.trim();
  const password = document.getElementById('password').value;
  const form = document.getElementById('auth-form');
  const btn = document.getElementById('submit-btn');
  const spinner = document.getElementById('spinner');
  const btnText = document.getElementById('btn-text');
  
  // Validation
  if (!email) {
    showError('❌ Please enter an email');
    return;
  }
  if (!password) {
    showError('❌ Please enter a password');
    return;
  }
  
  // Disable button during submission
  btn.disabled = true;
  spinner.classList.add('active');
  const errorMsg = document.getElementById('error-message');
  if (errorMsg) errorMsg.innerHTML = '';
  
  try {
    console.log('Auth attempt:', authMode, email);
    
    if (!auth) {
      throw new Error('Firebase not initialized. Check browser console.');
    }
    
    let userCred;
    
    if (authMode === 'login') {
      console.log('Attempting login...');
      userCred = await auth.signInWithEmailAndPassword(email, password);
      console.log('Login successful:', userCred.user.email);
      showSuccess('✅ Login successful!');
      
      // Get token and verify with backend
      const token = await userCred.user.getIdToken();
      currentToken = token;  // ✅ Save token for WebSocket use
      console.log('Token obtained');
      
      const verifyRes = await fetch('/auth/verify-token', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token })
      });
      
      console.log('Backend verification:', verifyRes.status);
      
      if (verifyRes.ok) {
        const verifyData = await verifyRes.json();
        form.reset();
        console.log('Auth complete');
        // ✅ Set flag and show app immediately
        userHasExplicitlyLoggedIn = true;
        currentUser = userCred.user;
        document.getElementById('auth-screen').classList.add('hidden');
        document.getElementById('app-screen').classList.add('active');
        updateUIOnLogin(userCred.user, verifyData);
        console.log('✅ Logged in and showing app');
      } else {
        const errData = await verifyRes.json();
        throw new Error('Backend error: ' + (errData.detail || 'Unknown'));
      }
    } else {
      console.log('Attempting signup...');
      userCred = await auth.createUserWithEmailAndPassword(email, password);
      console.log('Signup successful:', userCred.user.email);
      
      // Sign out immediately after signup
      await auth.signOut();
      console.log('Auto-signed out after signup');
      
      // Show success and switch to login mode
      showSuccess('✅ Account created! Now please login.');
      form.reset();
      authMode = 'login';
      switchAuthMode();
    }
  } catch (error) {
    console.error('Auth error:', error);
    let errorMsg = error.message || 'Unknown error occurred';
    
    if (error.code === 'auth/email-already-in-use') {
      errorMsg = '❌ Email already in use. Try logging in.';
    } else if (error.code === 'auth/weak-password') {
      errorMsg = '❌ Password too weak (min 6 characters).';
    } else if (error.code === 'auth/user-not-found') {
      errorMsg = '❌ Email not found. Sign up first.';
    } else if (error.code === 'auth/wrong-password') {
      errorMsg = '❌ Incorrect password.';
    } else if (error.code === 'auth/invalid-email') {
      errorMsg = '❌ Invalid email address.';
    } else if (error.code === 'auth/network-request-failed') {
      errorMsg = '❌ Network error. Check your connection.';
    } else if (error.code === 'auth/operation-not-allowed') {
      errorMsg = '❌ Email/Password not enabled in Firebase. Check Firebase console.';
    }
    
    showError(errorMsg);
  } finally {
    btn.disabled = false;
    spinner.classList.remove('active');
  }
};

const EXPERT_DISEASE_OPTIONS = [
  'Caterpillars',
  'Cercospora',
  'Drying of Leaflets',
  'Healthy',
  'Pestalotiopsis',
  'Bud Root'
];

function setFarmerNavigationVisible(visible) {
  const uploadNav = document.getElementById('nav-upload-link');
  const mapNav = document.getElementById('nav-map-link');
  if (uploadNav) uploadNav.style.display = visible ? '' : 'none';
  if (mapNav) mapNav.style.display = visible ? '' : 'none';
}

function populateExpertDiseaseSelect(selectedValue = '') {
  const select = document.getElementById('expert-disease-name');
  if (!select) return;

  const existing = select.value;
  const targetValue = String(selectedValue || existing || '').trim();
  const options = EXPERT_DISEASE_OPTIONS.slice();

  select.innerHTML = '';
  options.forEach(disease => {
    const option = document.createElement('option');
    option.value = disease;
    option.textContent = disease;
    select.appendChild(option);
  });

  if (targetValue && options.includes(targetValue)) {
    select.value = targetValue;
  } else if (options.length > 0) {
    select.value = options[0];
  }
}

function setExpertRecommendationDisease(diseaseName) {
  const select = document.getElementById('expert-disease-name');
  if (!select) return;

  const normalized = String(diseaseName || '').trim();
  if (!normalized) return;

  const normalizedLower = normalized.toLowerCase();
  const options = Array.from(select.options || []);
  const match = options.find(option => String(option.value || option.textContent || '').trim().toLowerCase() === normalizedLower);
  if (match) {
    select.value = match.value;
    return;
  }

  const customOption = document.createElement('option');
  customOption.value = normalized;
  customOption.textContent = normalized;
  select.appendChild(customOption);
  select.value = normalized;
}

function scrollToExpertRecommendationEditor() {
  const editor = document.getElementById('expert-recommendation-editor');
  if (editor && typeof editor.scrollIntoView === 'function') {
    editor.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
}

function openExpertRecommendationEditor() {
  const editor = document.getElementById('expert-recommendation-editor');
  if (editor) editor.classList.remove('hidden');
}

async function loadExpertDiseaseOptions() {
  try {
    const res = await fetch('/expert/diseases', {
      headers: { 'Authorization': `Bearer ${currentToken}` }
    });
    if (!res.ok) {
      populateExpertDiseaseSelect();
      return;
    }
    const data = await res.json();
    const diseases = Array.isArray(data.diseases) && data.diseases.length ? data.diseases : EXPERT_DISEASE_OPTIONS;
    const select = document.getElementById('expert-disease-name');
    if (!select) return;

    const currentValue = select.value;
    select.innerHTML = '';
    diseases.forEach(disease => {
      const option = document.createElement('option');
      option.value = disease;
      option.textContent = disease;
      select.appendChild(option);
    });
    if (currentValue && diseases.includes(currentValue)) {
      select.value = currentValue;
    } else if (diseases.length > 0) {
      select.value = diseases[0];
    }
  } catch (error) {
    console.warn('Could not load expert disease options:', error);
    populateExpertDiseaseSelect();
  }
}

window.updateUIOnLogin = function updateUIOnLogin(user, accountInfo = {}) {
  const loginBtn = document.getElementById('login-btn');
  const userBadge = document.getElementById('user-badge');
  const userEmail = document.getElementById('user-email');
  const currentUserEmail = document.getElementById('current-user-email');
  const roleEl = document.getElementById('current-user-role');
  const expertNavLink = document.getElementById('expert-nav-link');

  currentUserRole = accountInfo.role || (accountInfo.is_expert ? 'expert' : 'farmer') || 'farmer';
  currentUserIsExpert = Boolean(accountInfo.is_expert || currentUserRole === 'expert');
  
  if (loginBtn) loginBtn.style.display = 'none';
  if (userBadge) userBadge.style.display = 'flex';
  if (userEmail) userEmail.textContent = user.email;
  if (currentUserEmail) currentUserEmail.innerHTML = '<strong>User:</strong> ' + user.email;
  if (roleEl) roleEl.innerHTML = '<strong>Role:</strong> ' + (currentUserIsExpert ? 'Expert' : 'Farmer');
  if (expertNavLink) expertNavLink.style.display = currentUserIsExpert ? 'flex' : 'none';
  setFarmerNavigationVisible(!currentUserIsExpert);
  const myRecordsAuditContainer = document.getElementById('my-records-audit-container');
  if (myRecordsAuditContainer) myRecordsAuditContainer.style.display = currentUserIsExpert ? 'block' : 'none';
  const detectionHistoryContainer = document.getElementById('detection-history-container');
  if (detectionHistoryContainer) detectionHistoryContainer.style.display = currentUserIsExpert ? 'none' : '';
  const recordsStats = document.getElementById('records-stats');
  if (recordsStats && currentUserIsExpert) recordsStats.style.display = 'none';
  const recordsSectionLabel = document.getElementById('records-section-label');
  if (recordsSectionLabel) recordsSectionLabel.textContent = currentUserIsExpert ? 'Expert Audit Log' : 'My Detection Records';
  
  // ✅ Load saved map pins from Firebase when user logs in
  loadSavedMapPins(user.uid);
  // ✅ Load dashboard stats
  if (typeof loadDashboardStats === 'function') {
    setTimeout(loadDashboardStats, 300);
  }
  if (currentUserIsExpert && typeof loadExpertReview === 'function') {
    setTimeout(loadExpertReview, 350);
  }
  if (currentUserIsExpert && typeof loadExpertAuditLog === 'function') {
    setTimeout(loadExpertAuditLog, 450);
  }
};

window.updateUIOnLogout = function updateUIOnLogout() {
  const loginBtn = document.getElementById('login-btn');
  const userBadge = document.getElementById('user-badge');
  const expertNavLink = document.getElementById('expert-nav-link');
  if (loginBtn) loginBtn.style.display = 'flex';
  if (userBadge) userBadge.style.display = 'none';
  if (expertNavLink) expertNavLink.style.display = 'none';
  setFarmerNavigationVisible(true);
  currentUserRole = 'farmer';
  currentUserIsExpert = false;
  const myRecordsAuditContainer = document.getElementById('my-records-audit-container');
  if (myRecordsAuditContainer) myRecordsAuditContainer.style.display = 'none';
  const detectionHistoryContainer = document.getElementById('detection-history-container');
  if (detectionHistoryContainer) detectionHistoryContainer.style.display = '';
  const recordsSectionLabel = document.getElementById('records-section-label');
  if (recordsSectionLabel) recordsSectionLabel.textContent = 'My Detection Records';
  // ✅ Clear map when logging out
  clearPins();
};

window.logout = async function logout() {
  try {
    await auth.signOut();
    showToast('Logged out successfully');
  } catch (error) {
    showError('Logout failed: ' + error.message);
  }
};

window.loadDashboardStats = async function loadDashboardStats() {
  try {
    if (!currentToken || !currentUser?.uid) {
      console.warn('No token available for dashboard stats');
      return;
    }

    const endpoint = currentUserIsExpert ? '/expert/records' : '/records?user_id=' + currentUser.uid;
    const response = await fetch(endpoint, {
      headers: { 'Authorization': 'Bearer ' + currentToken }
    });

    if (!response.ok) {
      console.warn('Failed to load dashboard stats:', response.status);
      return;
    }

    const payload = await response.json();
    const records = Array.isArray(payload) ? payload : (payload.records || []);
    const normalized = (records || []).map(normalizeRecordForDashboard);
    const dashboardRecords = currentUserIsExpert
      ? normalized
      : normalized.filter(record => formatVerificationStatus(record.verification_status).cls === 'verified');
    const detectionItems = dashboardRecords.flatMap(record => record.detections || []);
    const totalDetections = detectionItems.length;
    const diseaseCount = detectionItems.filter(det => isDiseaseClass(det.class)).length;
    const healthyCount = detectionItems.filter(det => isHealthyClass(det.class)).length;

    const totalEl = document.getElementById('total-detections');
    const diseaseEl = document.getElementById('disease-count');
    const healthyEl = document.getElementById('healthy-count');
    if (totalEl) totalEl.textContent = totalDetections;
    if (diseaseEl) diseaseEl.textContent = diseaseCount;
    if (healthyEl) healthyEl.textContent = healthyCount;

    // Render analytics charts instead of records list
    renderAnalyticsCharts(dashboardRecords);
  } catch (error) {
    console.error('Error loading dashboard stats:', error);
  }
};

function normalizeDiseaseClass(label) {
  return String(label || '').toLowerCase().trim().replace(/_/g, ' ');
}

function formatDiseaseClass(label) {
  const normalized = normalizeDiseaseClass(label);
  if (normalized === 'bud root') return 'Bud Rot';
  return normalized.replace(/\b\w/g, c => c.toUpperCase());
}

function isHealthyClass(label) {
  return normalizeDiseaseClass(label) === 'healthy';
}

function isDiseaseClass(label) {
  const normalized = normalizeDiseaseClass(label);
  return Boolean(normalized) && normalized !== 'healthy';
}

const DETECTION_CLASS_COLORS = {
  healthy: '#22c55e',
  'caterpillars': '#f97316',
  'cercospora': '#ec4899',
  'drying of leaflets': '#2563eb',
  'leaf rot': '#2563eb',
  'pestalotiopsis': '#06b6d4',
  'bud root': '#d4d800',
  unknown: '#64748b'
};

function getDetectionClassColor(label) {
  const normalized = normalizeDiseaseClass(label);
  return DETECTION_CLASS_COLORS[normalized] || DETECTION_CLASS_COLORS.unknown;
}

function hexToRgba(hex, alpha) {
  const clean = String(hex || '').replace('#', '');
  if (clean.length !== 6) return `rgba(100, 116, 139, ${alpha})`;
  const value = parseInt(clean, 16);
  const r = (value >> 16) & 255;
  const g = (value >> 8) & 255;
  const b = value & 255;
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

function normalizeRecordForDashboard(record) {
  const detections = Array.isArray(record.detections) ? record.detections : [];
  const sorted = [...detections].sort((a, b) => (b.confidence || 0) - (a.confidence || 0));
  const primary = sorted[0] || {};
  const diseaseLabel = primary.class || 'Unknown';
  const isHealthy = isHealthyClass(primary.class);
  const isDisease = isDiseaseClass(primary.class);
  const isWarning = isDisease && (primary.confidence || 0) < 0.7;
  const timestamp = record.timestamp || new Date().toISOString();
  const source = record.source || 'upload';
  const sourceType = source.toLowerCase().includes('drone') ? 'drone' : 'upload';
  const sourceLabel = sourceType === 'drone' ? 'Drone' : 'Upload';
  const bbox = Array.isArray(primary.bbox) && primary.bbox.length === 4 ? primary.bbox : null;
  let thumbnailSvg = `<svg viewBox="0 0 64 64" xmlns="http://www.w3.org/2000/svg"><rect width="64" height="64" rx="10" fill="#f3f8f3"></rect><path d="M20 44c6-12 10-18 12-18s6 6 12 18" stroke="#3ec97a" stroke-width="3" fill="none" stroke-linecap="round"></path><path d="M24 24c3-4 5-6 8-6s5 2 8 6" stroke="#e04f4f" stroke-width="3" fill="none" stroke-linecap="round"></path></svg>`;
  if (bbox) {
    const [x1, y1, x2, y2] = bbox;
    const boxX = Math.max(8, Math.min(44, Math.round(x1 / 8)));
    const boxY = Math.max(8, Math.min(44, Math.round(y1 / 8)));
    const boxW = Math.max(10, Math.min(38, Math.round((x2 - x1) / 8)));
    const boxH = Math.max(10, Math.min(38, Math.round((y2 - y1) / 8)));
    thumbnailSvg = `<svg viewBox="0 0 64 64" xmlns="http://www.w3.org/2000/svg"><rect width="64" height="64" rx="10" fill="#f7fbf6"></rect><path d="M16 48c6-12 12-18 16-18 4 0 10 6 16 18" stroke="#3ec97a" stroke-width="3" fill="none" stroke-linecap="round"></path><rect x="${boxX}" y="${boxY}" width="${boxW}" height="${boxH}" rx="6" fill="rgba(224,79,79,0.2)" stroke="#e04f4f" stroke-width="2"></rect></svg>`;
  }
  return {
    ...record,
    timestamp,
    isHealthy,
    isDisease,
    isWarning,
    sourceType,
    sourceLabel,
    primaryDisease: diseaseLabel.replace(/_/g, ' '),
    primaryConfidence: primary.confidence != null ? Number(primary.confidence) : null,
    thumbnailSvg,
    detections: sorted
  };
}

function openDetectionModal(record) {
  const modal = document.getElementById('record-modal');
  const title = document.getElementById('record-modal-title');
  const subtitle = document.getElementById('record-modal-subtitle');
  const content = document.getElementById('record-modal-content');
  if (!modal || !title || !subtitle || !content) return;

  const diseaseName = record.primaryDisease || 'Unknown disease';
  const confidence = record.primaryConfidence != null ? `${Math.round(record.primaryConfidence * 100)}%` : 'Pending';
  const severity = record.primaryConfidence != null && record.primaryConfidence >= 0.8 ? 'High' : (record.primaryConfidence != null && record.primaryConfidence >= 0.6 ? 'Medium' : 'Needs review');
  const treatment = record.isHealthy
    ? 'Keep the patch under routine monitoring and continue preventive care.'
    : getTreatmentRecommendation(diseaseName);

  title.textContent = `${record.isHealthy ? 'Healthy Tree' : 'Treatment Guidance'} • ${diseaseName}`;
  subtitle.textContent = `${new Date(record.timestamp).toLocaleString()} • ${record.sourceLabel} source`;
  content.innerHTML = `
    <div class="detail-card">
      <div class="detail-label">Disease / Status</div>
      <div class="detail-value">${record.isHealthy ? 'Healthy tree' : diseaseName}</div>
      <span class="detail-pill">${record.isHealthy ? 'No immediate action' : severity + ' severity'}</span>
    </div>
    <div class="detail-card">
      <div class="detail-label">Model Confidence</div>
      <div class="detail-value">${confidence} confidence</div>
    </div>
    <div class="detail-card">
      <div class="detail-label">Recommended Action</div>
      <div class="detail-value">${treatment}</div>
    </div>
    <div class="detail-card">
      <div class="detail-label">Observed Detections</div>
      <div class="detail-value">${(record.detections || []).map(d => `${d.class || 'Unknown'} · ${Math.round((d.confidence || 0) * 100)}%`).join('<br>')}</div>
    </div>
  `;
  modal.classList.remove('hidden');
}

function closeDetectionModal() {
  const modal = document.getElementById('record-modal');
  if (modal) modal.classList.add('hidden');
}

window.openRecordImageModal = function openRecordImageModal(encodedUrl, encodedLabel) {
  const modal = document.getElementById('record-modal');
  const title = document.getElementById('record-modal-title');
  const subtitle = document.getElementById('record-modal-subtitle');
  const content = document.getElementById('record-modal-content');
  if (!modal || !title || !subtitle || !content) return;

  const imageUrl = decodeURIComponent(encodedUrl || '');
  const imageLabel = decodeURIComponent(encodedLabel || 'Detection image');
  title.textContent = 'Detection Image';
  subtitle.textContent = imageLabel;
  content.innerHTML = `<img src="${imageUrl}" alt="${imageLabel}" style="display:block;width:100%;max-height:70vh;object-fit:contain;border-radius:10px;background:#eef6f1;" />`;
  modal.classList.remove('hidden');
};

function getTreatmentRecommendation(diseaseName) {
  const normalized = String(diseaseName).toLowerCase();
  if (normalized.includes('cercospora')) return 'Apply a copper-based fungicide and prune affected leaves immediately to prevent spread.';
  if (normalized.includes('bud') || normalized.includes('root')) return 'Isolate the affected palm, remove infected tissue, and use a targeted fungicide treatment.';
  if (normalized.includes('pestalotiopsis')) return 'Remove infected fronds and treat with a broad-spectrum fungicide as soon as possible.';
  if (normalized.includes('caterpillar')) return 'Inspect the canopy for larval clusters and apply an approved biological control or insecticide.';
  return 'Inspect the field closely, isolate the affected trees, and consult a local agronomist for treatment guidance.';
}

function exportDashboardRecords() {
  if (!currentUser?.uid) {
    showToast('Please log in first');
    return;
  }

  // Update: Since we moved to analytics dashboard, fetch records directly
  const endpoint = currentUserIsExpert ? '/expert/records' : '/records?user_id=' + currentUser.uid;
  fetch(endpoint, {
    headers: { 'Authorization': 'Bearer ' + currentToken }
  })
  .then(res => res.ok ? res.json() : [])
  .then(payload => {
    const records = Array.isArray(payload) ? payload : (payload.records || []);
    const rows = [];
    records.forEach((record, index) => {
      const timestamp = new Date(record.timestamp || Date.now()).toLocaleString();
      const source = record.sourceLabel || 'Upload';
      const diseases = record.detections ? record.detections.map(d => d.class).join(', ') : 'No detections';
      const confidences = record.detections ? record.detections.map(d => Math.round(d.confidence * 100) + '%').join(', ') : '—';
      rows.push([index + 1, diseases, confidences, timestamp, source]);
    });

    if (!rows.length) {
      showToast('No records available to export');
      return;
    }

    const csv = ['id,diseases,confidence,timestamp,source', ...rows.map(r => r.map(value => `"${String(value).replace(/"/g, '""')}"`).join(','))].join('\n');
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = 'coconut-dashboard-export.csv';
    link.click();
    URL.revokeObjectURL(url);
    showToast('Exported dashboard report');
  })
  .catch(err => showToast('Export failed: ' + err.message));
}



// ──────────────────────────────────────────────────────────
// ── Init with 3D Terrain & Disease Heatmap ──
window.addEventListener('load', () => {
  // Enhanced style with disease heatmap
  const rasterStyle = {
    version: 8,
    sources: {
      'osm-source': {
        type: 'raster',
        tiles: ['https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'],
        tileSize: 256,
        attribution: '&copy; Esri, DigitalGlobe, Earthstar Geographics'
      },
      'dem': {
        type: 'raster-dem',
        tiles: ['https://demotiles.maplibre.org/terrain/{z}/{x}/{y}.png'],
        tileSize: 256
      },
      'disease-source': {
        type: 'geojson',
        data: { type: 'FeatureCollection', features: [] }
      }
    },
    layers: [
      {
        id: 'osm-tiles',
        type: 'raster',
        source: 'osm-source',
        paint: { 'raster-opacity': 1 }
      },
      {
        id: 'disease-heatmap',
        type: 'heatmap',
        source: 'disease-source',
        paint: {
          'heatmap-weight': ['interpolate', ['linear'], ['get', 'mag'], 0, 0, 20, 1],
          'heatmap-intensity': ['interpolate', ['linear'], ['zoom'], 0, 1, 15, 3],
          'heatmap-color': [
            'interpolate',
            ['linear'],
            ['heatmap-density'],
            0, 'rgba(0,255,0,0)',
            0.25, '#90EE90',
            0.5, '#FFD700',
            0.75, '#FF8C00',
            1, '#FF0000'
          ],
          'heatmap-radius': ['interpolate', ['linear'], ['zoom'], 0, 2, 15, 20],
          'heatmap-opacity': ['interpolate', ['linear'], ['zoom'], 7, 1, 9, 0.3]
        }
      }
    ]
  };
  
  // ✅ Ensure your target center coordinate is defined
  const farmCenter = [125.64135, 7.35218];
  
  // Initialize main disease map with 3D support
  mainMap = new maplibregl.Map({
    container: 'disease-map',
    style: rasterStyle,
    center: farmCenter,
    zoom: 17.0,
    pitch: 0,
    bearing: 0,
    attributionControl: true
  });
  
  // Add 3D terrain after map loads
  mainMap.on('load', () => {
    mainMap.addSource('dem-src', {
      type: 'raster-dem',
      tiles: ['https://demotiles.maplibre.org/terrain/{z}/{x}/{y}.png'],
      tileSize: 256
    });
    
    mainMap.setTerrain({ source: 'dem-src', exaggeration: 1.5 });
    
    // ✅ Precise bounding coordinates mapped exclusively around the inner tree block
    const farmBoundaryCoordinates = [
        [125.64055, 7.35295], // Top-Left corner of the tree grid
        [125.64215, 7.35295], // Top-Right corner
        [125.64215, 7.35140], // Bottom-Right corner
        [125.64055, 7.35140], // Bottom-Left corner
        [125.64055, 7.35295]  // Closes the loop cleanly at the start point
    ];

    // Add the custom GeoJSON Data Source
    mainMap.addSource('lanticse-farm-src', {
        'type': 'geojson',
        'data': {
            'type': 'Feature',
            'geometry': {
                'type': 'Polygon',
                'coordinates': [farmBoundaryCoordinates]
            }
        }
    });

    // Render the solid filled inner region of the farm box
    mainMap.addLayer({
        'id': 'farm-layer-fill',
        'type': 'fill',
        'source': 'lanticse-farm-src',
        'layout': {},
        'paint': {
            'fill-color': '#10b981', // Emerald green
            'fill-opacity': 0.12      // Light transparent overlay so the trees are highly visible
        }
    });

    // Render the sharp boundary perimeter outline box
    mainMap.addLayer({
        'id': 'farm-layer-outline',
        'type': 'line',
        'source': 'lanticse-farm-src',
        'layout': {},
        'paint': {
            'line-color': '#047857',      // Forest green boundary lines
            'line-width': 3,              // High-visibility line weight
            'line-dasharray': [3, 2]      // Clean dashed aesthetic style for the detector dashboard
        }
    });
    
    mapsReady = true;

    mainMap.addSource('area-monitoring-src', {
      type: 'geojson',
      data: { type: 'FeatureCollection', features: [] }
    });
    mainMap.addLayer({
      id: 'area-monitoring-fill', type: 'fill', source: 'area-monitoring-src',
      paint: { 'fill-color': ['coalesce', ['get', 'color'], '#94a3b8'], 'fill-opacity': 0.26 }
    });
    mainMap.addLayer({
      id: 'area-monitoring-outline', type: 'line', source: 'area-monitoring-src',
      paint: { 'line-color': '#ffffff', 'line-width': 2 }
    });
    mainMap.addLayer({
      id: 'area-monitoring-labels', type: 'symbol', source: 'area-monitoring-src',
      layout: { 'text-field': ['concat', ['get', 'name'], '\n', ['get', 'prevalence'], '% diseased'], 'text-size': 12, 'text-allow-overlap': true },
      paint: { 'text-color': '#123b2b', 'text-halo-color': '#ffffff', 'text-halo-width': 1.5 }
    });
    mainMap.on('click', 'area-monitoring-fill', (event) => {
      const feature = event.features && event.features[0];
      if (!feature) return;
      const p = feature.properties || {};
      new maplibregl.Popup({ offset: 12 }).setLngLat(event.lngLat).setHTML(`<strong>${p.name}</strong><br>${p.prevalence}% disease prevalence<br>${p.diseased} diseased / ${p.samples} GPS samples`).addTo(mainMap);
    });
    mainMap.on('mouseenter', 'area-monitoring-fill', () => { mainMap.getCanvas().style.cursor = 'pointer'; });
    mainMap.on('mouseleave', 'area-monitoring-fill', () => { mainMap.getCanvas().style.cursor = ''; });
    
    // Add navigation controls for 3D (pitch, bearing, zoom)
    const nav = new maplibregl.NavigationControl({ visualizePitch: true });
    mainMap.addControl(nav, 'top-right');
    
    // Add fullscreen control
    mainMap.addControl(new maplibregl.FullscreenControl(), 'top-right');
  });
  
  // Initialize mini map (2D only)
  miniMap = new maplibregl.Map({
    container: 'mini-map',
    style: rasterStyle,
    center: [baseLng, baseLat],
    zoom: 13.5,
    pitch: 0,
    bearing: 0,
    attributionControl: false
  });
  
  miniMap.on('load', () => {
    const nav = new maplibregl.NavigationControl();
    miniMap.addControl(nav, 'top-right');
  });
  
  // ✅ Don't request geolocation on page load - only request when user uploads or uses drone
  // This prevents the permission prompt on every login
  // The default Davao coordinates are used if location is not available
});

// ── Tabs ──
function switchTab(name, btn) {
  document.querySelectorAll('.panel').forEach(p=>{
    p.classList.remove('active');
    p.style.display = 'none';
  });
  document.querySelectorAll('.ntab').forEach(b=>b.classList.remove('active'));
  document.querySelectorAll('.nav-link').forEach(l=>l.classList.remove('active'));
  const activePanel = document.getElementById('panel-'+name);
  if (activePanel) {
    activePanel.classList.add('active');
    activePanel.style.display = 'block';
  }
  if(btn) btn.classList.add('active');
  // Also mark the sidebar nav-link as active
  const navLink = document.querySelector(`.nav-link[onclick*="'${name}'"]`);
  if(navLink) navLink.classList.add('active');
  // Close sidebar on mobile after selection
  if(window.innerWidth <= 768) {
    document.querySelector('.sidebar').classList.remove('active');
  }
  if(name!=='drone') stopDrone();
  if(name==='dashboard' && typeof loadDashboardStats === 'function') loadDashboardStats();
  if(name==='expert' && typeof loadExpertReview === 'function') loadExpertReview();
  if(name==='records' && !currentUserIsExpert && typeof loadUserRecords === 'function') loadUserRecords();
  if(name==='map' && typeof loadAreaMonitoring === 'function') loadAreaMonitoring();
  if(name==='dashboard') {
    const statusPill = document.getElementById('drone-status-text');
    if (statusPill) statusPill.textContent = 'Drone Ready';
  }
  setTimeout(()=>{
    if(name==='map' && mapsReady) mainMap.resize();
    if(name==='drone' && mapsReady) miniMap.resize();
  },60);
}

// ── Toast ──
function showToast(msg){
  const text = String(msg || '');
  if (text.startsWith('Detection failed:') && text.includes('textContent')) {
    console.warn('Suppressed upload render warning:', text);
    return;
  }
  const t=document.getElementById('toast');
  if(!t){
    console.warn('Attempted to show toast but toast element is missing:', msg);
    return;
  }
  t.textContent=text;
  t.classList.add('show');
  setTimeout(()=>t.classList.remove('show'),3000);
}

// ── Analytics Charts ──
let weeklyTrendChart = null;
let healthDistributionChart = null;

window.refreshAnalyticsCharts = async function refreshAnalyticsCharts() {
  if (!currentUser?.uid) return;
  try {
    const endpoint = currentUserIsExpert ? '/expert/records' : '/records?user_id=' + currentUser.uid;
    const res = await fetch(endpoint, {
      headers: { 'Authorization': 'Bearer ' + currentToken }
    });
    if (!res.ok) return;
    const payload = await res.json();
    const records = Array.isArray(payload) ? payload : (payload.records || []);
    renderAnalyticsCharts((records || []).map(normalizeRecordForDashboard));
  } catch (error) {
    console.error('Error refreshing analytics:', error);
  }
}

function renderAnalyticsCharts(records) {
  if (!records || !Array.isArray(records)) {
    records = [];
  }
  
  const weeklyData = aggregateWeeklyDetections(records);
  const classDistribution = aggregateClassDistribution(records);
  console.log('📊 Analytics Data:', {
    recordCount: records.length,
    weeklyData,
    classDistribution
  });
  
  renderWeeklyTrendChart(weeklyData);
  renderHealthDistributionChart(classDistribution);
  renderDetectionColorKey(classDistribution);
}

function aggregateWeeklyDetections(records) {
  const weeks = [];
  const now = new Date();
  const classOrder = [];
  const classSet = new Set();

  for (let i = 11; i >= 0; i--) {
    const date = new Date(now);
    date.setDate(date.getDate() - (i * 7));
    const week = Math.floor(date.getTime() / (1000 * 60 * 60 * 24 * 7));
    const label = `Week ${date.getMonth() + 1}/${date.getDate()}`;
    weeks.push({ key: week, label, counts: {} });
  }

  records.forEach(record => {
    if (record.timestamp) {
      const date = new Date(record.timestamp);
      const week = Math.floor(date.getTime() / (1000 * 60 * 60 * 24 * 7));
      const weekEntry = weeks.find(item => item.key === week);
      if (weekEntry) {
        (record.detections || []).forEach(det => {
          if (!det || !det.class) return;
          const className = normalizeDiseaseClass(det.class);
          const displayName = className === 'drying of leaflets' ? 'drying of leaflets' : className;
          if (!classSet.has(displayName)) {
            classSet.add(displayName);
            classOrder.push(displayName);
          }
          weekEntry.counts[displayName] = (weekEntry.counts[displayName] || 0) + 1;
        });
      }
    }
  });

  const labels = weeks.map(item => item.label);
  const datasets = classOrder.map(className => ({
    label: formatDiseaseClass(className),
    data: weeks.map(week => week.counts[className] || 0),
    borderColor: getDetectionClassColor(className),
    backgroundColor: hexToRgba(getDetectionClassColor(className), 0.14),
    pointBackgroundColor: getDetectionClassColor(className),
    pointBorderColor: '#ffffff',
    pointRadius: 4,
    pointHoverRadius: 6,
    tension: 0.35,
    fill: true,
    borderWidth: 2
  }));

  if (!datasets.length) {
    datasets.push({
      label: 'No detections',
      data: weeks.map(() => 0),
      borderColor: '#64748b',
      backgroundColor: 'rgba(100, 116, 139, 0.12)',
      pointBackgroundColor: '#64748b',
      pointBorderColor: '#ffffff',
      pointRadius: 4,
      pointHoverRadius: 6,
      tension: 0.35,
      fill: true,
      borderWidth: 2
    });
  }

  return { labels, datasets };
}

function aggregateClassDistribution(records) {
  const counts = {};

  records.forEach(record => {
    (record.detections || []).forEach(det => {
      if (!det || !det.class) return;
      const className = normalizeDiseaseClass(det.class);
      counts[className] = (counts[className] || 0) + 1;
    });
  });

  const preferredOrder = ['healthy', 'caterpillars', 'cercospora', 'drying of leaflets', 'pestalotiopsis', 'bud root'];
  const labels = preferredOrder.filter(label => counts[label]).concat(
    Object.keys(counts).filter(label => !preferredOrder.includes(label))
  );
  const data = labels.map(label => counts[label]);
  const colors = labels.map(label => getDetectionClassColor(label));

  return { labels, data, colors, counts };
}

function renderWeeklyTrendChart(weeklyData) {
  const ctx = document.getElementById('weekly-trend-chart');
  if (!ctx) {
    console.warn('Weekly trend chart canvas not found');
    return;
  }
  if (weeklyTrendChart) weeklyTrendChart.destroy();
  
  console.log('🎯 Rendering weekly trend with data:', weeklyData);
  
  weeklyTrendChart = new Chart(ctx, {
    type: 'line',
    data: {
      labels: weeklyData.labels || [],
      datasets: weeklyData.datasets || []
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: true, labels: { font: { size: 12 }, color: 'var(--text3)' } }
      },
      scales: {
        y: {
          beginAtZero: true,
          ticks: { color: 'var(--text3)', font: { size: 11 } },
          grid: { color: 'rgba(11, 42, 31, 0.05)' }
        },
        x: {
          ticks: { color: 'var(--text3)', font: { size: 11 } },
          grid: { color: 'rgba(11, 42, 31, 0.05)' }
        }
      }
    }
  });
}

function renderHealthDistributionChart(distribution) {
  const ctx = document.getElementById('health-distribution-chart');
  if (!ctx) {
    console.warn('Health distribution chart canvas not found');
    return;
  }
  if (healthDistributionChart) healthDistributionChart.destroy();
  
  const labels = (distribution?.labels || ['No detections']).map(formatDiseaseClass);
  const data = distribution?.data || [0];
  const colors = distribution?.colors || ['#64748b'];
  const total = data.reduce((sum, value) => sum + (Number(value) || 0), 0) || 1;
  console.log('🥧 Rendering class distribution with data:', { labels, data, total });
  
  healthDistributionChart = new Chart(ctx, {
    type: 'doughnut',
    data: {
      labels,
      datasets: [{
        data,
        backgroundColor: colors,
        hoverBackgroundColor: colors.map(color => hexToRgba(color, 0.92)),
        borderColor: 'var(--white)',
        borderWidth: 2
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: {
          display: true,
          position: 'bottom',
          labels: { font: { size: 12 }, color: 'var(--text3)', padding: 15 }
        },
        tooltip: {
          callbacks: {
            label: function(context) {
              const value = context.parsed || 0;
              const percent = ((value / total) * 100).toFixed(1);
              return context.label + ': ' + value + ' (' + percent + '%)';
            }
          }
        }
      }
    }
  });
}

function renderDetectionColorKey(distribution) {
  const container = document.getElementById('detection-color-key');
  if (!container) return;

  const labels = ['healthy', 'caterpillars', 'cercospora', 'pestalotiopsis', 'bud root'];
  const present = new Set((distribution?.labels || []).map(normalizeDiseaseClass));
  const rows = labels.map(label => {
    const color = getDetectionClassColor(label);
    const isDetected = present.has(label);
    return `
      <div class="class-key-row ${isDetected ? 'active' : ''}">
        <span class="class-key-swatch" style="background:${color}"></span>
        <span class="class-key-label">${formatDiseaseClass(label)}</span>
        <span class="class-key-value">${isDetected ? 'Detected' : 'No data'}</span>
      </div>
    `;
  }).join('');

  container.innerHTML = rows;
}

// ── Helper ──
function cls(label,conf){if(isHealthyClass(label))return 'h';return conf<0.6?'w':'d';}
function clsColor(c){return c==='h'?'var(--accent)':c==='w'?'var(--yellow)':'var(--red)';}

// ── GPS ──
function getGPS(cb){
  if(navigator.geolocation){
    // Request real device location with higher accuracy
    navigator.geolocation.getCurrentPosition(
      p => {
        const accuracy = Math.round(p.coords.accuracy || 10);
        console.log(`✅ Real GPS location: ${p.coords.latitude.toFixed(6)}, ${p.coords.longitude.toFixed(6)}, accuracy: ${accuracy}m`);
        cb(p.coords.latitude, p.coords.longitude, accuracy);
      },
      err => {
        console.warn('⚠️  Geolocation denied or unavailable:', err.message);
        // Silently fall back to Panabo coordinates - no toast warning to avoid clutter
        cb(baseLat+(Math.random()-.5)*.002, baseLng+(Math.random()-.5)*.002, 1000);
      },
      { enableHighAccuracy: true, timeout: 10000, maximumAge: 0 } // ✅ High accuracy, fresh location
    );
  } else {
    console.error('❌ Geolocation not supported');
    showToast('❌ Your browser does not support geolocation');
    cb(baseLat+(Math.random()-.5)*.002, baseLng+(Math.random()-.5)*.002, 1000);
  }
}

// ── Reverse Geocoding with Nominatim API ──
const geoCache = {}; // Cache to avoid duplicate API calls
async function reverseGeocode(lat, lng) {
  const key = `${lat.toFixed(5)},${lng.toFixed(5)}`;
  if(geoCache[key]) return geoCache[key];
  
  // ✅ OVERRIDE: Force correct location name for TADECO farm area
  const farmLongMin = 125.64055;
  const farmLongMax = 125.64215;
  const farmLatMin = 7.35140;
  const farmLatMax = 7.35295;
  
  if (lng >= farmLongMin && lng <= farmLongMax && lat >= farmLatMin && lat <= farmLatMax) {
    const customLocation = "TADECO Coconut Belt, Panabo";
    geoCache[key] = customLocation;
    return customLocation;
  }
  
  try {
    const response = await fetch(
      `https://nominatim.openstreetmap.org/reverse?format=json&lat=${lat}&lon=${lng}&zoom=18&addressdetails=1`,
      { headers: { 'Accept-Language': 'en' } }
    );
    const data = await response.json();
    
    // Extract human-readable address - prioritize neighborhood/village/town
    const addr = data.address || {};
    const location = addr.village || addr.town || addr.city || addr.municipality || addr.county || 'Unknown Location';
    const province = addr.state || addr.province || '';
    const address = province ? `${location}, ${province}` : location;
    
    geoCache[key] = address;
    return address;
  } catch(err) {
    console.warn('Geocoding failed:', err);
    return `${lat.toFixed(4)}, ${lng.toFixed(4)}`; // Fallback to coordinates
  }
}

// ✅ Save a single pin to Firebase
async function savePinToFirebase(lat, lng, label, confidence, source) {
  if (!currentUser) return;
  
  try {
    const db = firebase.database();
    const userPinsRef = db.ref(`users/${currentUser.uid}/map_pins`);
    
    const pinData = {
      lat: lat,
      lng: lng,
      label: label,
      confidence: confidence,
      source: source,
      timestamp: new Date().toISOString()
    };
    
    await userPinsRef.push(pinData);
    console.log('✅ Pin saved to Firebase:', pinData);
  } catch (error) {
    console.warn('⚠️  Failed to save pin to Firebase:', error);
    // Don't break the UI if Firebase save fails
  }
}

// ✅ Load all saved pins from Firebase and restore them on the map
async function loadSavedMapPins(userId) {
  try {
    const db = firebase.database();
    const userPinsRef = db.ref(`users/${userId}/map_pins`);
    
    userPinsRef.once('value', async snapshot => {
      const pinsData = snapshot.val();
      
      if (!pinsData) {
        console.log('ℹ️  No saved pins found for this user');
        return;
      }
      
      console.log('📍 Loading saved pins from Firebase:', Object.keys(pinsData).length, 'pins');
      
      // Clear current log and markers
      log = [];
      mainMarkers.forEach(m => mainMap.removeLayer(m));
      miniMarkers.forEach(m => miniMap.removeLayer(m));
      mainMarkers = [];
      miniMarkers = [];
      
      // Restore each pin
      for (const pinId in pinsData) {
        const pin = pinsData[pinId];
        if (pin.lat && pin.lng) {
          addPin(pin.lat, pin.lng, pin.label, pin.confidence, pin.source);
        }
      }
      
      console.log('✅ Restored', Object.keys(pinsData).length, 'pins from Firebase');
    });
  } catch (error) {
    console.warn('⚠️  Failed to load pins from Firebase:', error);
  }
}

// ── Map pins ──
// MapLibre uses GeoJSON features for markers - create element function
function createMarkerElement(color){
  const el = document.createElement('div');
  el.style.width = '20px';
  el.style.height = '20px';
  el.style.borderRadius = '50%';
  el.style.background = color;
  el.style.border = '2.5px solid white';
  el.style.boxShadow = '0 2px 6px rgba(0,0,0,0.3)';
  el.style.cursor = 'pointer';
  return el;
}

function addPin(lat,lng,label,confidence,source){
  const c=cls(label,confidence), color=clsColor(c), pct=Math.round(confidence*100);
  const time=new Date().toLocaleTimeString();
  
  // Build popup with loading state for address
  let popupContent=`<b style="text-transform:capitalize">${label.replace(/_/g,' ')}</b><br>Confidence: <b>${pct}%</b><br>Source: ${source}<br><span style="font-size:11px;color:#666">Loading location...</span><br>Time: ${time}`;
  
  let markerElement = null;
  let popup = null;
  let miniPopup = null;
  
  if(mapsReady){
    // Main map marker
    markerElement = createMarkerElement(color);
    popup = new maplibregl.Popup({offset: 25}).setHTML(popupContent);
    const mainMarker = new maplibregl.Marker({element: markerElement})
      .setLngLat([lng, lat])
      .setPopup(popup)
      .addTo(mainMap);
    
    // Mini map marker
    const miniElement = createMarkerElement(color);
    const miniMarker = new maplibregl.Marker({element: miniElement})
      .setLngLat([lng, lat])
      .addTo(miniMap);
    
    mainMarkers.push({marker: mainMarker, popup: popup}); 
    miniMarkers.push(miniMarker);
    miniMap.flyTo({center: [lng, lat], zoom: 16});
    
    // 📊 Add disease point to heatmap layer
    const diseaseSource = mainMap.getSource('disease-source');
    if (diseaseSource) {
      const diseaseData = diseaseSource._data;
      const severity = (c === 'd') ? confidence : (c === 'w') ? confidence * 0.6 : 0.1;
      diseaseData.features.push({
        type: 'Feature',
        geometry: { type: 'Point', coordinates: [lng, lat] },
        properties: { mag: severity * 100, label, confidence: pct }
      });
      diseaseSource.setData(diseaseData);
    }
  }
  
  const entry = {lat,lng,label,confidence,time,source,c,color,address:'Loading...',mainMarker: markerElement, popup: popup};
  log.unshift(entry);
  
  // ✅ Save pin to sessionStorage for farm location map
  try {
    const existingPins = JSON.parse(sessionStorage.getItem('farmPins') || '[]');
    existingPins.unshift({lat, lng, label, confidence, time, source});
    sessionStorage.setItem('farmPins', JSON.stringify(existingPins));
  } catch(e) {
    console.log('Could not save to sessionStorage:', e);
  }
  
  // ✅ Save pin to Firebase if user is logged in
  if (currentUser) {
    savePinToFirebase(lat, lng, label, confidence, source);
  }
  
  // Fetch and update address asynchronously
  reverseGeocode(lat, lng).then(address => {
    entry.address = address;
    
    // Update marker popup with real address
    if(popup) {
      const updatedPopup = `<b style="text-transform:capitalize">${label.replace(/_/g,' ')}</b><br>Confidence: <b>${pct}%</b><br>📍 <b>${address}</b><br>Source: ${source}<br>Time: ${time}`;
      popup.setHTML(updatedPopup);
    }
    
    renderLog(); // Re-render with address
  });
  
  renderLog(); updateStats();
  const pinCountEl = document.getElementById('pin-count');
  if (pinCountEl) pinCountEl.textContent = log.length + ' pins';
}

function renderLog(){
  const el=document.getElementById('events-list');
  if(!el) return; // Element doesn't exist in this context
  if(!log.length){el.innerHTML='<div class="no-events">No detections logged yet.<br>Start the drone camera or upload an image.</div>';return;}
  el.innerHTML=log.map((d,i)=>{
    return `
    <div class="eitem" onclick="flyTo(${i})">
      <div class="edot" style="background:${d.color}"></div>
      <div class="ebody">
        <div class="elabel">${d.label.replace(/_/g,' ')}</div>
        <div class="ecoords" style="font-weight:600;color:var(--text)">🌍 ${d.address}</div>
        <div class="etime">${d.time} &middot; ${d.source}</div>
        <div class="econf" style="color:${d.color}">${Math.round(d.confidence*100)}% confidence</div>
      </div>
    </div>`
  }).join('');
}

function setMapCenter(lat, lng){
  if(mapsReady){
    mainMap.flyTo({center: [lng, lat], zoom: 17});
  }
}

let recordLocationMarker = null;

function showRecordLocationOnMap(lat, lng) {
  if (!mapsReady || !mainMap) {
    showToast('Map is still loading. Please try again.');
    return;
  }

  if (recordLocationMarker) recordLocationMarker.remove();
  const markerElement = createMarkerElement('#26865a');
  const popup = new maplibregl.Popup({ offset: 25 })
    .setHTML(`<b>Detection record location</b><br>Latitude: ${Number(lat).toFixed(6)}<br>Longitude: ${Number(lng).toFixed(6)}`);
  recordLocationMarker = new maplibregl.Marker({ element: markerElement })
    .setLngLat([lng, lat])
    .setPopup(popup)
    .addTo(mainMap);
  mainMap.flyTo({ center: [lng, lat], zoom: 17 });
  popup.addTo(mainMap);
}

// ── Area-based GIS monitoring ──
const AREA_MONITORING_BOUNDS = { minLat: 7.35140, maxLat: 7.35295, minLng: 125.64055, maxLng: 125.64215 };
let areaMonitoringLoaded = false;

function getRecordCoordinates(record) {
  const gps = typeof record?.gps_data === 'string' ? (() => { try { return JSON.parse(record.gps_data); } catch (_) { return {}; } })() : (record?.gps_data || record?.gps || {});
  const lat = Number(record?.lat ?? gps.latitude ?? gps.lat);
  const lng = Number(record?.lng ?? gps.longitude ?? gps.lng);
  return Number.isFinite(lat) && Number.isFinite(lng) && lat >= -90 && lat <= 90 && lng >= -180 && lng <= 180 ? { lat, lng } : null;
}

function areaColor(prevalence) {
  if (prevalence >= 60) return '#dc2626';
  if (prevalence >= 30) return '#f59e0b';
  if (prevalence > 0) return '#84cc16';
  return '#94a3b8';
}

function buildAreaFeatures(areas) {
  return areas.map(area => ({
    type: 'Feature',
    properties: { name: area.name, prevalence: area.prevalence, diseased: area.diseased, samples: area.samples, color: area.color },
    geometry: { type: 'Polygon', coordinates: [[
      [area.minLng, area.minLat], [area.maxLng, area.minLat], [area.maxLng, area.maxLat], [area.minLng, area.maxLat], [area.minLng, area.minLat]
    ]] }
  }));
}

window.loadAreaMonitoring = async function loadAreaMonitoring() {
  const gridEl = document.getElementById('area-monitoring-grid');
  const summaryEl = document.getElementById('area-monitoring-summary');
  if (!gridEl || !currentToken) return;
  gridEl.innerHTML = '<div class="no-events" style="grid-column:1/-1;padding:1rem">Loading GPS-tagged samples…</div>';
  try {
    const endpoint = currentUserIsExpert ? '/expert/records?status=all' : '/detections/my-records';
    const response = await fetch(endpoint, { headers: { Authorization: `Bearer ${currentToken}` } });
    if (!response.ok) throw new Error('Unable to load disease records');
    const payload = await response.json();
    const records = Array.isArray(payload) ? payload : (payload.records || []);
    const latStep = (AREA_MONITORING_BOUNDS.maxLat - AREA_MONITORING_BOUNDS.minLat) / 2;
    const lngStep = (AREA_MONITORING_BOUNDS.maxLng - AREA_MONITORING_BOUNDS.minLng) / 2;
    const areas = [];
    for (let row = 0; row < 2; row++) for (let col = 0; col < 2; col++) {
      areas.push({ name: `Area ${row * 2 + col + 1}`, row, col, minLat: AREA_MONITORING_BOUNDS.minLat + row * latStep, maxLat: AREA_MONITORING_BOUNDS.minLat + (row + 1) * latStep, minLng: AREA_MONITORING_BOUNDS.minLng + col * lngStep, maxLng: AREA_MONITORING_BOUNDS.minLng + (col + 1) * lngStep, samples: 0, diseased: 0 });
    }
    records.forEach(record => {
      if (record.type && record.type !== 'upload' && record.source !== 'upload') return;
      const point = getRecordCoordinates(record);
      if (!point || point.lat < AREA_MONITORING_BOUNDS.minLat || point.lat > AREA_MONITORING_BOUNDS.maxLat || point.lng < AREA_MONITORING_BOUNDS.minLng || point.lng > AREA_MONITORING_BOUNDS.maxLng) return;
      const col = Math.min(1, Math.floor((point.lng - AREA_MONITORING_BOUNDS.minLng) / lngStep));
      const row = Math.min(1, Math.floor((point.lat - AREA_MONITORING_BOUNDS.minLat) / latStep));
      const area = areas[row * 2 + col];
      area.samples += 1;
      if ((record.detections || []).some(det => !isHealthyClass(det.class))) area.diseased += 1;
    });
    areas.forEach(area => { area.prevalence = area.samples ? Math.round(area.diseased / area.samples * 100) : 0; area.color = areaColor(area.prevalence); });
    const gpsSamples = areas.reduce((sum, area) => sum + area.samples, 0);
    const diseasedSamples = areas.reduce((sum, area) => sum + area.diseased, 0);
    const highest = [...areas].sort((a, b) => b.prevalence - a.prevalence)[0];
    if (summaryEl) summaryEl.innerHTML = `<div class="stat-box"><h4>GPS Samples</h4><div class="stat-value">${gpsSamples}</div><div class="stat-subtext">Uploaded images mapped</div></div><div class="stat-box disease"><h4>Disease Prevalence</h4><div class="stat-value">${gpsSamples ? Math.round(diseasedSamples / gpsSamples * 100) : 0}%</div><div class="stat-subtext">Across mapped areas</div></div><div class="stat-box"><h4>Priority Area</h4><div class="stat-value" style="font-size:1.15rem">${gpsSamples && highest.prevalence ? highest.name : '—'}</div><div class="stat-subtext">Highest disease prevalence</div></div>`;
    gridEl.innerHTML = areas.map(area => `<div class="area-card" style="border-left-color:${area.color}" onclick="setMapCenter(${(area.minLat + area.maxLat) / 2}, ${(area.minLng + area.maxLng) / 2})"><h5>${area.name}</h5><div class="area-prevalence">${area.prevalence}%</div><div class="area-meta">${area.diseased} diseased / ${area.samples} GPS samples</div><div class="area-bar"><span style="width:${area.prevalence}%;background:${area.color}"></span></div></div>`).join('');
    if (mapsReady && mainMap.getSource('area-monitoring-src')) mainMap.getSource('area-monitoring-src').setData({ type: 'FeatureCollection', features: buildAreaFeatures(areas) });
    areaMonitoringLoaded = true;
  } catch (error) {
    console.error('Area monitoring error:', error);
    gridEl.innerHTML = `<div class="no-events" style="grid-column:1/-1;padding:1rem">${error.message}</div>`;
  }
};

function flyTo(i){
  const d=log[i];
  if(mapsReady){
    mainMap.flyTo({center: [d.lng, d.lat], zoom: 17});
    const markerData = mainMarkers[mainMarkers.length-1-i];
    if(markerData && markerData.popup) markerData.popup.addTo(mainMap);
  }
  switchTab('map',document.querySelectorAll('.ntab')[2]);
}
function updateStats(){
  const total=log.length, dis=log.filter(d=>isDiseaseClass(d.label)).length, ok=total-dis;
  ['ms-total','ms-dis','ms-ok'].forEach((id,i)=>{
    const el=document.getElementById(id);
    if(el) el.textContent=[total,dis,ok][i];
  });
}
function clearPins(){
  mainMarkers.forEach(m=>{
    if(m && m.marker) m.marker.remove();
  });
  miniMarkers.forEach(m=>{
    if(m && m.remove) m.remove();
  });
  mainMarkers=[];miniMarkers=[];log=[];
  // ✅ Also clear farm location map pins
  sessionStorage.removeItem('farmPins');
  sessionStorage.removeItem('farmPinsCount');
  
  // Clear heatmap data
  if (mainMap && mainMap.getSource) {
    const diseaseSource = mainMap.getSource('disease-source');
    if (diseaseSource) {
      diseaseSource.setData({ type: 'FeatureCollection', features: [] });
    }
  }
  
  renderLog();updateStats();
  const pinCountEl = document.getElementById('pin-count');
  if (pinCountEl) pinCountEl.textContent='0 pins';
  showToast('Map cleared.');
}

// 3D View Toggle
let is3DView = false;
function toggle3DView() {
  if (!mainMap) return;
  
  is3DView = !is3DView;
  
  if (is3DView) {
    // Enter 3D mode - increase pitch and set bearing
    mainMap.easeTo({
      pitch: 60,
      bearing: 45,
      duration: 1000
    });
    showToast('📍 3D View Activated! Drag to rotate 🌍');
  } else {
    // Return to 2D mode
    mainMap.easeTo({
      pitch: 0,
      bearing: 0,
      duration: 1000
    });
    showToast('Returned to 2D View');
  }
}

// ── Upload ──
let stagedUploadFile = null;

function stageUploadImage(file) {
  if (!file) return;
  if (!file.type.startsWith('image/')) {
    showToast('Please choose an image file.');
    return;
  }

  stagedUploadFile = file;
  const confirmation = document.getElementById('upload-confirmation');
  const fileName = document.getElementById('upload-confirmation-name');
  if (fileName) fileName.textContent = `Ready to upload: ${file.name}`;
  if (confirmation) confirmation.style.display = 'block';
}

window.confirmStagedUpload = function confirmStagedUpload() {
  if (!stagedUploadFile) return;
  const file = stagedUploadFile;
  stagedUploadFile = null;
  const confirmation = document.getElementById('upload-confirmation');
  if (confirmation) confirmation.style.display = 'none';
  detectImage(file);
};

window.cancelStagedUpload = function cancelStagedUpload() {
  stagedUploadFile = null;
  const confirmation = document.getElementById('upload-confirmation');
  const fileInput = document.getElementById('fileInput');
  if (confirmation) confirmation.style.display = 'none';
  if (fileInput) fileInput.value = '';
};

function handleDrop(e){
  e.preventDefault();
  e.stopPropagation();
  const uploadDrop = document.getElementById('upload-drop');
  if (uploadDrop) uploadDrop.classList.remove('over');
  const f=e.dataTransfer.files[0];if(f&&f.type.startsWith('image/'))stageUploadImage(f);else showToast('Please drop an image file.');
}
async function detectImage(file){
  if(!file)return;
  if (!currentUser) {
    showToast('Session expired, please refresh');
    return;
  }
  const uploadLoading = document.getElementById('upload-loading');
  const resultArea = document.getElementById('result-area');
  const recsArea = document.getElementById('recommendations-area');
  const finishUpload = () => {
    const uploadLoading = document.getElementById('upload-loading');
    const uploadDrop = document.getElementById('upload-drop');
    const fileInput = document.getElementById('fileInput');
    if (uploadLoading) uploadLoading.classList.remove('on');
    if (uploadDrop) uploadDrop.classList.remove('over');
    if (fileInput) fileInput.value='';
  };
  if (uploadLoading) uploadLoading.classList.add('on');
  if (resultArea) resultArea.style.display='none';
  if (recsArea) {
    recsArea.style.display = 'none';
    recsArea.innerHTML = '';
  }
  
  // ✅ CRITICAL FIX: Use the explicit farm coordinates instead of laptop geolocation
  const farmCenter = [125.64135, 7.35218];
  const gps_lng = farmCenter[0];
  const gps_lat = farmCenter[1];
  const gps_accuracy = 5; // Farm boundary accuracy in meters
  
  const fd=new FormData();
  fd.append('file',file);
  fd.append('lat', gps_lat);  // ✅ Send farm center coordinates to backend
  fd.append('lng', gps_lng);
  fd.append('accuracy', gps_accuracy);
  
  console.log(`🌾 Farm Coordinates: ${gps_lat.toFixed(6)}, ${gps_lng.toFixed(6)} (accuracy: ${gps_accuracy}m)`);
  
  const fetchOpts={
    method:'POST',
    body:fd
  };
  
  // ✅ Add auth header only if token exists (offline mode if missing)
  if(currentToken){
    fetchOpts.headers={'Authorization': `Bearer ${currentToken}`};
  }
  
  fetch('/detect/image',fetchOpts).then(res=>{
    if(!res.ok)throw new Error('Server error '+res.status);
    return res.json();
  }).then(data=>{
    try {
      renderUpload(data);
      // ✅ Record one pin per uploaded image instead of one per detected object
      const detections = Array.isArray(data.detections) ? data.detections : [];
      if (detections.length) {
        const primary = [...detections].sort((a, b) => (b.confidence || 0) - (a.confidence || 0))[0];
        addPin(gps_lat+(Math.random()-.5)*.0003,gps_lng+(Math.random()-.5)*.0003, primary.class, primary.confidence, 'Upload');
      }
    } catch (renderErr) {
      console.error('Error rendering upload:', renderErr);
    } finally {
      finishUpload();
    }
  }).catch(err=>{
    console.error('Upload error:', err);
    const uploadDrop = document.getElementById('upload-drop');
    const feedToolbar = document.querySelector('.feed-toolbar');
    finishUpload();
    if (feedToolbar) feedToolbar.style.display='';
    if (uploadDrop) uploadDrop.style.display='';
  });
}
function renderUpload(data){
  const resultImg = document.getElementById('result-img');
  const feedToolbar = document.querySelector('.feed-toolbar');
  const uploadDrop = document.getElementById('upload-drop');
  const resultArea = document.getElementById('result-area');
  const sTotal = document.getElementById('s-total');
  const sConf = document.getElementById('s-conf');
  const sStatus = document.getElementById('s-status');
  
  if (!resultImg || !feedToolbar || !uploadDrop || !resultArea || !sTotal || !sConf || !sStatus) {
    console.error('❌ Required DOM elements missing for renderUpload');
    return;
  }
  
  if (resultImg && data.annotated_image_base64) {
    resultImg.src='data:image/jpeg;base64,'+data.annotated_image_base64;
  }
  if (feedToolbar) feedToolbar.style.display='none';
  if (uploadDrop) uploadDrop.style.display='none';
  if (resultArea) resultArea.style.display='block';
  
  const allDetections = Array.isArray(data.detections) ? data.detections : [];
  const diseaseDetections = allDetections.filter(d => isDiseaseClass(d.class));
  const n = diseaseDetections.length;
  const primary = n > 0
    ? [...diseaseDetections].sort((a, b) => Number(b.confidence || 0) - Number(a.confidence || 0))[0]
    : null;
  const confText = primary ? `${(Number(primary.confidence || 0) * 100).toFixed(2)}%` : '—';
  const bad = n > 0;
  if (sTotal) sTotal.textContent = n;
  if (sConf) sConf.textContent = confText;
  if (sStatus) {
    sStatus.textContent = n === 0 ? 'Clear' : 'Diseased';
    sStatus.style.color = bad ? 'var(--red)' : 'var(--accent)';
  }
  
  if (n > 0) {
    // Recommendations are now shown only after verification from the record cards.
    // Keep the upload result focused on detection output.
  }
}

// ✅ Clear upload results and restore upload UI
function clearUploadResults(){
  const resultArea = document.getElementById('result-area');
  const feedToolbar = document.querySelector('.feed-toolbar');
  const uploadDrop = document.getElementById('upload-drop');
  const fileInput = document.getElementById('fileInput');
  const sTotal = document.getElementById('s-total');
  const sConf = document.getElementById('s-conf');
  const sStatus = document.getElementById('s-status');
  const recsArea = document.getElementById('recommendations-area');
  
  if (resultArea) resultArea.style.display='none';
  if (feedToolbar) feedToolbar.style.display='';
  if (uploadDrop) uploadDrop.style.display='';
  if (fileInput) fileInput.value='';
  if (sTotal) sTotal.textContent='0';
  if (sConf) sConf.textContent='—';
  if (sStatus) {
    sStatus.textContent='—';
    sStatus.style.color='';
  }
  if (recsArea) recsArea.style.display='none';
}

async function fetchRecommendation(disease, confidence){
  try{
    // ✅ Validate disease parameter
    if (!disease || typeof disease !== 'string' || disease.trim() === '') {
      throw new Error('Invalid disease name provided');
    }
    
    // ✅ Validate confidence parameter
    if (confidence === undefined || confidence === null) {
      throw new Error('Confidence value is missing');
    }
    
    // Convert to number if string
    let confValue = typeof confidence === 'string' ? parseFloat(confidence) : confidence;
    
    // Check for NaN or Infinity
    if (isNaN(confValue) || !isFinite(confValue)) {
      throw new Error(`Invalid confidence value: ${confidence}`);
    }
    
    // Ensure confidence is in valid range (0-1 or 0-100)
    if (confValue < 0) {
      throw new Error(`Confidence cannot be negative: ${confValue}`);
    }
    if (confValue > 100) {
      console.warn(`⚠️ Confidence value ${confValue} exceeds 100, clamping to 1.0`);
      confValue = 1.0;
    }
    
    const rec_url='/recommendations/fertilizer';
    
    // Log confidence as percentage for readability
    const confPercent = confValue > 1 ? confValue : Math.round(confValue * 100);
    console.log(`📋 Fetching recommendations for: ${disease} (${confPercent}% confidence)`);
    console.log(`   Raw confidence value: ${confValue}`);
    
    const payload = {
      disease: disease.trim(),
      confidence: confValue
    };
    
    console.log('📤 Sending request:', JSON.stringify(payload));
    
    const response = await fetch(rec_url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${currentToken}`
      },
      body: JSON.stringify(payload)
    });
    
    if (!response.ok) {
      const errorText = await response.text();
      console.error(`Backend error: ${response.status}`);
      console.error(`Response body: ${errorText}`);
      
      // Try to parse error details if available
      let errorDetail = `Failed to fetch recommendation: ${response.status}`;
      try {
        const errorJson = JSON.parse(errorText);
        if (errorJson.detail) {
          errorDetail = errorJson.detail;
        }
      } catch (e) {
        // errorText might not be JSON
      }
      
      throw new Error(errorDetail);
    }
    
    const data = await response.json();
    console.log('✅ Recommendations received:', data);
    displayRecommendation(data);
  } catch (err) {
    console.error('❌ Recommendation error:', err);
    showToast(`⚠️ Could not load recommendations: ${err.message}`);
  }
}

function displayRecommendation(rec){
  const recArea=document.getElementById('recommendations-area');
  
  // ✅ Safety check: return early if element doesn't exist
  if (!recArea) {
    console.warn('⚠️ recommendations-area element not found');
    return;
  }
  
  recArea.innerHTML = renderRecommendationCardHtml(rec);
  recArea.style.display='block';
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, ch => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;'
  }[ch]));
}

function getRecordKey(record, idx) {
  return String(record?.id || record?.record_id || `${record?.user_id || 'record'}-${idx}`)
    .replace(/[^a-zA-Z0-9_-]/g, '_');
}

function getPrimaryDetection(record) {
  const detections = Array.isArray(record?.detections) ? record.detections : [];
  if (record?.primaryDisease) {
    return {
      class: String(record.primaryDisease),
      confidence: Number(record.primaryConfidence || record.primary_confidence || detections[0]?.confidence || 0)
    };
  }

  if (!detections.length) {
    return { class: 'Unknown', confidence: 0 };
  }

  const primary = detections.reduce((best, candidate) => (
    Number(candidate?.confidence || 0) > Number(best?.confidence || 0) ? candidate : best
  ), detections[0]);

  return {
    class: String(primary?.class || 'Unknown'),
    confidence: Number(primary?.confidence || 0)
  };
}

function renderRecommendationCardHtml(rec) {
  const recommendation = rec?.recommendations || {};
  const preventionText = Array.isArray(recommendation.prevention)
    ? recommendation.prevention.join(' • ')
    : recommendation.prevention;
  const sourceLabel = rec?.source === 'expert_override' ? 'Expert edited recommendation' : 'Default recommendation';

  return `
    <div class="rec-card" style="margin-top:10px;">
      <div class="rec-title">Recommendation - ${escapeHtml(rec?.disease || 'Unknown')} (${escapeHtml(rec?.confidence_percent ?? '0')}%)</div>
      <div style="margin-top:4px;font-size:11px;color:var(--text3);">${escapeHtml(sourceLabel)}</div>
      <div class="rec-item">
        <div class="rec-label">Fertilizer</div>
        <div class="rec-text">${escapeHtml(recommendation.fertilizer || '')}</div>
      </div>
      <div class="rec-item">
        <div class="rec-label">Treatment</div>
        <div class="rec-text">${escapeHtml(recommendation.treatment || '')}</div>
      </div>
      <div class="rec-item">
        <div class="rec-label">Prevention</div>
        <div class="rec-text">${escapeHtml(preventionText || '')}</div>
      </div>
      <div style="margin-top:12px; padding-top:12px; border-top:1px solid rgba(11,42,31,.1); font-size:11px; color:var(--text3);">
        ${escapeHtml(rec?.note || '')}
      </div>
    </div>
  `;
}

window.toggleRecordRecommendation = async function toggleRecordRecommendation(recordKey, disease, confidence, buttonEl) {
  if (!recordKey || !disease) {
    showToast('Recommendation data is missing');
    return;
  }

  const panel = document.getElementById(`record-recommendation-${recordKey}`);
  if (!panel) return;

  if (panel.style.display !== 'none' && panel.innerHTML.trim()) {
    panel.style.display = 'none';
    if (buttonEl) buttonEl.textContent = 'Recommendation';
    return;
  }

  if (panel.dataset.loaded === '1' && panel.innerHTML.trim()) {
    panel.style.display = 'block';
    if (buttonEl) buttonEl.textContent = 'Hide Recommendation';
    return;
  }

  panel.innerHTML = '<div class="no-records">No saved expert recommendation for this verified record yet.</div>';
  panel.dataset.loaded = '0';
  panel.style.display = 'block';
  if (buttonEl) buttonEl.textContent = 'Hide Recommendation';
};

// ── Drone ──
let frameQueue=[], sendingFrame=false, droneFrameSkip=0;
let droneDetectionCount = 0; // Track disease detections during flight
let droneSessionPins = []; // Track pins added in current session
let dronePath = []; // Track drone flight path
let dronePathMarker = null; // Polyline for drone path
const FRAME_SKIP = 2; // Send 1 out of every 3 frames (reduces to ~1.67 FPS for processing)
const REDUCED_WIDTH = 416;
const REDUCED_HEIGHT = 312;
const JPEG_QUALITY = 0.5; // Reduce compression quality for faster transmission
const MIN_CONFIDENCE = 0.5; // Only pin detections with 50%+ confidence

async function startDrone(){
  if (!currentUser) {
    showToast('Session expired, please refresh');
    return;
  }
  
  // ✅ Refresh token before connecting WebSocket
  try {
    currentToken = await currentUser.getIdToken();
    console.log('✅ Token refreshed before WebSocket connection');
  } catch (e) {
    showToast('Failed to refresh auth token: ' + e.message);
    return;
  }
  
  if (!currentToken) {
    showToast('No valid authentication token');
    return;
  }
  
  try{
    droneDetectionCount = 0; // Reset detection counter
    droneSessionPins = [];
    
    // Try to get camera - support both mobile (environment) and laptop (user)
    let constraints = {video:{width:640,height:480}};
    try {
      // First try: For mobile devices (back camera)
      droneStream = await navigator.mediaDevices.getUserMedia({video:{width:640,height:480,facingMode:'environment'}});
    } catch (e) {
      // Fallback: For laptops or if back camera not available (front camera)
      droneStream = await navigator.mediaDevices.getUserMedia(constraints);
    }
    
    const v=document.getElementById('droneVideo'),c=document.getElementById('droneCanvas');
    v.srcObject=droneStream;c.width=640;c.height=480;
    droneRunning=true;frameCount=0;droneFrameSkip=0;frameQueue=[];sendingFrame=false;
    document.getElementById('sdot').classList.add('live');
    document.getElementById('sdot-lbl').textContent='Live • Detecting...';
    document.getElementById('canvas-ph').classList.add('hidden');
    ws=new WebSocket('ws://'+location.host+'/detect/stream?token='+currentToken);
    ws.onmessage=e=>{
      const data=JSON.parse(e.data);
      if (data.error) {
        showToast('WebSocket error: ' + data.error);
        stopDrone();
        return;
      }
      // ✅ Store drone GPS data from WebSocket
      if(data.gps && data.gps.lat && data.gps.lng) {
        lastDroneGPS = data;
      }
      const img=new Image();img.onload=()=>c.getContext('2d').drawImage(img,0,0,640,480);img.src=data.annotated_image;
      const box=document.getElementById('live-dets');
      
      // 📍 Real-time disease detection and pinning during drone flight
      if(!data.detections.length){
        box.innerHTML='<span class="live-empty">📡 Scanning... No diseases this frame</span>';
      }
      else{
        // Show all detections in real-time display
        box.innerHTML=data.detections.map(d=>{const cl=cls(d.class,d.confidence);return `<span class="ltag ${cl}"><span class="ltdot"></span>${d.class.replace(/_/g,' ')} ${Math.round(d.confidence*100)}%</span>`;}).join('');
        
        // 📌 Pin disease detections with high confidence (exclude healthy + low confidence)
        if(data.gps && data.gps.lat && data.gps.lng){
          const lat=data.gps.lat, lng=data.gps.lng;
          data.detections.forEach(d=>{
            // Only pin disease/warning with sufficient confidence
            if(isDiseaseClass(d.class) && d.confidence >= MIN_CONFIDENCE){
              addPin(lat, lng, d.class, d.confidence, 'Drone');
              droneDetectionCount++;
              droneSessionPins.push({lat, lng, class: d.class, confidence: d.confidence});
              
              // Update detection counter badge
              const pinCountEl = document.getElementById('pin-count');
              if (pinCountEl) pinCountEl.textContent = droneDetectionCount + ' diseases pinned';
              
              // Show toast notification for high-confidence detections
              if(d.confidence >= 0.75){
                showToast('🚨 ' + d.class.toUpperCase() + ' detected at ' + lat.toFixed(4) + ',' + lng.toFixed(4));
              }
            }
          });
        } else {
          // Fallback to browser geolocation if drone GPS unavailable
          getGPS((lat,lng)=>{
            data.detections.forEach(d=>{
              if(isDiseaseClass(d.class) && d.confidence >= MIN_CONFIDENCE){
                addPin(lat+(Math.random()-.5)*.0003, lng+(Math.random()-.5)*.0003, d.class, d.confidence, 'Drone');
                droneDetectionCount++;
              }
            });
          });
        }
      }
      sendingFrame=false; // ✅ Mark frame sent, process queue
      processFrameQueue();
    };
    
    ws.onerror = (error) => {
      console.error('❌ WebSocket error:', error);
      showToast('Connection error - check backend server');
      document.getElementById('sdot-lbl').textContent='Error';
    };
    
    ws.onclose = () => {
      if(droneRunning) {
        console.warn('WebSocket closed unexpectedly');
        showToast('Connection lost');
        document.getElementById('sdot-lbl').textContent='Offline';
      }
    };
    
    // Wait for video to be ready before starting capture
    v.onloadedmetadata = () => {
      console.log('[OK] Video loaded - dimensions:', v.videoWidth, 'x', v.videoHeight);
      v.play().then(() => {
        console.log('[OK] Video playing');
        captureLoop(v);
      }).catch(e => {
        console.error('[ERROR] Video play failed:', e);
        showToast('Failed to play camera: ' + e.message);
        stopDrone();
      });
    };
    
    v.onerror = (e) => {
      console.error('[ERROR] Video error:', e);
      showToast('Camera error');
      stopDrone();
    };
    
  }catch(err){
    console.error('[ERROR] Camera access error:', err);
    showToast('Camera error: '+err.message);
    if (err.name === 'NotAllowedError') {
      showToast('Permission denied - check browser camera permissions');
    } else if (err.name === 'NotFoundError') {
      showToast('No camera found');
    }
  }
}

function processFrameQueue(){
  if(frameQueue.length>0 && !sendingFrame && ws && ws.readyState===WebSocket.OPEN){
    sendingFrame=true;
    ws.send(frameQueue.shift());
  }
}

function captureLoop(v){
  if(!droneRunning)return;
  
  // ✅ Draw video frame to main canvas for preview
  if(v.readyState===4){  // Video is ready
    frameCount++;
    
    // Draw current frame to canvas for display
    const c=document.getElementById('droneCanvas');
    const ctx=c.getContext('2d');
    ctx.drawImage(v,0,0,640,480);
    
    document.getElementById('g-frames').textContent=frameCount;
    
    // Update GPS display from latest WebSocket data or browser location
    if(lastDroneGPS && lastDroneGPS.gps){
      const lat = lastDroneGPS.gps.lat;
      const lng = lastDroneGPS.gps.lng;
      
      document.getElementById('g-lat').textContent=lat.toFixed(6);
      document.getElementById('g-lng').textContent=lng.toFixed(6);
      document.getElementById('g-alt').textContent=lastDroneGPS.gps.altitude.toFixed(1);
      
      // 📍 Track drone flight path on mini-map
      dronePath.push([lng, lat]);
      
      // Update drone location on mini-map every 10 frames
      if(frameCount % 10 === 0 && mapsReady){
        // Update drone position marker on mini-map
        const droneMarkerEl = document.createElement('div');
        droneMarkerEl.style.width = '16px';
        droneMarkerEl.style.height = '16px';
        droneMarkerEl.style.borderRadius = '50%';
        droneMarkerEl.style.background = '#4a7cf7';
        droneMarkerEl.style.border = '3px solid white';
        droneMarkerEl.style.boxShadow = '0 0 10px rgba(74,124,247,0.6)';
        
        if(!dronePathMarker) {
          dronePathMarker = new maplibregl.Marker({element: droneMarkerEl, scale: 1.2})
            .setLngLat([lng, lat])
            .addTo(miniMap);
        } else {
          dronePathMarker.setLngLat([lng, lat]);
        }
        
        // Auto-center mini-map on drone
        miniMap.flyTo({center: [lng, lat], zoom: 16, duration: 300});
      }
    } else {
      getGPS((lat,lng)=>{
        document.getElementById('g-lat').textContent=lat.toFixed(6);
        document.getElementById('g-lng').textContent=lng.toFixed(6);
        document.getElementById('g-alt').textContent=(Math.random()*5+10).toFixed(1);
      });
    }
    
    // ✅ OPTIMIZATION: Skip frames to reduce processing load
    if(droneFrameSkip++ % (FRAME_SKIP+1) === 0){
      const canvas=document.createElement('canvas');
      canvas.width=REDUCED_WIDTH;
      canvas.height=REDUCED_HEIGHT;
      canvas.getContext('2d').drawImage(v,0,0,REDUCED_WIDTH,REDUCED_HEIGHT);
      frameQueue.push(canvas.toDataURL('image/jpeg',JPEG_QUALITY));
      processFrameQueue(); // Try to send if not already sending
    }
  } else {
    if(frameCount % 60 === 0) console.log('[WAIT] Video not ready, state:', v.readyState);
  }
  
  requestAnimationFrame(captureLoop.bind(null,v)); // ✅ Use RAF for smooth capture
}
function stopDrone(){
  droneRunning=false;
  if(ws)ws.close();
  if(droneStream)droneStream.getTracks().forEach(t=>t.stop());
  frameQueue=[];sendingFrame=false;droneFrameSkip=0;
  
  // Clean up drone path marker
  if(dronePathMarker){
    dronePathMarker.remove();
    dronePathMarker = null;
  }
  dronePath = [];
  
  document.getElementById('sdot')?.classList.remove('live');
  const statusLabel = document.getElementById('sdot-lbl');
  if (statusLabel) statusLabel.textContent='Offline';
  
  // Show session summary
 
  const liveDets = document.getElementById('live-dets');
  if (liveDets) liveDets.innerHTML='<span class="live-empty">Start camera to detect diseases</span>';
  document.getElementById('canvas-ph')?.classList.remove('hidden');
  const droneCanvas = document.getElementById('droneCanvas');
  if (droneCanvas) droneCanvas.getContext('2d').clearRect(0,0,640,480);
  if (!document.getElementById('g-lat')) return;
  ['g-lat','g-lng','g-alt'].forEach(id=>document.getElementById(id).textContent='—');
  
  // Log drone session data
  if(droneSessionPins.length > 0){
    console.log('📊 Drone Session Summary:', {
      totalDetections: droneDetectionCount,
      pins: droneSessionPins,
      flightPath: dronePath,
      timestamp: new Date().toISOString()
    });
  }
}

// ── Load User Detection Records ──
let currentUserRecordsFilter = 'all';

window.setUserRecordsFilter = function setUserRecordsFilter(filterName) {
  currentUserRecordsFilter = ['all', 'pending', 'verified'].includes(filterName) ? filterName : 'all';

  ['all', 'pending', 'verified'].forEach((filter) => {
    const button = document.getElementById(`records-filter-${filter}`);
    if (!button) return;
    const isActive = filter === currentUserRecordsFilter;
    button.classList.toggle('active', isActive);
    button.setAttribute('aria-pressed', String(isActive));
  });

  loadUserRecords();
};

function formatVerificationStatus(status) {
  const normalized = String(status || 'pending_verification').toLowerCase();
  if (normalized === 'verified') return { label: 'Verified', cls: 'verified' };
  if (normalized === 'pending_verification' || normalized === 'pending') return { label: 'Pending', cls: 'pending' };
  return { label: normalized.replace(/_/g, ' '), cls: 'pending' };
}

function buildRecordStatusBadge(record) {
  const status = formatVerificationStatus(record.verification_status);
  return `<span class="record-status ${status.cls}">${status.label}</span>`;
}

function renderRecordCard(record, idx, options = {}) {
  const timestamp = new Date(record.timestamp || Date.now()).toLocaleString();
  const typeLabel = record.type === 'upload' ? 'Upload' : 'Drone';
  const detections = record.detections || [];
  const isExpertView = Boolean(options.expert);
  const ownerLabel = record.email || record.user_id || 'Unknown farmer';
  const statusBadge = buildRecordStatusBadge(record);
  const previewImage = record.image_url || record.annotated_image_url || '';
  const primary = getPrimaryDetection(record);
  const recommendationSnapshot = record.recommendation_snapshot || record.recommendationSnapshot || null;

  let lat = null;
  let lng = null;
  let gpsSource = 'unknown';

  if (record.lat && record.lng) {
    lat = record.lat;
    lng = record.lng;
    gpsSource = record.gps_source || 'database';
  } else if (record.gps_data && typeof record.gps_data === 'object') {
    lat = record.gps_data.latitude || record.gps_data.lat;
    lng = record.gps_data.longitude || record.gps_data.lng;
    gpsSource = record.gps_data.source || gpsSource;
  } else if (record.gps && typeof record.gps === 'object') {
    lat = record.gps.lat || record.gps.latitude;
    lng = record.gps.lng || record.gps.longitude;
    gpsSource = record.gps.source || gpsSource;
  }

  if (lat !== null && lng !== null) {
    lat = parseFloat(lat);
    lng = parseFloat(lng);
  }

  if (!lat || !lng || isNaN(lat) || isNaN(lng)) {
    lat = 7.3427;
    lng = 125.6290;
    gpsSource = 'farm_default';
  }

  const mapButtonHtml = `<button class="btn btn-o" onclick="switchTab('map', document.querySelector('.nav-link[onclick*=\\'map\\']'));showRecordLocationOnMap(${lat}, ${lng})" style="padding:6px 12px;font-size:11px;margin-top:8px;background:#26865a;color:white;border:none;border-radius:4px;cursor:pointer;"><span>🗺️ Show on Map</span></button>`;
  const imageLabel = record.filename || record.image_path || 'Unknown image';
  const imagePreviewHtml = previewImage
    ? `<div style="margin-top:10px;border:1px solid rgba(11,42,31,0.12);border-radius:10px;overflow:hidden;background:#fff;">
         <div style="padding:8px 10px;font-size:11px;font-weight:600;color:#164d37;background:rgba(38,134,90,0.08);border-bottom:1px solid rgba(11,42,31,0.08);">Image Preview</div>
         <button type="button" onclick="openRecordImageModal('${encodeURIComponent(previewImage)}', '${encodeURIComponent(imageLabel)}')" style="display:block;width:100%;padding:0;border:0;background:transparent;cursor:pointer;">
           <img src="${previewImage}" alt="${imageLabel}" style="width:100%;max-height:280px;object-fit:cover;display:block;background:#eef6f1;" />
         </button>
       </div>`
    : `<div style="margin-top:10px;padding:10px;background:rgba(38,134,90,0.08);border-radius:8px;border:1px dashed rgba(38,134,90,0.25);color:#3f5b4d;font-size:12px;">
         No saved image file was found for this record yet.
       </div>`;
  const locationDisplay = `<div style="margin-top:10px;padding:10px;background:rgba(38, 134, 90, 0.12);border-radius:6px;border-left:4px solid #26865a;">
    <div style="font-size:11px;font-weight:600;color:#164d37;margin-bottom:6px;text-transform:uppercase;letter-spacing:0.5px;">📍 Location Detected</div>
    <div style="font-size:12px;color:#0e1c15;font-family:monospace;font-weight:500;margin-bottom:8px;background:white;padding:6px;border-radius:3px;"><strong>Latitude:</strong> ${lat.toFixed(6)}<br><strong>Longitude:</strong> ${lng.toFixed(6)}</div>
    <div style="font-size:10px;color:#7a9a8a;margin-bottom:6px;"><strong>Source:</strong> ${gpsSource.replace(/_/g, ' ')}</div>
    ${isExpertView ? `<div style="font-size:10px;color:#7a9a8a;margin-bottom:6px;"><strong>Image:</strong> ${imageLabel}</div>` : ''}
    ${isExpertView ? '' : mapButtonHtml}
  </div>`;

  const expertActions = isExpertView && record.verification_status !== 'verified'
    ? `<div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:10px;">
         <button class="btn btn-p" onclick="verifyExpertRecord('${record.user_id || ''}','${record.id || ''}')" style="padding:6px 12px;font-size:11px;">Mark Verified</button>
         <button class="btn btn-o" onclick="editExpertRecommendation('${(primary.class || record.primaryDisease || (detections[0]?.class || '')).replace(/'/g, '&#39;')}', ${primary.confidence || 0}, '${(record.user_id || '').replace(/'/g, '&#39;')}', '${(record.id || '').replace(/'/g, '&#39;')}')" style="padding:6px 12px;font-size:11px;">Edit Recommendation</button>
       </div>`
    : '';

  return `
    <div class="record-item">
      <span class="record-type ${record.type || 'upload'}">${typeLabel}</span>
      ${statusBadge}
      <div class="record-meta">
        <strong>${timestamp}</strong><br>
        ${isExpertView ? `Farmer: ${ownerLabel}<br>` : ''}
        ${detections.length} detection${detections.length !== 1 ? 's' : ''}
      </div>
      <div class="record-detections">
        ${detections.map(d => `<span class="record-det high">${formatDiseaseClass(d.class)} ${Math.round(d.confidence*100)}%</span>`).join('')}
      </div>
      ${isExpertView ? imagePreviewHtml : ''}
      ${locationDisplay}
      ${expertActions}
    </div>
  `;
}

function getExpertFilterLabel(filterName) {
  const value = String(filterName || 'all').toLowerCase();
  if (value === 'all') return 'all records';
  if (value === 'verified') return 'verified records';
  return 'pending uploads';
}

function renderAuditEntry(entry) {
  const timestamp = new Date(entry.timestamp || Date.now()).toLocaleString();
  const actor = entry.actor_email || entry.actor_uid || 'Unknown expert';
  const target = entry.target || {};
  const details = entry.details || {};
  const actionLabel = String(entry.action || 'action').replace(/_/g, ' ');
  const targetLabel = target.disease || target.record_id || target.user_id || '—';
  const detailLabel = Object.keys(details).length
    ? Object.entries(details).map(([key, value]) => `${key}: ${Array.isArray(value) ? value.join(', ') : String(value)}`).join(' • ')
    : 'No details';

  return `
    <div class="record-item">
      <span class="record-status verified">Audit</span>
      <div class="record-meta">
        <strong>${timestamp}</strong><br>
        ${actionLabel} by ${actor}<br>
        Target: ${targetLabel}
      </div>
      <div class="record-detections">
        <span class="record-det high">${detailLabel}</span>
      </div>
    </div>
  `;
}

async function loadUserRecords() {
  if (!currentToken) {
    showToast('Please log in first');
    return;
  }
  
  const listEl = document.getElementById('records-list');
  listEl.innerHTML = '<div class="no-records">Loading records...</div>';
  
  try {
    const res = await fetch('/detections/my-records', {
      headers: {
        'Authorization': `Bearer ${currentToken}`
      }
    });
    
    if (!res.ok) throw new Error('Failed to load records');
    
    const data = await res.json();
    console.log('📊 User records:', data);
    
    const allRecords = data.records || [];
    if (allRecords.length === 0) {
      listEl.innerHTML = '<div class="no-records">No detection records yet.<br>Upload an image or start the drone to record detections.</div>';
      return;
    }

    const records = currentUserRecordsFilter === 'all'
      ? allRecords
      : allRecords.filter((record) => formatVerificationStatus(record.verification_status).cls === currentUserRecordsFilter);

    if (records.length === 0) {
      const statusLabel = currentUserRecordsFilter === 'pending' ? 'pending' : 'verified';
      listEl.innerHTML = `<div class="no-records">No ${statusLabel} detection records yet.</div>`;
    } else {
      listEl.innerHTML = records.map((record, idx) => {
      const timestamp = new Date(record.timestamp).toLocaleString();
      const typeLabel = record.type === 'upload' ? 'Upload' : 'Drone';
      const detections = record.detections || [];
      const status = formatVerificationStatus(record.verification_status);
      const primary = getPrimaryDetection(record);
      const recordKey = getRecordKey(record, idx);
      const recommendationSnapshot = record.recommendation_snapshot || record.recommendationSnapshot || null;
      
      // ✅ Handle GPS from ALL possible sources with ALWAYS-AVAILABLE fallback
      let lat = null;
      let lng = null;
      let gpsSource = 'unknown';
      
      // Try multiple sources for lat/lng extraction
      if (record.lat && record.lng) {
        lat = record.lat;
        lng = record.lng;
        gpsSource = record.gps_source || 'database';
      } else if (record.gps_data && typeof record.gps_data === 'object') {
        // Check for latitude/longitude or lat/lng in gps_data object
        lat = record.gps_data.latitude || record.gps_data.lat;
        lng = record.gps_data.longitude || record.gps_data.lng;
        gpsSource = record.gps_data.source || gpsSource;
      } else if (record.gps && typeof record.gps === 'object') {
        lat = record.gps.lat || record.gps.latitude;
        lng = record.gps.lng || record.gps.longitude;
        gpsSource = record.gps.source || gpsSource;
      }
      
      // Convert to numbers if they exist
      if (lat !== null && lng !== null) {
        lat = parseFloat(lat);
        lng = parseFloat(lng);
      }
      
      // ✅ FALLBACK: Always provide Lanticse farm location if no GPS data
      if (!lat || !lng || isNaN(lat) || isNaN(lng)) {
        lat = 7.3427;
        lng = 125.6290;
        gpsSource = 'farm_default';
        console.log(`📍 Record ${idx}: No GPS found - using Lanticse farm default`);
      }
      
      // Debug log
      console.log(`📍 Record ${idx}: Location - ${lat.toFixed(6)}, ${lng.toFixed(6)} (source: ${gpsSource})`);
      
      // Build location display with map button
      let locationDisplay = '';
      const mapButtonHtml = `<button class="btn btn-o" onclick="switchTab('map', document.querySelector('.nav-link[onclick*=\\'map\\']'));showRecordLocationOnMap(${lat}, ${lng})" style="padding:6px 12px;font-size:11px;margin-top:8px;background:#26865a;color:white;border:none;border-radius:4px;cursor:pointer;"><span>🗺️ Show on Map</span></button>`;
      const recommendationButtonHtml = status.cls === 'verified'
        ? `<button class="btn btn-o" onclick='toggleRecordRecommendation(${JSON.stringify(recordKey)}, ${JSON.stringify(primary.class)}, ${primary.confidence}, this)' style="padding:6px 12px;font-size:11px;margin-top:8px;background:#f0b429;color:#0e1c15;border:none;border-radius:4px;cursor:pointer;"><span>＋ Recommendation</span></button>`
        : `<button class="btn btn-o" disabled style="padding:6px 12px;font-size:11px;margin-top:8px;background:#edf1ec;color:#7a9a8a;border:none;border-radius:4px;cursor:not-allowed;"><span>＋ Recommendation (Pending)</span></button>`;
      locationDisplay = `<div style="margin-top:10px;padding:10px;background:rgba(38, 134, 90, 0.12);border-radius:6px;border-left:4px solid #26865a;">
        <div style="font-size:11px;font-weight:600;color:#164d37;margin-bottom:6px;text-transform:uppercase;letter-spacing:0.5px;">📍 Location Detected</div>
        <div style="font-size:12px;color:#0e1c15;font-family:monospace;font-weight:500;margin-bottom:8px;background:white;padding:6px;border-radius:3px;"><strong>Latitude:</strong> ${lat.toFixed(6)}<br><strong>Longitude:</strong> ${lng.toFixed(6)}</div>
        <div style="font-size:10px;color:#7a9a8a;margin-bottom:6px;"><strong>Source:</strong> ${gpsSource.replace(/_/g, ' ')}</div>
        <div style="display:flex;gap:8px;flex-wrap:wrap;justify-content:flex-start;align-items:center;">
          ${mapButtonHtml}
          ${recommendationButtonHtml}
        </div>
      </div>`;
      const recommendationPanel = status.cls === 'verified'
        ? `<div id="record-recommendation-${recordKey}" data-loaded="${recommendationSnapshot ? '1' : '0'}" style="display:none;margin-top:10px;">${recommendationSnapshot ? renderRecommendationCardHtml(recommendationSnapshot) : ''}</div>`
        : '';
      
      return `
        <div class="record-item">
          <span class="record-type ${record.type}">${typeLabel}</span>
          ${buildRecordStatusBadge(record)}
          <div class="record-meta">
            <strong>${timestamp}</strong><br>
            ${detections.length} detection${detections.length !== 1 ? 's' : ''}
          </div>
          <div class="record-detections">
            ${detections.map(d => `<span class="record-det high">${formatDiseaseClass(d.class)} ${Math.round(d.confidence*100)}%</span>`).join('')}
          </div>
          ${locationDisplay}
          ${recommendationPanel}
        </div>
      `;
      }).join('');
    }
    
    // ✅ Calculate and display disease statistics
    const totalRecords = allRecords.length;
    const allDetections = [];
    const diseaseCounts = {};
    
    allRecords.forEach(record => {
      if (record.detections && Array.isArray(record.detections)) {
        record.detections.forEach(detection => {
          allDetections.push(detection);
          const diseaseClass = detection.class || 'unknown';
          diseaseCounts[diseaseClass] = (diseaseCounts[diseaseClass] || 0) + 1;
        });
      }
    });
    
    const totalDiseases = allDetections.length;
    const mostCommonDisease = Object.keys(diseaseCounts).length > 0 
      ? formatDiseaseClass(Object.entries(diseaseCounts).reduce((a, b) => a[1] > b[1] ? a : b)[0])
      : '—';
    
    // Update statistics display
    const totalDiseasesEl = document.getElementById('total-diseases-found');
    const totalRecordsEl = document.getElementById('total-records-count');
    const mostCommonEl = document.getElementById('most-common-disease');
    const statsEl = document.getElementById('records-stats');
    
    if (totalDiseasesEl) totalDiseasesEl.textContent = totalDiseases;
    if (totalRecordsEl) totalRecordsEl.textContent = totalRecords;
    if (mostCommonEl) mostCommonEl.textContent = mostCommonDisease;
    if (statsEl) statsEl.style.display = totalRecords > 0 ? 'grid' : 'none';
    
    
  } catch (error) {
    console.error('Error loading records:', error);
    listEl.innerHTML = `<div class="no-records">❌ Error loading records<br>${error.message}</div>`;
  }
}

window.loadExpertReview = async function loadExpertReview(filterName = currentExpertFilter || 'all') {
  if (!currentToken || !currentUserIsExpert) return;

  currentExpertFilter = String(filterName || 'all').toLowerCase();
  const pendingListEl = document.getElementById('expert-pending-list');
  const filterStateEl = document.getElementById('expert-filter-state');
  if (pendingListEl) pendingListEl.innerHTML = '<div class="no-records">Loading expert queue...</div>';
  if (filterStateEl) filterStateEl.textContent = `Showing ${getExpertFilterLabel(currentExpertFilter)}`;

  try {
    const [recordsRes, overridesRes] = await Promise.all([
      fetch(`/expert/records?status=${encodeURIComponent(currentExpertFilter)}`, { headers: { 'Authorization': `Bearer ${currentToken}` } }),
      fetch('/expert/recommendations', { headers: { 'Authorization': `Bearer ${currentToken}` } })
    ]);

    if (!recordsRes.ok) throw new Error('Failed to load expert records');

    const recordsData = await recordsRes.json();
    const records = recordsData.records || [];
    const overridesData = overridesRes.ok ? await overridesRes.json() : { overrides: {} };
    const overrides = overridesData.overrides || {};
    if (typeof loadExpertDiseaseOptions === 'function') {
      loadExpertDiseaseOptions();
    } else {
      populateExpertDiseaseSelect();
    }

    const pending = records.filter(record => formatVerificationStatus(record.verification_status).cls === 'pending');
    const pendingCountEl = document.getElementById('expert-pending-records');
    const totalCountEl = document.getElementById('expert-total-records');
    const overrideCountEl = document.getElementById('expert-override-count');
    if (pendingCountEl) pendingCountEl.textContent = pending.length;
    if (totalCountEl) totalCountEl.textContent = records.length;
    if (overrideCountEl) overrideCountEl.textContent = Object.keys(overrides).length;

    if (pendingListEl) {
      pendingListEl.innerHTML = pending.length
        ? pending.map((record, idx) => renderRecordCard(record, idx, { expert: true })).join('')
        : '<div class="no-records">No pending uploads waiting for expert verification.</div>';
    }

    const statusEl = document.getElementById('expert-recommendation-status');
    if (statusEl) {
      statusEl.textContent = `Loaded ${records.length} ${getExpertFilterLabel(currentExpertFilter)} and ${Object.keys(overrides).length} expert overrides.`;
    }
    if (typeof loadExpertAuditLog === 'function') loadExpertAuditLog();
  } catch (error) {
    console.error('Error loading expert review:', error);
    if (pendingListEl) pendingListEl.innerHTML = `<div class="no-records">❌ Error loading expert queue<br>${error.message}</div>`;
  }
};

window.loadExpertAuditLog = async function loadExpertAuditLog() {
  if (!currentToken || !currentUserIsExpert) return;

  const auditListEl = document.getElementById('expert-audit-list');
  if (auditListEl) auditListEl.innerHTML = '<div class="no-records">Loading audit log...</div>';

  try {
    const res = await fetch('/expert/audit-log?limit=50', {
      headers: { 'Authorization': `Bearer ${currentToken}` }
    });
    if (!res.ok) throw new Error('Failed to load audit log');
    const data = await res.json();
    const events = data.events || [];
    if (auditListEl) {
      auditListEl.innerHTML = events.length
        ? events.map(renderAuditEntry).join('')
        : '<div class="no-records">No expert actions recorded yet.</div>';
    }
  } catch (error) {
    console.error('Error loading audit log:', error);
    if (auditListEl) auditListEl.innerHTML = `<div class="no-records">❌ Error loading audit log<br>${error.message}</div>`;
  }
};

window.loadExpertRecommendation = async function loadExpertRecommendation(diseaseName, confidence, target = null) {
  if (!currentToken || !currentUserIsExpert) return;
  currentExpertRecommendationTarget = target && target.userId && target.recordId
    ? { userId: target.userId, recordId: target.recordId }
    : null;
  openExpertRecommendationEditor();

  const diseaseInput = document.getElementById('expert-disease-name');
  const fertilizerInput = document.getElementById('expert-fertilizer');
  const treatmentInput = document.getElementById('expert-treatment');
  const preventionInput = document.getElementById('expert-prevention');
  const noteInput = document.getElementById('expert-note');
  const statusEl = document.getElementById('expert-recommendation-status');
  const disease = (diseaseName || diseaseInput?.value || '').trim();
  const confValue = Number(confidence ?? 0.85);

  if (!disease) {
    showToast('Enter a disease name first');
    return;
  }

  try {
    const res = await fetch('/recommendations/fertilizer', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${currentToken}`
      },
      body: JSON.stringify({
        disease,
        confidence: Number.isFinite(confValue) ? confValue : 0.85
      })
    });
    if (!res.ok) throw new Error('Failed to load recommendation');

    const data = await res.json();
    const recommendation = data.recommendations || {};
    if (diseaseInput) setExpertRecommendationDisease(data.disease || disease);
    if (fertilizerInput) fertilizerInput.value = recommendation.fertilizer || '';
    if (treatmentInput) treatmentInput.value = recommendation.treatment || '';
    if (preventionInput) preventionInput.value = Array.isArray(recommendation.prevention) ? recommendation.prevention.join('\n') : '';
    if (noteInput) noteInput.value = data.note || '';
    if (statusEl) statusEl.textContent = data.source === 'expert_override' ? 'Loaded expert-edited recommendation data.' : 'Loaded default recommendation data.';
    scrollToExpertRecommendationEditor();
    if (diseaseInput) diseaseInput.focus();
  } catch (error) {
    console.error('Error loading expert recommendation:', error);
    showToast('Could not load recommendation');
  }
};

window.editExpertRecommendation = async function editExpertRecommendation(diseaseName, confidence, userId, recordId) {
  if (!currentToken || !currentUserIsExpert) return;
  openExpertRecommendationEditor();
  await loadExpertRecommendation(diseaseName, confidence, { userId, recordId });
  scrollToExpertRecommendationEditor();
};

window.saveExpertRecommendation = async function saveExpertRecommendation() {
  if (!currentToken || !currentUserIsExpert) return;

  const diseaseInput = document.getElementById('expert-disease-name');
  const fertilizerInput = document.getElementById('expert-fertilizer');
  const treatmentInput = document.getElementById('expert-treatment');
  const preventionInput = document.getElementById('expert-prevention');
  const noteInput = document.getElementById('expert-note');
  const statusEl = document.getElementById('expert-recommendation-status');

  const disease = (diseaseInput?.value || '').trim();
  if (!disease) {
    showToast('Please enter a disease name');
    return;
  }

  const payload = {
    disease,
    fertilizer: fertilizerInput?.value || '',
    treatment: treatmentInput?.value || '',
    prevention: (preventionInput?.value || '').split('\n').map(line => line.trim()).filter(Boolean),
    note: noteInput?.value || '',
    active: true
  };

  if (currentExpertRecommendationTarget && currentExpertRecommendationTarget.userId && currentExpertRecommendationTarget.recordId) {
    payload.target_user_id = currentExpertRecommendationTarget.userId;
    payload.target_record_id = currentExpertRecommendationTarget.recordId;
  }

  try {
    const res = await fetch(`/expert/recommendations/${encodeURIComponent(disease)}`, {
      method: 'PUT',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${currentToken}`
      },
      body: JSON.stringify(payload)
    });
    if (!res.ok) {
      const errData = await res.json().catch(() => ({}));
      throw new Error(errData.detail || 'Failed to save recommendation');
    }

    if (statusEl) statusEl.textContent = `Saved expert override for ${disease}.`;
    showToast(`Saved recommendation for ${disease}`);
    openExpertRecommendationEditor();

    const target = currentExpertRecommendationTarget;
    if (target && target.userId && target.recordId) {
      if (statusEl) statusEl.textContent = `Saved recommendation for ${disease} and marked the record verified.`;
      currentExpertRecommendationTarget = null;
      if (typeof loadExpertReview === 'function') {
        loadExpertReview();
      }
      if (typeof loadUserRecords === 'function') {
        loadUserRecords();
      }
    } else if (typeof loadExpertReview === 'function') {
      loadExpertReview();
    }
  } catch (error) {
    console.error('Error saving expert recommendation:', error);
    showToast(`Save failed: ${error.message}`);
  }
};

window.verifyExpertRecord = async function verifyExpertRecord(userId, recordId, status = 'verified') {
  if (!currentToken || !currentUserIsExpert) return;
  if (!userId || !recordId) {
    showToast('Missing record identifiers');
    return;
  }

  try {
    const res = await fetch(`/expert/records/${encodeURIComponent(userId)}/${encodeURIComponent(recordId)}/verify`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${currentToken}`
      },
      body: JSON.stringify({ status })
    });
    if (!res.ok) {
      const errData = await res.json().catch(() => ({}));
      throw new Error(errData.detail || 'Failed to verify record');
    }

    showToast('Record verified');
    if (typeof loadExpertReview === 'function') loadExpertReview();
    if (typeof loadUserRecords === 'function') loadUserRecords();
    if (typeof loadDashboardStats === 'function') loadDashboardStats();
  } catch (error) {
    console.error('Error verifying record:', error);
    showToast(`Verification failed: ${error.message}`);
  }
};

// Initialize auth UI when page loads
document.addEventListener('DOMContentLoaded', () => {
  console.log('Page loaded, initializing app');
  
  // ✅ Prevent drag events from affecting the map or other elements
  document.addEventListener('dragover', (e) => {
    const uploadDrop = document.getElementById('upload-drop');
    // Only allow drag events on the upload drop zone
    if (!uploadDrop || !uploadDrop.contains(e.target)) {
      e.preventDefault();
      e.stopPropagation();
    }
  }, false);
  
  document.addEventListener('drop', (e) => {
    const uploadDrop = document.getElementById('upload-drop');
    // Only allow drop events on the upload drop zone
    if (!uploadDrop || !uploadDrop.contains(e.target)) {
      e.preventDefault();
      e.stopPropagation();
    }
  }, false);
  
  // ✅ Prevent dragging of UI elements
  document.addEventListener('dragstart', (e) => {
    // Prevent dragging of any elements inside panels except file input
    const panel = e.target.closest('.panel');
    if (panel && e.target.id !== 'fileInput') {
      e.preventDefault();
      e.stopPropagation();
    }
  }, false);
});
