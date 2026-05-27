# 🚁 POST-FLIGHT GPS APPROACH - Complete Guide

## Overview

Your coconut disease detector now uses the **Post-Flight Approach** - the industry standard for drone agriculture (used by DroneDeploy, WebODM, Pix4D, etc.).

### How It Works

```
1. DRONE FLIGHT          →  2. UPLOAD IMAGES      →  3. EXTRACT GPS      →  4. MAP RESULTS
Autonomous grid flight      User drag-drop photos      From EXIF metadata      Leaflet/Google Maps
(saves to SD card)          to web portal              Auto-detected           Show diseased trees
High-res geotagged photos   (multi-upload)                                    with colored pins
```

---

## ✅ WORKFLOW

### Step 1: Prepare Your Drone
1. **Configure Flight Pattern** - Set your drone to fly a grid pattern over the plantation
2. **Enable GPS Tagging** - Ensure EXIF GPS is embedded in photos (most drones do this by default)
3. **Flight Details**:
   - High overlap (80%+) between photos
   - Altitude: 50-100m above ground (adjust resolution as needed)
   - Flight speed: 5-10 m/s
   - Save photos to SD card

### Step 2: Upload Images
**Two Options:**

#### Option A: Web Portal (Recommended)
1. Open your app in browser
2. Go to **Upload** section
3. Drag & drop drone photos (or multi-select)
4. System automatically extracts GPS from EXIF
5. Images process and results map appears

#### Option B: API Upload
```bash
curl -X POST http://localhost:8000/detect/image \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "file=@photo_001.jpg" \
  # Optional: -F "lat=7.30806" -F "lng=125.68417"
```

### Step 3: GPS Extraction (Automatic)
The system:
1. ✅ Reads image EXIF metadata
2. ✅ Extracts GPS coordinates (lat, lng, altitude)
3. ✅ Extracts timestamp from photo
4. ✅ Falls back to manual coordinates if no EXIF

**What's extracted:**
```python
{
  "lat": 7.30806,           # Latitude (degrees)
  "lng": 125.68417,         # Longitude (degrees)
  "altitude": 45.5,         # Altitude (meters above ground)
  "timestamp": "2026-05-26T10:30:45",  # Photo time
  "accuracy": 5.0           # GPS accuracy (meters)
}
```

### Step 4: Disease Detection & Mapping
1. Disease detection runs on each image
2. High-confidence detections (≥50%) are saved
3. **Results are mapped** with GPS coordinates:
   - 🟢 Green pin = No disease detected
   - 🟡 Yellow pin = Low severity
   - 🔴 Red pin = High severity

---

## 📱 API ENDPOINT

### POST `/detect/image`
Upload a single image for disease detection with automatic GPS extraction.

**Request:**
```bash
POST /detect/image
Content-Type: multipart/form-data

Parameters:
- file (required): Image file from drone
- lat (optional): Latitude (used if EXIF has no GPS)
- lng (optional): Longitude (used if EXIF has no GPS)
```

**Response:**
```json
{
  "success": true,
  "detections": 2,
  "high_confidence": 1,
  "gps_source": "exif",
  "location": {
    "lat": 7.30806,
    "lng": 125.68417,
    "altitude": 45.5
  },
  "results": {
    "diseases": [
      {
        "disease": "leaf_spot",
        "confidence": 0.87,
        "bbox": [100, 150, 200, 250]
      }
    ]
  }
}
```

---

## 🛠️ DRONE SETUP EXAMPLES

### DJI Drones (Most Common)
✅ **Supported** - DJI drones automatically embed EXIF GPS
- Phantom 4 Pro
- Matrice 300 RTK
- Agras T20
- etc.

**Requirements:**
- GPS enabled (default)
- Photos saved as JPEG
- No special settings needed

### Other Drones
| Drone | EXIF GPS | Notes |
|-------|----------|-------|
| Auterion Skynode | ✅ | Professional RTK |
| Freefly Astro | ✅ | Cinema camera |
| Yamaha Fazer | ✅ | Agricultural |
| Custom/DIY | ⚠️ | May need GPS module |

**If your drone doesn't embed GPS:**
- Manually provide lat/lng via form
- Record GPS track separately, match by timestamp
- Contact us for custom integration

---

## 🗺️ FRONTEND MAPPING

### Current Implementation
The map (Leaflet.js) shows:
- 📍 Colored pins for each detection
- 🔵 Drone flight path
- 📊 Heatmap of disease concentration
- 📈 Historical data overlay

### Example Integration (HTML/JS)
```html
<div id="map" style="height: 500px;"></div>

<script src="https://leafletjs.com/dist/leaflet.js"></script>
<script>
  // Create map
  const map = L.map('map').setView([7.308, 125.684], 13);
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png').addTo(map);
  
  // Add detection points
  fetch('/api/detections')
    .then(r => r.json())
    .then(data => {
      data.forEach(detection => {
        const color = detection.severity === 'high' ? 'red' : 
                      detection.severity === 'medium' ? 'yellow' : 'green';
        L.circleMarker([detection.lat, detection.lng], {
          radius: 10,
          color: color,
          fillOpacity: 0.8
        }).addTo(map);
      });
    });
</script>
```

---

## 🔍 TROUBLESHOOTING

### Issue: "No GPS data in {filename}"
**Cause:** Image has no EXIF GPS

**Solutions:**
1. Ensure drone GPS is enabled
2. Wait for GPS lock before flying (may take 30s)
3. Provide lat/lng manually in upload form
4. Check image isn't stripped of EXIF by editing software

**Verify EXIF:**
```bash
# Linux/Mac:
exiftool image.jpg | grep GPS

# Or use Python:
from PIL import Image
from PIL.ExifTags import TAGS
img = Image.open('image.jpg')
print(img._getexif())
```

### Issue: GPS accuracy seems off
**Possible causes:**
- Drone GPS lock was weak (need clear sky)
- Camera time sync was wrong
- Altitude in image is absolute MSL, not above ground level

**Verification:**
- Compare image EXIF timestamp with flight log
- Check drone altitude setting
- Use RTK drone for ±2cm accuracy (if critical)

### Issue: Images not uploading
**Check:**
1. File size (max 50MB per image)
2. Format (JPEG, PNG supported)
3. Network connection
4. Server logs: `tail -f main.py`

---

## 📊 DATABASE STORAGE

### Local Storage (Offline)
```
detection_records/
├── USER_ID/
│   ├── detection_2026-05-26T10_30_45.json
│   ├── detection_2026-05-26T11_15_30.json
│   └── ...
```

### Firebase Cloud (Online)
```
/detections/{user_id}/{timestamp}
{
  "email": "farmer@example.com",
  "timestamp": "2026-05-26T10:30:45",
  "gps_source": "exif",
  "lat": 7.30806,
  "lng": 125.68417,
  "filename": "DJI_0001.jpg",
  "detections": [
    {
      "disease": "leaf_spot",
      "confidence": 0.87,
      "bbox": [100, 150, 200, 250]
    }
  ]
}
```

---

## 🚀 ADVANCED FEATURES

### Batch Processing
Process multiple images at once:
```bash
for image in *.jpg; do
  curl -X POST http://localhost:8000/detect/image \
    -H "Authorization: Bearer TOKEN" \
    -F "file=@$image"
done
```

### RTK GPS (Centimeter Accuracy)
If using RTK-enabled drone (DJI M300 RTK, Auterion):
- ±2cm horizontal accuracy
- ±5cm vertical accuracy
- Ideal for precision mapping

### Time-Series Analysis
Track disease progression over time:
1. Fly same area weekly
2. Stack results in timeline
3. See disease spread patterns
4. Predict future spread

### Custom Ground Control Points (GCP)
For orthorectification:
1. Place visible markers in field
2. Record GPS coordinates
3. Match in post-processing
4. ±5cm positional accuracy

---

## 📞 SUPPORT

**Issues with EXIF extraction?**
- Check [TROUBLESHOOTING.md](TROUBLESHOOTING.md)
- Run in simulation mode: `use_simulation=True`
- View server logs for error details

**Custom drone integration?**
- Edit `drone_gps.py` `extract_gps_from_image()` method
- Add support for custom GPS formats
- Contact for implementation

---

## 📚 References

- **EXIF Standard:** https://en.wikipedia.org/wiki/Exif
- **DJI SDK:** https://developer.dji.com/
- **DroneDeploy API:** https://api.dronedeploy.com/
- **OpenDroneMap:** https://www.opendronemap.org/

---

**Last Updated:** May 26, 2026  
**Version:** 2.0 (Post-Flight Approach)
