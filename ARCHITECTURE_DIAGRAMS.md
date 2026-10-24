# CoconutAI Architecture

Updated against the local source on October 24, 2026.

```text
Browser: index.html + app.js + monitoring.js + tree_identity.js
  |-- Firebase Authentication -> bearer ID tokens
  |-- FastAPI: app/main.py -> routers -> domain/services/infrastructure
  |     |-- model.py -> Ultralytics -> weights.pt (YOLO26 v6 label)
  |     |-- Detection persistence, expert review, recommendations
  |     |-- Monitoring, treatment applications, schedules, tree identity
  |     |-- Audit events, notifications, PDF/CSV exports
  |     |-- Firestore: authoritative structured records
  |     `-- Cloudinary: authenticated original image evidence
  |-- private_media.js -> protected API media routes
  |-- detection_overlay.js -> SVG boxes over original images
  `-- MapLibre GL -> embedded-GPS image locations
```

```text
Farmer upload -> validation -> quota reservation -> inference
  |-- failure: error, no successful analysis record
  |-- no qualifying detections: result without a new detection record
  `-- detections >= 5% -> original asset + Firestore pending record
         |-- primary >= 50%: upload-screen guidance
         `-- primary < 50%: hidden guidance; low-confidence review priority

Expert verification / record recommendation
  -> verified record + audit event + farmer notification
  -> eligible diseased baseline -> care record + registered tree
       |-- immutable baseline evidence and recommendation snapshots
       |-- planned treatment -> actual application -> post-treatment schedule
       |-- disease progression schedules
       `-- follow-up uploads (3 per tree ID across cases)
              |-- original close-up + optional whole-tree reference
              |-- separate expert identity and outcome reviews
              `-- explicit expert Finish -> healthy case, history retained
```

Farmer records use compact image-and-details cards. The care-record Inspection timeline spans the full row; the former Recorded treatment plan panel is removed. Tree GPS location replaces the recommendation/application comparison, showing baseline and selected-inspection coordinates or No GPS location available.

The farm map center does not supply missing image GPS. SQLite/JSON detection backups are historical only; Firestore outages do not activate a local detection fallback. See [storage](HYBRID_STORAGE_README.md) for service-specific local state.
