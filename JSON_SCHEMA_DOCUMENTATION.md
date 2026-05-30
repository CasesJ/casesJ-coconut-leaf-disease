# 📋 System JSON Schema Documentation

Complete reference guide for all JSON structures used in the Coconut Disease Detector system.

---

## Table of Contents

1. [Detection Record Schema](#1-detection-record-schema) - Single disease detection output
2. [Detection History Schema](#2-detection-history-schema) - Historical detection log
3. [Inventory Logs Schema](#3-inventory-logs-schema) - Treatment and inventory tracking
4. [GPS Data Schema](#4-gps-data-schema) - Location and drone telemetry
5. [Firebase Detection Record](#5-firebase-detection-record-schema) - Cloud storage format
6. [API Response Schemas](#6-api-response-schemas) - HTTP endpoints
7. [WebSocket Message Schema](#7-websocket-message-schema) - Real-time streaming
8. [Local Storage Record](#8-local-storage-record-schema) - SQLite + JSON backup

---

## 1. Detection Record Schema

Single disease detection from the YOLO26 model output.

```json
{
  "class": "Cercospora",
  "confidence": 0.92,
  "bbox": [120, 150, 340, 420]
}
```

### Field Descriptions

| Field | Type | Description | Example |
|-------|------|-------------|---------|
| `class` | string | Disease/health classification | `"Cercospora"`, `"Healthy"`, `"Caterpillars"` |
| `confidence` | float | Confidence score (0-1 range) | `0.92` = 92% confidence |
| `bbox` | array[int] | Bounding box `[x1, y1, x2, y2]` | `[120, 150, 340, 420]` |

### Valid Classes

```
0: "Caterpillars"        - Pest/insect damage
1: "Cercospora"          - Fungal leaf spot
2: "Drying of Leaflets"  - Dehydration/nutrient deficiency
3: "Healthy"             - No disease detected
4: "Pestalotiopsis"      - Brown leaf spot disease
5: "bud root"            - Root/stem issues
```

### Example Array (from /detect/image response)

```json
{
  "detections": [
    {"class": "Cercospora", "confidence": 0.92, "bbox": [120, 150, 340, 420]},
    {"class": "Caterpillars", "confidence": 0.85, "bbox": [50, 80, 220, 300]},
    {"class": "Healthy", "confidence": 0.98, "bbox": [400, 200, 600, 450]}
  ]
}
```

---

## 2. Detection History Schema

Complete record saved in `detection_history.json` file.

```json
{
  "disease_name": "Cercospora",
  "confidence": 0.92,
  "severity": "high",
  "field_id": "field_1",
  "detection_date": "2026-05-18T09:57:32.735809",
  "location": {
    "latitude": 7.30806,
    "longitude": 125.68417,
    "altitude": 85.5
  }
}
```

### Field Descriptions

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `disease_name` | string | ✓ | Name of detected disease or "Healthy" |
| `confidence` | float | ✓ | Detection confidence (0-1) |
| `severity` | string | ✓ | `"high"`, `"medium"`, `"low"` |
| `field_id` | string | ✓ | Unique field identifier |
| `detection_date` | string | ✓ | ISO 8601 timestamp |
| `location` | object | ✗ | GPS coordinates (nullable) |
| `location.latitude` | float | ✗ | Latitude decimal degrees |
| `location.longitude` | float | ✗ | Longitude decimal degrees |
| `location.altitude` | float | ✗ | Altitude in meters |

### Severity Mapping

```
confidence >= 0.85  →  "high"
0.65 <= confidence < 0.85  →  "medium"
confidence < 0.65  →  "low"
```

### Complete Example Array

```json
[
  {
    "disease_name": "Cercospora",
    "confidence": 0.92,
    "severity": "high",
    "field_id": "field_1",
    "detection_date": "2026-05-18T09:57:32.735809",
    "location": {
      "latitude": 7.30806,
      "longitude": 125.68417,
      "altitude": 85.5
    }
  },
  {
    "disease_name": "Healthy",
    "confidence": 0.98,
    "severity": "low",
    "field_id": "field_1",
    "detection_date": "2026-05-18T09:57:32.746639",
    "location": null
  }
]
```

---

## 3. Inventory Logs Schema

Treatment applications and farm inventory tracking in `inventory_logs.json`.

```json
{
  "treatment_name": "Sulphur dust",
  "treatment_type": "organic",
  "cost": 45.0,
  "application_date": "2026-04-18T09:57:32.772323",
  "effectiveness_rating": 4.5,
  "field_id": "field_1",
  "disease_treated": "Cercospora",
  "notes": "Applied early morning, good coverage"
}
```

### Field Descriptions

| Field | Type | Description |
|-------|------|-------------|
| `treatment_name` | string | Name of treatment product |
| `treatment_type` | string | `"organic"`, `"chemical"`, `"biological"`, `"mechanical"` |
| `cost` | float | Cost in USD |
| `application_date` | string | ISO 8601 timestamp |
| `effectiveness_rating` | float | 1-5 rating (1=poor, 5=excellent) |
| `field_id` | string | Which field was treated |
| `disease_treated` | string | Target disease name |
| `notes` | string | Optional application notes |

### Treatment Types

```
"organic"        - Natural/biological treatments (neem oil, sulfur, etc.)
"chemical"       - Synthetic fungicides/pesticides
"biological"     - Living organisms (Bacillus, fungi, etc.)
"mechanical"     - Physical interventions (pruning, irrigation, etc.)
```

### Example Array

```json
[
  {
    "treatment_name": "Sulphur dust",
    "treatment_type": "organic",
    "cost": 45.0,
    "application_date": "2026-04-18T09:57:32.772323",
    "effectiveness_rating": 4.5,
    "field_id": "field_1",
    "disease_treated": "Cercospora",
    "notes": "Applied early morning, good coverage"
  },
  {
    "treatment_name": "Neem oil",
    "treatment_type": "organic",
    "cost": 38.0,
    "application_date": "2026-04-28T09:57:32.772372",
    "effectiveness_rating": 4.0,
    "field_id": "field_1",
    "disease_treated": "Caterpillars",
    "notes": null
  }
]
```

---

## 4. GPS Data Schema

Drone location and flight telemetry data.

```json
{
  "latitude": 7.30806,
  "longitude": 125.68417,
  "altitude": 85.5,
  "timestamp": "2026-05-18T09:57:32.735809",
  "accuracy": 5.0,
  "source": "drone_exif"
}
```

### Field Descriptions

| Field | Type | Description |
|-------|------|-------------|
| `latitude` | float | Decimal degrees, negative = South |
| `longitude` | float | Decimal degrees, negative = West |
| `altitude` | float | Height above ground in meters |
| `timestamp` | string | ISO 8601 timestamp when photo was taken |
| `accuracy` | float | GPS accuracy radius in meters |
| `source` | string | `"drone_exif"`, `"browser"`, `"manual"`, `"none"` |

### GPS Sources

```
"drone_exif"   - Extracted from DJI/drone EXIF metadata (most accurate)
"browser"      - From browser geolocation API (browser fallback)
"manual"       - User-provided coordinates (frontend input)
"none"         - No GPS data available
```

### Accuracy Interpretation

```
< 5m     - Excellent (drone with RTK)
5-20m    - Good (standard drone)
20-100m  - Fair (browser location)
100m+    - Poor (manual entry or cellular)
```

---

## 5. Firebase Detection Record Schema

Complete detection record sent to Firebase (Realtime Database or Firestore).

```json
{
  "timestamp": "2026-05-18T10:15:32.123456",
  "email": "farmer@example.com",
  "detections": [
    {"class": "Cercospora", "confidence": 0.92, "bbox": [120, 150, 340, 420]},
    {"class": "Caterpillars", "confidence": 0.85, "bbox": [50, 80, 220, 300]}
  ],
  "count": 2,
  "source": "upload",
  "lat": 7.30806,
  "lng": 125.68417,
  "filename": "DJI_20260518_090000.jpg",
  "gps_source": "exif"
}
```

### Field Descriptions

| Field | Type | Description |
|-------|------|-------------|
| `timestamp` | string | ISO 8601 - when detection was made |
| `email` | string | User email for tracking |
| `detections` | array | Array of Detection Records |
| `count` | int | Number of detections (convenience field) |
| `source` | string | `"upload"`, `"stream"`, `"batch"` |
| `lat` | float | Latitude coordinate (default: 7.30806) |
| `lng` | float | Longitude coordinate (default: 125.68417) |
| `filename` | string | Original uploaded filename |
| `gps_source` | string | `"exif"`, `"manual"`, `"none"` |

### Storage Paths

**Realtime Database:**
```
users/{userId}/uploads/{pushId}/
```

**Firestore:**
```
users/{userId}/detections/{docId}
```

---

## 6. API Response Schemas

### POST /detect/image Response

```json
{
  "detections": [
    {"class": "Cercospora", "confidence": 0.92, "bbox": [120, 150, 340, 420]}
  ],
  "recorded_count": 1,
  "total_detected": 3,
  "annotated_image_base64": "iVBORw0KGgoAAAANSUhEUgAAAAUA...",
  "user_email": "farmer@example.com",
  "message": "3 detections found (1 saved - >= 50% confidence)",
  "gps_lat": 7.30806,
  "gps_lng": 125.68417,
  "gps_source": "exif"
}
```

### GET /api/analytics Response

```json
{
  "total_detections": 156,
  "disease_breakdown": {
    "Cercospora": {
      "count": 42,
      "avg_confidence": 0.87,
      "severity_distribution": {
        "high": 28,
        "medium": 12,
        "low": 2
      }
    },
    "Caterpillars": {
      "count": 38,
      "avg_confidence": 0.82,
      "severity_distribution": {
        "high": 15,
        "medium": 18,
        "low": 5
      }
    },
    "Healthy": {
      "count": 76,
      "avg_confidence": 0.94,
      "severity_distribution": {
        "high": 0,
        "medium": 0,
        "low": 76
      }
    }
  },
  "field_summary": {
    "field_1": {"detected": 45, "healthy": 20},
    "field_2": {"detected": 32, "healthy": 28},
    "field_3": {"detected": 28, "healthy": 12}
  }
}
```

### GET /api/export-csv Response

CSV format with headers:
```
Disease,Confidence,Severity,Field,Detection Date,Latitude,Longitude
Cercospora,0.92,high,field_1,2026-05-18T09:57:32,7.30806,125.68417
Caterpillars,0.85,medium,field_2,2026-05-18T09:57:32,7.30806,125.68417
```

---

## 7. WebSocket Message Schema

Real-time streaming messages sent over `/ws/{token}` endpoint.

### Client → Server (Upload)

```json
{
  "type": "image_upload",
  "image_base64": "iVBORw0KGgoAAAANSUhEUgAAAAUA...",
  "lat": 7.30806,
  "lng": 125.68417
}
```

### Server → Client (Detection Result)

```json
{
  "type": "detection_result",
  "detections": [
    {"class": "Cercospora", "confidence": 0.92, "bbox": [120, 150, 340, 420]}
  ],
  "latency_ms": 345,
  "fps": 5.2,
  "timestamp": "2026-05-18T10:15:32.123456"
}
```

### Server → Client (Status Update)

```json
{
  "type": "status",
  "message": "Connected to camera stream",
  "timestamp": "2026-05-18T10:15:32.123456"
}
```

### Server → Client (Error)

```json
{
  "type": "error",
  "error": "Camera connection failed",
  "message": "Unable to connect to USB camera",
  "timestamp": "2026-05-18T10:15:32.123456"
}
```

---

## 8. Local Storage Record Schema

Record stored in SQLite database and JSON backup files.

### SQLite Table: `detections`

```sql
CREATE TABLE detections (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  email TEXT NOT NULL,
  timestamp TEXT NOT NULL,
  inference_results TEXT,        -- JSON string
  gps_data TEXT,                 -- JSON string
  image_path TEXT,
  is_synced BOOLEAN DEFAULT 0,
  sync_attempts INTEGER DEFAULT 0,
  error_message TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
```

### JSON Backup Format

**File Path:** `detection_records/{user_id}/detection_YYYY-MM-DDTHH-MM-SS.json`

```json
{
  "id": "offline_user_1715857052000",
  "user_id": "offline_user",
  "email": "offline@local",
  "timestamp": "2026-05-18T10:15:32.735809",
  "inference_results": [
    {"class": "Cercospora", "confidence": 0.92, "bbox": [120, 150, 340, 420]}
  ],
  "gps_data": {
    "latitude": 7.30806,
    "longitude": 125.68417,
    "altitude": 85.5,
    "accuracy": 5.0,
    "source": "drone_exif"
  },
  "image_path": "/path/to/image.jpg",
  "is_synced": false,
  "sync_attempts": 0
}
```

### Record Fields

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Unique record ID (format: `{user_id}_{timestamp_ms}`) |
| `user_id` | string | User identifier |
| `email` | string | User email address |
| `timestamp` | string | ISO 8601 detection timestamp |
| `inference_results` | array | YOLO26 detections (JSON string in DB) |
| `gps_data` | object | Location data (JSON string in DB) |
| `image_path` | string | Local path to original image |
| `is_synced` | boolean | Whether synced to Firebase |
| `sync_attempts` | int | Number of sync retry attempts |
| `error_message` | string | Last sync error (if any) |

---

## Usage Examples

### Reading Detection History

```python
import json

# Load all detections
with open("detection_history.json") as f:
    detections = json.load(f)

# Filter high-severity detections
high_severity = [d for d in detections if d["severity"] == "high"]
print(f"Found {len(high_severity)} high-severity cases")
```

### Accessing Detection Records

```python
from hybrid_storage.local_storage import get_local_storage

storage = get_local_storage()

# Get all records for a user
records = storage.get_user_detections("user_123")
for record in records:
    print(f"{record['email']}: {len(record['inference_results'])} detections")

# Get storage stats
stats = storage.get_storage_stats()
print(f"Database size: {stats['database_size_bytes']} bytes")
print(f"Pending syncs: {stats['pending_records']}")
```

### Working with GPS Data

```python
# Extract location from detection
detection_record = detections[0]
if detection_record["location"]:
    lat = detection_record["location"]["latitude"]
    lng = detection_record["location"]["longitude"]
    print(f"Detection at: {lat}, {lng}")
```

---

## Data Flow Diagram

```
┌─────────────────┐
│  Drone Photo    │
│  (JPG + EXIF)   │
└────────┬────────┘
         │
         ├─ Extract GPS from EXIF
         │
    ┌────▼────────────┐
    │  YOLO26 Model   │
    │  Inference      │
    └────┬────────────┘
         │
    ┌────▼──────────────────────┐
    │  Detection Records Array   │
    │  [{class, confidence, ...}]│
    └────┬──────────────────────┘
         │
         ├─ Save to:
         │  • detection_history.json
         │  • hybrid_storage.db
         │  • Firebase (RTDB/Firestore)
         │
    ┌────▼──────────────────┐
    │  HTTP Response        │
    │  /{detections array}  │
    │  /annotated image     │
    │  /gps coordinates     │
    └───────────────────────┘
```

---

## Common Queries

### Count detections by disease

```python
from collections import Counter
import json

with open("detection_history.json") as f:
    detections = json.load(f)

disease_counts = Counter(d["disease_name"] for d in detections)
for disease, count in disease_counts.most_common():
    print(f"{disease}: {count}")
```

### Calculate average confidence by field

```python
from collections import defaultdict

field_stats = defaultdict(lambda: {"sum": 0, "count": 0})

for d in detections:
    field_id = d["field_id"]
    field_stats[field_id]["sum"] += d["confidence"]
    field_stats[field_id]["count"] += 1

for field_id, stats in field_stats.items():
    avg_conf = stats["sum"] / stats["count"]
    print(f"{field_id}: {avg_conf:.2%}")
```

### Export detections for a specific date

```python
from datetime import datetime

target_date = "2026-05-18"
daily_detections = [
    d for d in detections 
    if d["detection_date"].startswith(target_date)
]
print(f"Detections on {target_date}: {len(daily_detections)}")
```

---

## Best Practices

✅ **DO:**
- Always validate incoming JSON against schema before processing
- Store GPS data with every detection for traceability
- Include timestamps in ISO 8601 format
- Use proper confidence thresholds (≥0.50 for savings)
- Handle null/missing optional fields gracefully

❌ **DON'T:**
- Trust confidence scores > 1.0 (data corruption)
- Omit user identification from detection records
- Mix coordinate systems (use decimal degrees consistently)
- Store binary image data in JSON (use base64 or separate files)
- Assume GPS is always available (may be null)

---

**Last Updated**: May 30, 2026  
**YOLO Model**: YOLO26s OpenVINO  
**Storage Backend**: SQLite + Firebase  
**API Version**: v1.0
