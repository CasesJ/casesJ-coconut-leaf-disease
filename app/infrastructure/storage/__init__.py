"""
app/infrastructure/storage/__init__.py — Hybrid storage adapter.

Re-exports the public interface from the existing hybrid_storage package
so the rest of the application imports from a single stable location.
"""
from hybrid_storage.local_storage import DetectionRecord, get_local_storage
from hybrid_storage.sync_manager import SyncManager, SyncStatus
from hybrid_storage.firebase_sync import FirebaseSync

__all__ = [
    "DetectionRecord",
    "get_local_storage",
    "SyncManager",
    "SyncStatus",
    "FirebaseSync",
]
