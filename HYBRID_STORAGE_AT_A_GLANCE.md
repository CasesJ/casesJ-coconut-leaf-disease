# Storage at a Glance

Updated against the local source on October 24, 2026. The historical filename is retained for links.

| Layer | Current role |
|---|---|
| Firestore | Authoritative detections, registered trees, care records, treatment history, schedules, reviews, follow-up allowances, notifications, and expert audit events |
| Cloudinary | Authenticated original disease and whole-tree reference images |
| Browser SVG overlay | Detection boxes from stored dimensions and normalized bounding boxes |
| Firebase RTDB | Notification compatibility fallback |
| Local notification/configuration/audit files | Service-specific state, not a detection database fallback |
| Historical SQLite/JSON detections | Retained backups, not application record storage |
| Historical local/Firebase Storage images | Protected compatibility reads when referenced by an authorized record |

Firestore failures fail record operations. Cloudinary failures fail affected image writes. New annotated copies are not persisted. See [storage details](HYBRID_STORAGE_README.md), [fields](DATA_DICTIONARY.md), and the backend /health endpoint.
