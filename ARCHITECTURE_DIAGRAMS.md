# CoconutAI Architecture

```text
Browser UI (static/index.html + static/app.js)
  |-- Firebase Authentication
  |-- FastAPI REST API
  |     |-- YOLO26 v6 inference and annotated images
  |     |-- Recommendation service
  |     |-- Expert review, audit log, and notifications
  |     `-- PDF report generation
  |-- Firestore authoritative records (users/{uid}/detections/{record_id})
  |-- Cloudinary authenticated image evidence
  `-- MapLibre GL disease map
```

```text
Farmer upload -> inference -> Detection Record
                   |
                   +-> confidence > 50%: show recommendation below the detection
                   `-> confidence <= 50%: no upload-screen recommendation; Needs Review queue

Expert verification or expert recommendation -> verified record + audit event
                                                -> farmer notification
                                                -> verified guidance in Detection Records
```

The UI uses **Total Detections**, **Diseased Trees**, **Healthy Trees**, **Mapped Locations**, **Detection Records**, and **Disease Prevalence** as its standard terms.
