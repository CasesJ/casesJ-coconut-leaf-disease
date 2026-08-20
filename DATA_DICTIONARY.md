# CoconutAI Data Dictionary

## Detection Record

| Field | Description |
|---|---|
| `id` | Unique Detection Record identifier |
| `user_id`, `email` | Record owner |
| `timestamp` | ISO-8601 record time |
| `detections` | Array of YOLO11 detections (`class`, `confidence`, optional `bbox`) |
| `primaryDisease`, `primaryConfidence` | Primary detection selected from result data |
| `image_url`, `annotated_image_url` | Original and annotated upload locations |
| `lat`, `lng`, `gps_data`, `gps_source` | Detection location and source |
| `verification_status` | `pending_verification` or `verified` |
| `verified_by`, `verified_by_uid`, `verified_at` | Expert verification details |
| `recommendation_snapshot` | Record-level verified recommendation |
| `recommendation_source` | Default recommendation or `expert_override` |

## Notification

Notifications are per-user. They are stored in Firebase RTDB when available, with `user_notifications.json` as a local fallback.

| Field | Description |
|---|---|
| `id` | Notification identifier |
| `record_id` | Related Detection Record |
| `title`, `message` | Visible verification alert |
| `type` | `verification` |
| `read` | Cleared from the bell count after viewing |
| `created_at` | ISO-8601 creation time |

## Standard terminology

- **Total Detections:** count of all detection objects.
- **Diseased Trees:** disease detections.
- **Healthy Trees:** healthy detections.
- **Mapped Locations:** unique GPS locations.
- **Detection Records:** persistent inference records.
- **Disease Prevalence:** disease percentage for a selected sample or area.

## Storage

- `hybrid_storage.db` — local SQLite data
- `detection_records/` — local JSON backups
- `static/uploads/` — original images
- `static/annotated_uploads/` — annotated images
- `expert_audit_log.jsonl` — expert action audit trail
