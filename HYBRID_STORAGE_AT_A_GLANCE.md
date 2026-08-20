# Hybrid Storage at a Glance

| Layer | Current role |
|---|---|
| SQLite | Reliable local Detection Record source of truth |
| JSON backup | Recoverable local record copies in `detection_records/` |
| Firebase RTDB / Firestore | Remote record synchronization when configured |
| Static image folders | Original and annotated image files |

The system continues to save locally when remote Firebase operations are unavailable. Expert verification updates record status across available stores. User notifications are stored per user in RTDB with a local JSON fallback.

Use `GET /health` to check the application and `GET /records?user_id=<uid>` to inspect a farmer's records.
