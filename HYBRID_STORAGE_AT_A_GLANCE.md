# Hybrid Storage at a Glance

| Layer | Current role |
|---|---|
| SQLite | Local-first source for Detection Records |
| JSON backup | Recoverable copies in `detection_records/` |
| Firebase RTDB / Firestore | Remote synchronization when configured |
| Static image folders | Original and annotated upload files |

The app continues to save locally when Firebase is unavailable. Expert verification and expert recommendations update every available backend. Per-user notifications use RTDB when available and `user_notifications.json` as a fallback.

Use `GET /health` for service status and `GET /detections/my-records` for the signed-in farmer’s records.
