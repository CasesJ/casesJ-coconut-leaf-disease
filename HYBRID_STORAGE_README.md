# Hybrid Storage

CoconutAI is local-first. Detection Records are written to SQLite through `hybrid_storage/`, then optionally synchronized with Firebase services when connectivity and credentials are available.

## Components

- `hybrid_storage/local_storage.py` — SQLite records and local JSON backup support.
- `hybrid_storage/sync_manager.py` — synchronization workflow.
- `hybrid_storage/firebase_sync.py` — Firebase integration.
- `hybrid_storage/connectivity.py` — connection checks.

## Stored data

`hybrid_storage.db` stores Detection Records, including inference data, GPS, verification fields, and recommendation snapshots. Image assets are in `static/uploads/` and `static/annotated_uploads/`.

## Verification data

Experts update `verification_status`, verifier fields, and optional record-level recommendations. These updates are written through every available backend. Farmers receive a persisted in-app verification notification.

Run the normal application with `uvicorn main:app --reload`; no separate hybrid-storage server is required.
