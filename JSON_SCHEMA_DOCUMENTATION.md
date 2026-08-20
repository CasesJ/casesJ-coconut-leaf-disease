# API and JSON Schemas

## `POST /detect/image`

The response includes `detections` (array of class/confidence/bounding-box data), annotated image output when available, image metadata, and location data where available.

## Detection Record

```json
{
  "id": "record-id",
  "user_id": "firebase-user-id",
  "timestamp": "2026-08-19T00:00:00Z",
  "detections": [{"class": "leaf_rot", "confidence": 0.82, "bbox": [1, 2, 3, 4]}],
  "verification_status": "pending_verification",
  "lat": 7.35218,
  "lng": 125.64135
}
```

Verified records add `verified_by`, `verified_at`, `recommendation_snapshot`, and `recommendation_source`.

## Notifications

`GET /notifications` returns `{ "notifications": [], "unread_count": 0 }`. Each notification has `id`, `record_id`, `title`, `message`, `type`, `read`, and `created_at`. `POST /notifications/mark-read` preserves history; `POST /notifications/clear` removes it for the current user only.

## Expert endpoints

- `GET /expert/records?status=all|pending|verified`
- `POST /expert/records/{user_id}/{record_id}/verify`
- `GET /expert/audit-log`

All expert endpoints require an authenticated expert identity.
