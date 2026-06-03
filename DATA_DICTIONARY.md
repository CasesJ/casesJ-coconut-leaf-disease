# Data Dictionary - Coconut Disease Detector System

**Document Version:** 2.0  
**Last Updated:** June 1, 2026  
**System:** Coconut Leaf Disease Detection & Farm Management Platform

---

## Table 1: Detections (SQLite Database)

| Field Name | Data Type | Field Length | Constraints | Description |
|:-----------|:----------|:-------------|:-----------|:-----------|
| id | TEXT | 20 | Primary Key, Required | Unique detection ID (format: user_id_timestamp_ms) |
| user_id | TEXT | — | Foreign Key, Required, Indexed | Firebase UID reference to authenticated user |
| email | TEXT | 100 | Required | User email address |
| timestamp | TEXT | — | Required, Indexed | ISO 8601 UTC timestamp of detection |
| inference_results | TEXT | — | — | JSON string array of YOLO26 detections |
| gps_data | TEXT | — | — | JSON string object with GPS coordinates |
| image_path | TEXT | 255 | — | Local file path of uploaded image |
| is_synced | BOOLEAN | — | Default: 0 | Firebase synchronization status (0=not synced, 1=synced) |
| sync_attempts | INTEGER | — | Default: 0 | Number of Firebase sync attempts |
| error_message | TEXT | — | — | Error message from last failed sync |
| created_at | TIMESTAMP | — | Default: CURRENT_TIMESTAMP | Record creation timestamp |
| updated_at | TIMESTAMP | — | Default: CURRENT_TIMESTAMP | Record last update timestamp |

---

## Table 2: Detection History (JSON File)

**File Path:** `detection_history.json`

| Field Name | Data Type | Field Length | Constraints | Description |
|:-----------|:----------|:-------------|:-----------|:-----------|
| disease_name | VARCHAR | 100 | Required | Detected disease name or "Healthy" |
| confidence | FLOAT | — | Required | Model confidence score (0.0 to 1.0) |
| severity | VARCHAR | 20 | Required | Severity level: high, medium, low |
| field_id | VARCHAR | 20 | Required | Field identifier |
| detection_date | TIMESTAMP | — | Required | ISO 8601 UTC detection timestamp |
| location | JSON Object | — | Optional | GPS location data (can be null) |

---

## Table 3: Inventory Logs (JSON File)

**File Path:** `inventory_logs.json`

| Field Name | Data Type | Field Length | Constraints | Description |
|:-----------|:----------|:-------------|:-----------|:-----------|
| treatment_name | VARCHAR | 100 | Required | Treatment/pesticide product name |
| treatment_type | VARCHAR | 50 | Required | Treatment type: organic, chemical, biological, mechanical |
| cost | FLOAT | — | Required | Treatment cost in Philippine Pesos (PHP) |
| application_date | TIMESTAMP | — | Required | ISO 8601 UTC application timestamp |
| effectiveness_rating | FLOAT | — | Required | Effectiveness rating (0.0 to 5.0 scale) |
| field_id | VARCHAR | 20 | Required | Field where treatment was applied |
| disease_treated | VARCHAR | 100 | Required | Disease name targeted by treatment |
| notes | TEXT | — | Optional | Additional notes or observations |

---

## Table 4: Inference Results (Nested JSON)

**Location:** Stored in `detections.inference_results` as JSON array

| Field Name | Data Type | Field Length | Constraints | Description |
|:-----------|:----------|:-------------|:-----------|:-----------|
| class | VARCHAR | 50 | Required | Disease/health classification name |
| confidence | FLOAT | — | Required | Model confidence score (0.0 to 1.0) |
| bbox | ARRAY[INT] | — | Required | Bounding box coordinates [x1, y1, x2, y2] |

---

## Table 5: GPS Data (Nested JSON)

**Location:** Stored in `detections.gps_data` as JSON object

| Field Name | Data Type | Field Length | Constraints | Description |
|:-----------|:----------|:-------------|:-----------|:-----------|
| latitude | FLOAT | — | Required | Latitude coordinate (decimal degrees) |
| longitude | FLOAT | — | Required | Longitude coordinate (decimal degrees) |
| altitude | FLOAT | — | Optional | Altitude in meters above sea level |
| timestamp | TIMESTAMP | — | Optional | ISO 8601 UTC timestamp of GPS reading |
| accuracy | FLOAT | — | Optional | GPS accuracy radius in meters |
| source | VARCHAR | 50 | Required | GPS data source (drone_exif, browser_geolocation, davao_default_fallback) |

---

## Table 6: Disease Classes (YOLO26 Model Reference)

| Class ID | Disease Name | Category | Typical Severity | Description |
|:---------|:-------------|:---------|:-----------------|:-----------|
| 0 | Caterpillars | Pest/Insect | Medium | Insect pest damage on coconut leaves |
| 1 | Cercospora | Fungal Disease | High | Fungal leaf spot disease |
| 2 | Drying of Leaflets | Physiological | Medium | Leaf drying from dehydration/deficiency |
| 3 | Healthy | Status | None | Healthy coconut leaf, no disease |
| 4 | Pestalotiopsis | Fungal Disease | High | Brown leaf spot fungal disease |
| 5 | bud root | Structural | High | Root, stem, or bud structural issues |

---

## Table 7: Valid Severity Levels

| Severity Level | Confidence Range | Action Required | Timeline |
|:---------------|:-----------------|:---------------:|:---------:|
| high | 0.75 - 1.00 | Immediate treatment | < 1 week |
| medium | 0.50 - 0.74 | Plan treatment | 1-2 weeks |
| low | 0.00 - 0.49 | Monitor | Weekly |

---

## Table 8: Valid Treatment Types

| Treatment Type | Description | Examples |
|:---|:---|:---|
| organic | Natural, pesticide-free treatments | Neem oil, Sulphur dust, Bacillus thuringiensis |
| chemical | Synthetic pesticides and fungicides | Fungicides, insecticides, chemical fertilizers |
| biological | Living organism-based control | Fungal antagonists, predatory insects |
| mechanical | Physical intervention methods | Pruning, irrigation adjustment, hand removal |

---

## Table 9: GPS Data Sources

| Source | Accuracy | Priority | Method |
|:---|:---|:---|:---|
| drone_exif | ±5m | Highest | Extracted from drone photo EXIF metadata |
| browser_geolocation | ±10-50m | High | Browser Geolocation API with user permission |
| davao_default_fallback | ±50km | Lowest | Default Davao region coordinates (fallback) |

---

## Data Validation Rules

| Field | Validation Rule |
|:---|:---|
| user_id | Must be non-empty Firebase UID |
| email | Must be valid RFC 5321 email format |
| timestamp | Must be valid ISO 8601 UTC format |
| confidence | Must be between 0.0 and 1.0 |
| latitude | Must be between -90 and 90 |
| longitude | Must be between -180 and 180 |
| cost | Must be ≥ 0 (Philippine Pesos) |
| effectiveness_rating | Must be between 0.0 and 5.0 |
| disease_name | Must match valid YOLO26 class names |
| severity | Must be: high, medium, or low |
| treatment_type | Must be: organic, chemical, biological, or mechanical |

---

## Data Storage Locations

| Data Type | Primary Storage | Backup Location | Format | Sync Method |
|:---|:---|:---|:---|:---|
| User Credentials | Firebase Auth | None | Firebase | Real-time |
| Detections | SQLite (hybrid_storage.db) | JSON files | SQLite + JSON | Queued |
| Detection History | detection_history.json | Firebase | JSON | Manual |
| Inventory Logs | inventory_logs.json | Firebase | JSON | Manual |
| Detection Records | /detection_records/{user_id}/ | Firebase Storage | JSON | On-sync |
| Images | Local filesystem | Firebase Storage | Binary | On-sync |

---

**End of Data Dictionary**
