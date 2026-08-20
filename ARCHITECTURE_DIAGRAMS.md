# CoconutAI Architecture

```text
Browser UI (static/index.html + app.js)
  ├─ Firebase Authentication
  ├─ FastAPI REST / WebSocket API
  │    ├─ YOLO11 inference and annotated image output
  │    ├─ Recommendation service
  │    ├─ Expert Review and audit log
  │    └─ Per-user notifications
  ├─ SQLite hybrid storage + JSON backup
  ├─ Firebase RTDB / Firestore synchronization
  └─ MapLibre GL disease map
```

```text
Farmer upload → inference → Pending Detection Record
Expert verification/recommendation → Verified record + audit event
→ per-user notification → farmer views Detection Records and guidance
```

The UI uses Total Detections, Diseased Trees, Healthy Trees, Mapped Locations, Detection Records, and Disease Prevalence as its standard terms.
