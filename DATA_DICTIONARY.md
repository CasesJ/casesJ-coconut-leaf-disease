# CoconutAI Data Dictionary

Updated against the local source on October 24, 2026. Request models are defined in app/schemas; storage behavior is implemented in app/services.

## Detection record

| Field | Meaning |
|---|---|
| id, user_id, email, timestamp | Identifier, owner, and upload time |
| source, filename, tree_id | Origin, uploaded filename, and registered tree link when assigned |
| detections | Class, confidence (0?1), bbox, and bbox_normalized where available |
| image_dimensions | Original width and height in pixels |
| annotation_format | xyxy-normalized-v1 for current metadata-based annotations |
| image_assets | Durable authenticated Cloudinary asset references |
| image_url, annotated_image_url | API-presented media references; historical records can reference separate annotated assets |
| image_metadata, gps_data, gps_source, lat, lng | Image metadata and location, including legacy shapes |
| verification_status | pending_verification or verified |
| verified_by, verified_by_uid, verified_at | Verification provenance |
| recommendation_snapshot, recommendation_source | Saved guidance and origin, including expert_override or farmer_review |
| farmer_recommendation_confirmed_at | Low-confidence farmer API submission timestamp, when used |

Current uploads store original evidence, not a new annotated image copy. API media presentation and browser SVG overlays provide previews. Missing embedded GPS remains unknown and is not replaced by browser/form/default coordinates.

## Confidence and review

Display and recording thresholds are both 5%. The primary detection controls upload-screen guidance: at least 50% shows it; below 50% hides it. All saved uploads begin pending. Expert low-confidence prioritization uses below 50%. UI severity labels derived from confidence are presentation categories, not a clinical severity or recovery measurement.

## Registered trees and care cases

Firestore users/{uid}/trees/{tree_id} stores farmer-owned tree registration. New generated IDs use TREE- followed by ten uppercase hexadecimal characters; global tree_id_reservations reserves generated IDs. Existing linked identities must be preserved.

A monitoring case includes id, farmer_id, tree_id, baseline_detection_id, disease, treatment_record_id, created_at, and created_by_expert_uid. baseline_image_reference, baseline_prediction_snapshot, baseline_gps_snapshot, and baseline_recommendation_provenance preserve baseline evidence.

| Case field | Values / purpose |
|---|---|
| treatment_status | awaiting_treatment, treatment_scheduled, treatment_applied |
| monitoring_status | waiting or finish |
| health_status, finished_at, finished_by_expert_uid | Explicit expert completion details |
| next_follow_up_date, follow_up_interval_days | Progression inspection schedule; interval defaults to 6 days |
| next_post_treatment_follow_up_date | Next post-treatment inspection |
| planned_treatment_id, planned_treatment_date | Selected treatment plan |
| latest_treatment_application_id, latest_treatment_application_date | Most recent actual application |

Treatment snapshots remain stored even though the separate Recorded treatment plan panel is removed.

## Treatment and inspections

- treatment_records: baseline recommendation_snapshot, provenance, recording time, and expert.
- planned_treatments: planned_date, optional notes, status scheduled/applied/superseded, and creation provenance.
- treatment_applications: treatment_performed, application_date, optional notes/planned_treatment_id, first_follow_up_due_date, interval, and recording farmer. These are historical application logs.
- follow_up_schedules: disease_progression or post_treatment; due_date, interval, linked application, creator, and status scheduled/inspection_recorded/superseded.
- follow_ups: image references, dimensions, predictions, optional GPS/tree_reference, preliminary_status, review_status, and linked treatment/schedule.
- preliminary_status: needs_review, improving, no_visible_change, or worsening.
- Outcome review: confirmed_outcome improving/no_visible_change/worsening, expert note, and expert provenance. Farmer observations remain preliminary until review.
- identity_review: confirmed, uncertain, or mismatch, with revision and review provenance. Identity and treatment-outcome reviews are separate.
- follow_up_upload_limits/{tree_id}: transactional used count; maximum three follow-up uploads per tree ID across cases, including historical usage.

Whole-tree references support visual identity comparison. GPS comparison can report gps_unavailable, within_tolerance, outside_tolerance, or inconclusive_accuracy; it does not itself confirm identity. Identity review events and outcome review_events retain history; completion_events records explicit expert completion.

## Notifications and storage

Notifications include id, record_id, title, message, type, read, and created_at. They prefer Firestore users/{uid}/notifications, then Firebase RTDB and user_notifications.json fallback. Expert audit/configuration files have separate storage behavior.

See [storage](HYBRID_STORAGE_README.md) for Firestore paths and historical compatibility sources.
