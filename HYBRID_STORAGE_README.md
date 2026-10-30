# Firestore and Private Evidence Storage

Updated against the local source on October 24, 2026. This document retains its historical filename.

## Structured records

Firestore users/{uid}/detections/{record_id} is authoritative. Farmer history, expert queues, verification, maps, reports, and monitoring baselines resolve canonical records. Runtime detection reads and writes do not use SQLite or historical JSON backups.

User collections include trees, monitoring_cases, treatment_records, and follow_up_upload_limits. Case subcollections hold planned_treatments, treatment_applications, follow_up_schedules, and follow_ups. Review and completion events preserve history. Global tree_id_reservations prevents generated tree ID collisions.

Care creation atomically binds the baseline to a registered tree and creates care records. Baseline image, prediction, GPS, and recommendation-provenance snapshots preserve evidence against later detection changes. Follow-up allowances enforce three uploads per farmer-owned tree ID across cases.

## Image evidence

New uploads persist authenticated Cloudinary evidence plus a compact private thumbnail for record lists. Stored image_dimensions and normalized boxes (annotation_format: xyxy-normalized-v1) support SVG annotation in static/detection_overlay.js. Older records generate and cache a thumbnail on first preview. Follow-up comparisons display original images without overlays. Whole-tree photos are separate identity evidence.

static/private_media.js loads protected API images using the signed-in token. Farmer and expert routes check record/case access. Historical local images and Firebase Storage objects are compatibility sources when authorized records reference them. Public static hosting excludes historical upload folders.

Failures return errors rather than claiming local-only persistence succeeded. Upload flows attempt exact-asset cleanup on failed persistence, with reconciliation where implemented.

## Separate local state

expert_recommendations.json holds recommendation configuration; expert audit events prefer the Firestore expert_audit_events collection with expert_audit_log.jsonl fallback. Notifications prefer users/{uid}/notifications in Firestore, then Firebase RTDB and user_notifications.json fallback. These are not detection backups and need a durability strategy on ephemeral or multi-instance hosting.

Historical SQLite/JSON backups and image folders are not automatically migrated or deleted. Removing the Recorded treatment plan UI panel does not remove stored treatment records.

See [data dictionary](DATA_DICTIONARY.md) and [deployment](FIREBASE_DEPLOY.md).
