# Hybrid Storage

CoconutAI is local-first. Detection records are written to SQLite through `hybrid_storage/` and may be synchronized to Firebase when credentials and connectivity are available.

## Components

- `hybrid_storage/local_storage.py` — SQLite record access and local backup support.
- `hybrid_storage/sync_manager.py` — synchronization workflow.
- `hybrid_storage/firebase_sync.py` — Firebase integration.
- `hybrid_storage/connectivity.py` — connectivity checks.

`hybrid_storage.db` holds inference data, GPS details, verification state, and recommendation snapshots. Images are stored in `static/uploads/` and `static/annotated_uploads/`.

Expert actions update verification and recommendation fields across available stores. Run the application normally with `uvicorn main:app --reload`; no separate storage service is required.
