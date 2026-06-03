// ✅ app.js - Function definitions only
// Variables and Firebase initialization are in index.html <head>
// This file defines all application functions that override the stubs

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
  document.getElementById('error-message').innerHTML = '';
  
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
        form.reset();
        console.log('Auth complete');
        // ✅ Set flag and show app immediately
        userHasExplicitlyLoggedIn = true;
        currentUser = userCred.user;
        document.getElementById('auth-screen').classList.add('hidden');
        document.getElementById('app-screen').classList.add('active');
        updateUIOnLogin(userCred.user);
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

window.updateUIOnLogin = function updateUIOnLogin(user) {
  document.getElementById('login-btn').style.display = 'none';
  document.getElementById('user-badge').style.display = 'flex';
  document.getElementById('user-email').textContent = user.email;
  document.getElementById('current-user-email').innerHTML = '<strong>User:</strong> ' + user.email;
  // ✅ Load saved map pins from Firebase when user logs in
  loadSavedMapPins(user.uid);
  // ✅ Load dashboard stats
  if (typeof loadDashboardStats === 'function') {
    setTimeout(loadDashboardStats, 300);
  }
};

window.updateUIOnLogout = function updateUIOnLogout() {
  document.getElementById('login-btn').style.display = 'flex';
  document.getElementById('user-badge').style.display = 'none';
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
    if (!currentToken) {
      console.warn('No token available for dashboard stats');
      return;
    }
    
    // Fetch user records from backend
    const response = await fetch('/records?user_id=' + currentUser.uid, {
      headers: { 'Authorization': 'Bearer ' + currentToken }
    });
    
    if (!response.ok) {
      console.warn('Failed to load dashboard stats:', response.status);
      return;
    }
    
    const records = await response.json();
    
    // Calculate stats
    let totalDetections = records.length;
    let diseaseCount = records.filter(r => r.disease_status === 'disease' || r.disease_status === 'diseased').length;
    let healthyCount = records.filter(r => r.disease_status === 'healthy').length;
    
    // Update stat boxes
    document.getElementById('total-detections').textContent = totalDetections;
    document.getElementById('disease-count').textContent = diseaseCount;
    document.getElementById('healthy-count').textContent = healthyCount;
    
    // Update dashboard records list
    const dashboardRecords = document.getElementById('dashboard-records');
    if (records.length === 0) {
      dashboardRecords.innerHTML = '<div class="no-events">No detections recorded yet. Start uploading images or use the drone camera.</div>';
      return;
    }
    
    // Show recent 10 records
    dashboardRecords.innerHTML = records.slice(0, 10).map(record => {
      const status = record.disease_status === 'healthy' ? '✓ Healthy' : '⚠ Disease Detected';
      const iconClass = record.disease_status === 'healthy' ? 'healthy' : (record.disease_status === 'warning' ? 'warning' : 'disease');
      const icon = record.disease_status === 'healthy' ? '✓' : (record.disease_status === 'warning' ? '⚠' : '⚠');
      const timestamp = new Date(record.timestamp).toLocaleDateString();
      const source = record.source || 'Upload';
      const sourceType = source.toLowerCase() === 'rtdb' ? 'drone' : 'upload';
      
      return `
        <div class="record-item">
          <div class="record-icon ${iconClass}">${icon}</div>
          <div class="record-content">
            <div class="record-title">${status}</div>
            <div class="record-type ${sourceType}">${source}</div>
            <div class="record-meta">${timestamp}</div>
          </div>
        </div>
      `;
    }).join('');
  } catch (error) {
    console.error('Error loading dashboard stats:', error);
  }
};



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
    
    // Add sky layer for better 3D visualization
    mainMap.addLayer({
      id: 'sky',
      type: 'sky',
      paint: {
        'sky-type': 'gradient',
        'sky-gradient': ['interpolate', ['linear'], ['sky-radial-progress'],
          0.8, '#87CEEB',
          1, '#E0F6FF'
        ]
      }
    });
    
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
  document.querySelectorAll('.panel').forEach(p=>p.classList.remove('active'));
  document.querySelectorAll('.ntab').forEach(b=>b.classList.remove('active'));
  document.querySelectorAll('.nav-link').forEach(l=>l.classList.remove('active'));
  document.getElementById('panel-'+name).classList.add('active');
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
  setTimeout(()=>{
    if(name==='map' && mapsReady) mainMap.resize();
    if(name==='drone' && mapsReady) miniMap.resize();
  },60);
}

// ── Toast ──
function showToast(msg){const t=document.getElementById('toast');t.textContent=msg;t.classList.add('show');setTimeout(()=>t.classList.remove('show'),3000);}

// ── Helper ──
function cls(label,conf){if(label==='healthy')return 'h';return conf<0.6?'w':'d';}
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
  document.getElementById('pin-count').textContent=log.length+' pins';
}

function renderLog(){
  const el=document.getElementById('events-list');
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
  const total=log.length, dis=log.filter(d=>d.label!=='healthy').length, ok=total-dis;
  ['ms-total','ms-dis','ms-ok'].forEach((id,i)=>document.getElementById(id).textContent=[total,dis,ok][i]);
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
  const diseaseSource = mainMap.getSource('disease-source');
  if (diseaseSource) {
    diseaseSource.setData({ type: 'FeatureCollection', features: [] });
  }
  
  renderLog();updateStats();
  document.getElementById('pin-count').textContent='0 pins';
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
function handleDrop(e){
  e.preventDefault();document.getElementById('upload-drop').classList.remove('over');
  const f=e.dataTransfer.files[0];if(f&&f.type.startsWith('image/'))detectImage(f);else showToast('Please drop an image file.');
}
async function detectImage(file){
  if(!file)return;
  if (!currentUser) {
    showToast('Session expired, please refresh');
    return;
  }
  document.getElementById('upload-loading').classList.add('on');
  document.getElementById('result-area').style.display='none';
  
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
    renderUpload(data);
    // ✅ Pin detections to farm center with slight random spread within bounds
    data.detections.forEach(d=>addPin(gps_lat+(Math.random()-.5)*.0003,gps_lng+(Math.random()-.5)*.0003,d.class,d.confidence,'Upload'));
    document.getElementById('upload-loading').classList.remove('on');
  }).catch(err=>{
    showToast('Detection failed: '+err.message);
    document.getElementById('upload-loading').classList.remove('on');
  });
}
function renderUpload(data){
  document.getElementById('result-img').src='data:image/jpeg;base64,'+data.annotated_image_base64;
  document.getElementById('result-area').style.display='block';
  const n=data.detections.length;
  document.getElementById('count-badge').textContent=n+' found';
  const list=document.getElementById('det-list');
  list.innerHTML=n===0?'<div class="empty-state">No diseases detected</div>':data.detections.map(d=>{
    const c=cls(d.class,d.confidence),pct=Math.round(d.confidence*100);
    return `<div class="ditem"><div class="ddot ${c}"></div><div class="dinfo"><div class="dlabel">${d.class.replace(/_/g,' ')}</div><div class="dbar-bg"><div class="dbar ${c}" style="width:${pct}%"></div></div><div class="dpct">${pct}% confidence</div></div></div>`;
  }).join('');
  const avg=n>0?Math.round(data.detections.reduce((a,d)=>a+d.confidence,0)/n*100)+'%':'—';
  const bad=data.detections.some(d=>d.class!=='healthy');
  document.getElementById('s-total').textContent=n;
  document.getElementById('s-conf').textContent=avg;
  document.getElementById('s-status').textContent=n===0?'Clear':bad?'Diseased':'Healthy';
  document.getElementById('s-status').style.color=bad?'var(--red)':'var(--accent)';
  
  // ✅ Fetch and display recommendations for detected disease
  if(n>0){
    const primary=data.detections.reduce((a,d)=>d.confidence>a.confidence?d:a);
    if(primary.class!=='healthy'){
      fetchRecommendation(primary.class, primary.confidence);
    }
  }
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
  
  // Handle prevention array - convert to readable list
  const preventionText = Array.isArray(rec.recommendations.prevention)
    ? rec.recommendations.prevention.join(' • ')
    : rec.recommendations.prevention;
  
  recArea.innerHTML=`
    <div class="rec-card">
      <div class="rec-title">🌾 Farmer Recommendation — ${rec.disease} (${rec.confidence_percent}%)</div>
      <div class="rec-item">
        <div class="rec-label">🧪 Fertilizer</div>
        <div class="rec-text">${rec.recommendations.fertilizer}</div>
      </div>
      <div class="rec-item">
        <div class="rec-label">💊 Treatment</div>
        <div class="rec-text">${rec.recommendations.treatment}</div>
      </div>
      <div class="rec-item">
        <div class="rec-label">🛡️ Prevention</div>
        <div class="rec-text">${preventionText}</div>
      </div>
      <div style="margin-top:12px; padding-top:12px; border-top:1px solid rgba(11,42,31,.1); font-size:11px; color:var(--text3);">
        ${rec.note}
      </div>
    </div>
  `;
  recArea.style.display='block';
}

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
            if(d.class!=='healthy' && d.confidence >= MIN_CONFIDENCE){
              addPin(lat, lng, d.class, d.confidence, 'Drone');
              droneDetectionCount++;
              droneSessionPins.push({lat, lng, class: d.class, confidence: d.confidence});
              
              // Update detection counter badge
              document.getElementById('pin-count').textContent = droneDetectionCount + ' diseases pinned';
              
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
              if(d.class!=='healthy' && d.confidence >= MIN_CONFIDENCE){
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
  
  document.getElementById('sdot').classList.remove('live');
  document.getElementById('sdot-lbl').textContent='Offline';
  
  // Show session summary
 
  document.getElementById('live-dets').innerHTML='<span class="live-empty">Start camera to detect diseases</span>';
  document.getElementById('canvas-ph').classList.remove('hidden');
  document.getElementById('droneCanvas').getContext('2d').clearRect(0,0,640,480);
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
    
    if (!data.records || data.records.length === 0) {
      listEl.innerHTML = '<div class="no-records">No detection records yet.<br>Upload an image or start the drone to record detections.</div>';
      return;
    }
    
    listEl.innerHTML = data.records.map((record, idx) => {
      const timestamp = new Date(record.timestamp).toLocaleString();
      const typeLabel = record.type === 'upload' ? 'Upload' : 'Drone';
      const detections = record.detections || [];
      
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
      const mapButtonHtml = `<button class="btn btn-o" onclick="switchTab('map', document.querySelector('.ntab:nth-child(3)'));setMapCenter(${lat}, ${lng});showToast('📍 Location: ${lat.toFixed(5)}, ${lng.toFixed(5)} (${gpsSource})')" style="padding:6px 12px;font-size:11px;margin-top:8px;background:#26865a;color:white;border:none;border-radius:4px;cursor:pointer;"><span>🗺️ Show on Map</span></button>`;
      locationDisplay = `<div style="margin-top:10px;padding:10px;background:rgba(38, 134, 90, 0.12);border-radius:6px;border-left:4px solid #26865a;">
        <div style="font-size:11px;font-weight:600;color:#164d37;margin-bottom:6px;text-transform:uppercase;letter-spacing:0.5px;">📍 Location Detected</div>
        <div style="font-size:12px;color:#0e1c15;font-family:monospace;font-weight:500;margin-bottom:8px;background:white;padding:6px;border-radius:3px;"><strong>Latitude:</strong> ${lat.toFixed(6)}<br><strong>Longitude:</strong> ${lng.toFixed(6)}</div>
        <div style="font-size:10px;color:#7a9a8a;margin-bottom:6px;"><strong>Source:</strong> ${gpsSource.replace(/_/g, ' ')}</div>
        ${mapButtonHtml}
      </div>`;
      
      return `
        <div class="record-item">
          <span class="record-type ${record.type}">${typeLabel}</span>
          <div class="record-meta">
            <strong>${timestamp}</strong><br>
            ${detections.length} detection${detections.length !== 1 ? 's' : ''} (≥50% confidence)
          </div>
          <div class="record-detections">
            ${detections.map(d => `<span class="record-det high">${d.class.replace(/_/g,' ')} ${Math.round(d.confidence*100)}%</span>`).join('')}
          </div>
          ${locationDisplay}
        </div>
      `;
    }).join('');
    
    // ✅ Calculate and display disease statistics
    const totalRecords = data.records.length;
    const allDetections = [];
    const diseaseCounts = {};
    
    data.records.forEach(record => {
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
      ? Object.entries(diseaseCounts).reduce((a, b) => a[1] > b[1] ? a : b)[0].replace(/_/g, ' ')
      : '—';
    
    // Update statistics display
    document.getElementById('total-diseases-found').textContent = totalDiseases;
    document.getElementById('total-records-count').textContent = totalRecords;
    document.getElementById('most-common-disease').textContent = mostCommonDisease;
    document.getElementById('records-stats').style.display = totalRecords > 0 ? 'grid' : 'none';
    
    
  } catch (error) {
    console.error('Error loading records:', error);
    listEl.innerHTML = `<div class="no-records">❌ Error loading records<br>${error.message}</div>`;
  }
}

// Initialize auth UI when page loads
document.addEventListener('DOMContentLoaded', () => {
  console.log('Page loaded, initializing app');
});
