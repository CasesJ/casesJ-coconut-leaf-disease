# Retired Hybrid Storage

SQLite and JSON detection records are retained as historical backups; they are not migrated or deleted by the Firestore cutover. The SQLite adapter package, sync manager, and migration utility have been retired from the codebase. The FastAPI runtime does not open or create SQLite databases.

New detections are stored at users/{uid}/detections/{record_id}. Expert queues, farmer history, verification, recommendations, monitoring baselines, maps, and reports read canonical Firestore records. New evidence images use authenticated Cloudinary assets. Historical local images and Firebase Storage objects remain available through authenticated compatibility routes when a Firestore record references them.

Firestore and Cloudinary must be configured for record and image persistence. If either is unavailable, the affected write fails instead of saving a local-only record.
