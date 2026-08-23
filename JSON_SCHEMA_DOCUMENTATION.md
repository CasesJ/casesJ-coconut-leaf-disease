# API and JSON Schemas

## `POST /detect/image`

Accepts a multipart image upload and optional `lat`, `lng`, and `accuracy` form fields. The response includes:

```json
{
  "detections": [{"class": "leaf_rot", "confidence": 0.82, "bbox": [1, 2, 3, 4]}],
  "record_id": "upload-user-time-random",
  "recorded_count": 1,
  "annotated_image_base64": "...",
  "gps_lat": 7.35218,
  "gps_lng": 125.64135,
  "model": {"name": "YOLO26 v6"}
}
```

## Detection Record

```json
{
  "id": "record-id",
  "user_id": "firebase-user-id",
  "timestamp": "2026-08-22T00:00:00Z",
  "detections": [{"class": "leaf_rot", "confidence": 0.82}],
  "verification_status": "pending_verification",
  "lat": 7.35218,
  "lng": 125.64135
}
```

Verified records add verifier fields and a `recommendation_snapshot`. Expert recommendations use `recommendation_source: "expert_override"`.

## Recommendations

`POST /recommendations/fertilizer` accepts `{"disease": "leaf_rot", "confidence": 0.82}` and returns disease, `confidence_percent`, model information, fertilizer/treatment/prevention guidance, and a note.

The browser displays this guidance for an uploaded result only at 50% confidence or higher. The endpoint itself remains available to authenticated clients.

`PUT /detections/my-records/{record_id}/low-confidence-recommendation` accepts farmer-reviewed guidance for an authenticated record below 50%; it keeps the record pending for expert verification.

## Expert endpoints

- `GET /expert/records?status=all|pending|verified`
- `POST /expert/records/{user_id}/{record_id}/verify`
- `PUT /expert/recommendations/{disease}`
- `GET /expert/audit-log`

All expert endpoints require an authenticated expert identity.
