# Hybrid Storage at a Glance

| Layer | Current role |
|---|---|
| Firestore | Authoritative detection records and expert queue |
| Cloudinary | Authenticated storage for new image evidence |
| Firebase RTDB | Notifications and legacy data only |
| Local SQLite and JSON | Retained historical backups; not used by the application runtime |

The app fails record reads and writes when Firestore is unavailable; it does not save detections to SQLite or JSON. Expert verification and recommendations update the canonical Firestore record. Per-user notifications remain a separate service.

Use `GET /health` for service status and `GET /detections/my-records` for the signed-in farmer’s records.
