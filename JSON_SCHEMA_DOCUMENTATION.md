# API and JSON Schemas

Updated against the local source on October 24, 2026. The local backend /docs and app/schemas define the complete request contracts. Examples below are abbreviated.

Private operations require a Firebase bearer token; expert operations additionally require expert authorization. Tree identity operations use verified-token dependencies.

## Detection upload

POST /detect/image accepts multipart file (JPEG/PNG). Optional lat, lng, and accuracy fields exist for compatibility; uploaded-image location is extracted from valid embedded EXIF GPS, not these fields.

```json
{
  "detections": [{"class": "Stem Bleeding", "confidence": 0.82, "bbox": [10, 20, 100, 200]}],
  "record_id": "upload_example",
  "recorded_count": 1,
  "total_detected": 1,
  "annotated_image_base64": "...",
  "display_confidence_threshold": 0.05,
  "record_confidence_threshold": 0.05,
  "gps_lat": null,
  "gps_lng": null,
  "gps_source": "unknown",
  "gps_metadata_found": false,
  "image_dimensions": {"width": 640, "height": 640},
  "model": {"name": "YOLO26 v6", "backend": "ultralytics", "weights": "weights.pt"}
}
```

Read actual model metadata from the response. A successful empty analysis returns no new record ID. Inference/storage failures return errors. Persisted detections add normalized boxes, image_assets, annotation_format, owner/timestamp, and pending_verification status. New annotated image copies are not stored.

## Records, recommendations, and experts

- GET /detections/my-records: signed-in farmer history.
- POST /recommendations/fertilizer: disease and confidence input; guidance, model/source details, and note output.
- PUT /detections/my-records/{record_id}/low-confidence-recommendation: farmer-reviewed guidance below 50%; remains pending.
- GET /expert/records?status=all|pending|verified: expert queue.
- POST /expert/records/{user_id}/{record_id}/verify: verification.
- PUT /expert/recommendations/{disease}: expert guidance editing; selected-record actions save snapshots and verify.
- GET /expert/audit-log and GET /notifications: audit and notification views.
- GET /reports/my-records.pdf and GET /api/export-csv: exports.

Upload guidance is visible at primary confidence >= 0.5. Below 0.5 it is hidden on the upload screen. All saved uploads require expert verification.

## Care creation and treatment

POST /expert/monitoring/cases:

```json
{
  "farmer_id": "farmer_uid",
  "baseline_detection_id": "verified_detection_id",
  "tree_id": null,
  "next_follow_up_date": null,
  "follow_up_interval_days": 6
}
```

The baseline must be a verified diseased upload with matching treatment guidance. A null tree_id registers a new tree; an explicit ID must belong to the farmer and respect existing baseline links.

- GET /monitoring/cases and GET /monitoring/cases/{case_id}/overview: farmer list and combined care data.
- GET /expert/monitoring/cases and GET /expert/monitoring/cases/{farmer_id}/{case_id}/overview: expert equivalents.
- GET/POST /monitoring/cases/{case_id}/planned-treatments: planned_date and optional notes.
- GET/POST /monitoring/cases/{case_id}/treatment-applications: treatment_performed, application_date, optional notes/planned_treatment_id.
- PUT /expert/monitoring/cases/{farmer_id}/{case_id}/schedule: next_follow_up_date, optional follow_up_interval_days (1?90).
- PUT /expert/monitoring/cases/{farmer_id}/{case_id}/finish: explicit healthy completion.

## Follow-ups and identity

POST /monitoring/cases/{case_id}/follow-ups accepts multipart file, preliminary_status (default needs_review), optional follow_up_schedule_id and tree_reference_file. Maximum three follow-up uploads per tree ID across cases; exhausted allowance returns HTTP 409. The overview exposes follow_up_upload_allowance with limit, used, and remaining.

PUT /expert/monitoring/cases/{farmer_id}/{case_id}/follow-ups/{follow_up_id}/review accepts confirmed_outcome, required note (1?2000 characters), and optional next_follow_up_date.

PUT /expert/monitoring/cases/{farmer_id}/{case_id}/follow-ups/{follow_up_id}/identity-review accepts:

```json
{
  "status": "confirmed",
  "explanation": "Whole-tree evidence reviewed",
  "expected_revision": 0,
  "baseline_reference_id": null
}
```

Status is confirmed, uncertain, or mismatch. expected_revision prevents concurrent review overwrites. POST /expert/monitoring/cases/{farmer_id}/{case_id}/tree-reference registers baseline whole-tree evidence. Identity review history is available through the corresponding farmer/expert follow-up identity-reviews routes. GET /monitoring/trees lists farmer registrations.

## Protected media

GET /media/detections/{record_id}/{kind} serves farmer evidence; GET /expert/media/detections/{farmer_id}/{record_id}/{kind} serves authorized experts. Monitoring media routes provide baseline and follow-up evidence; tree-reference routes serve identity images. Use API-returned references with the token-aware private media loader. Historical local image routes also require authorization.
