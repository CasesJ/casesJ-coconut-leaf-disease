# CoconutAI Architecture

```text
Browser UI (static/index.html + static/app.js)
  |-- Firebase Authentication
  |-- FastAPI REST API
  |     |-- YOLO26 v6 inference and annotated images
  |     |-- Recommendation service
  |     |-- Expert review, audit log, and notifications
  |     `-- PDF report generation
  |-- SQLite local-first storage and JSON fallback
  |-- Firebase RTDB / Firestore synchronization
  `-- MapLibre GL disease map
```

```text
Farmer upload -> inference -> pending Detection Record
                   |
                   +-> confidence >= 50%: show recommendation below the detection
                   `-> confidence < 50%: no upload-screen recommendation; Needs Review queue

Expert verification or expert recommendation -> verified record + audit event
                                                -> farmer notification
                                                -> verified guidance in Detection Records
```

The UI uses **Total Detections**, **Diseased Trees**, **Healthy Trees**, **Mapped Locations**, **Detection Records**, and **Disease Prevalence** as its standard terms.
