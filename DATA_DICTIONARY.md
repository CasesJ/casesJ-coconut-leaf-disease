# CoconutAI Data Dictionary

## Detection Record

| Field | Description |
|---|---|
| `id` | Unique detection-record identifier |
| `user_id`, `email` | Record owner |
| `timestamp` | ISO-8601 upload time |
| `detections` | YOLO26 v6 detection array (`class`, `confidence`, optional `bbox`) |
| `image_url`, `annotated_image_url` | Original and annotated upload assets |
| `lat`, `lng`, `gps_data`, `gps_source` | Location and its source |
| `verification_status` | `pending_verification` or `verified` |
| `verified_by`, `verified_by_uid`, `verified_at` | Expert verification details |
| `recommendation_snapshot` | Record-specific guidance after expert action or farmer-review API submission |
| `recommendation_source` | `default`, `expert_override`, or `farmer_review` |
| `farmer_recommendation_confirmed_at` | Timestamp of a low-confidence farmer-review API submission, if used |

## Confidence rules

- Upload detections are displayed and recorded at the current 5% review threshold.
- The primary detection’s confidence determines recommendation visibility.
- **Above 50%:** the upload is automatically marked `verified`; no expert review is needed.
- **50% and below:** the upload remains `pending_verification` and appears under **Needs Review**.

## Notification

Notifications are per-user and are stored in Firebase RTDB when available, with `user_notifications.json` as a local fallback. Each has `id`, `record_id`, `title`, `message`, `type`, `read`, and `created_at`.

## Storage

- `hybrid_storage.db` — local SQLite records
- `detection_records/` — local JSON backups
- `static/uploads/` — original uploads
- `static/annotated_uploads/` — annotated images
- `expert_audit_log.jsonl` — expert action audit events
