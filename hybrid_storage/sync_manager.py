"""
Sync Manager - Background Synchronization Orchestrator

Coordinates the hybrid storage system:
- Checks internet connectivity
- Syncs pending local records to Firebase when online
- Manages background sync loop
- Provides status monitoring
"""

import asyncio
import logging
from typing import Dict, Any, Optional
from datetime import datetime, timedelta
from enum import Enum

logger = logging.getLogger(__name__)


class SyncStatus(Enum):
    """Sync operation status"""
    IDLE = "idle"
    CHECKING = "checking"
    SYNCING = "syncing"
    WAITING = "waiting"
    ERROR = "error"


class SyncManager:
    """
    Orchestrates background synchronization between local storage and Firebase
    """

    def __init__(
        self,
        local_storage,
        firebase_sync,
        connectivity_checker,
        sync_interval: int = 60,  # seconds
    ):
        """
        Initialize sync manager
        
        Args:
            local_storage: LocalStorageManager instance
            firebase_sync: FirebaseSync instance
            connectivity_checker: Connectivity checker instance/module
            sync_interval: Interval between sync checks (seconds)
        """
        self.local_storage = local_storage
        self.firebase_sync = firebase_sync
        self.connectivity_checker = connectivity_checker
        self.sync_interval = sync_interval

        # Sync state
        self.status = SyncStatus.IDLE
        self.last_sync_time: Optional[datetime] = None
        self.last_connectivity_check: Optional[datetime] = None
        self.is_internet_available = False
        self.is_running = False
        self.sync_task: Optional[asyncio.Task] = None

        # Statistics
        self.stats = {
            "total_syncs": 0,
            "successful_syncs": 0,
            "failed_syncs": 0,
            "total_records_synced": 0,
            "last_sync_time": None,
            "last_error": None,
        }

    async def start(self):
        """Start the background sync loop"""
        if self.is_running:
            logger.warning("⚠️  Sync manager already running")
            return

        self.is_running = True
        self.sync_task = asyncio.create_task(self._sync_loop())
        logger.info("✅ Sync manager started")

    async def stop(self):
        """Stop the background sync loop"""
        self.is_running = False
        if self.sync_task:
            self.sync_task.cancel()
            try:
                await self.sync_task
            except asyncio.CancelledError:
                pass
        logger.info("✅ Sync manager stopped")

    async def _sync_loop(self):
        """Main background sync loop"""
        while self.is_running:
            try:
                await self._sync_iteration()
                await asyncio.sleep(self.sync_interval)
            except Exception as e:
                logger.error(f"❌ Error in sync loop: {e}")
                self.status = SyncStatus.ERROR
                self.stats["last_error"] = str(e)
                await asyncio.sleep(self.sync_interval)

    async def _sync_iteration(self):
        """Single sync iteration"""
        self.status = SyncStatus.CHECKING

        # Check internet connectivity
        try:
            connectivity_result = await self.connectivity_checker.check_internet_connectivity()
            self.is_internet_available = connectivity_result.get("is_connected", False)
            self.last_connectivity_check = datetime.utcnow()
        except Exception as e:
            logger.warning(f"⚠️  Connectivity check failed: {e}")
            self.is_internet_available = False

        logger.debug(f"Internet available: {self.is_internet_available}")

        # If no internet, just wait
        if not self.is_internet_available:
            self.status = SyncStatus.WAITING
            return

        # Get pending syncs
        self.status = SyncStatus.SYNCING
        pending_records = self.local_storage.get_pending_syncs()

        if not pending_records:
            self.status = SyncStatus.IDLE
            return

        logger.info(f"🔄 Starting sync of {len(pending_records)} pending records")

        # Sync records
        synced_count = 0
        failed_count = 0

        for record in pending_records:
            try:
                # Check if we should retry based on attempts
                if not self.firebase_sync.should_retry(
                    record["id"], record.get("sync_attempts", 0)
                ):
                    logger.warning(f"⚠️  Skipping {record['id']} - max retries exceeded")
                    self.local_storage.mark_synced(
                        record["id"],
                        error_message="Max retries exceeded"
                    )
                    failed_count += 1
                    continue

                # Attempt sync
                success, error = await self.firebase_sync.sync_detection(
                    record,
                    record["user_id"]
                )

                if success:
                    self.local_storage.mark_synced(record["id"])
                    synced_count += 1
                    self.stats["total_records_synced"] += 1
                    logger.info(f"✅ Synced: {record['id']}")
                else:
                    self.local_storage.increment_sync_attempts(
                        record["id"],
                        error_message=error or "Unknown error"
                    )
                    failed_count += 1
                    logger.warning(f"❌ Sync failed: {record['id']} - {error}")

            except Exception as e:
                logger.error(f"❌ Exception during sync of {record['id']}: {e}")
                self.local_storage.increment_sync_attempts(
                    record["id"],
                    error_message=str(e)
                )
                failed_count += 1

        # Update stats
        self.status = SyncStatus.IDLE
        self.last_sync_time = datetime.utcnow()
        self.stats["total_syncs"] += 1
        self.stats["successful_syncs"] += synced_count
        self.stats["failed_syncs"] += failed_count
        self.stats["last_sync_time"] = self.last_sync_time.isoformat()

        logger.info(
            f"✅ Sync complete: {synced_count} synced, {failed_count} failed"
        )

    async def force_sync(self) -> Dict[str, Any]:
        """
        Force an immediate sync operation
        
        Returns:
            Sync result summary
        """
        logger.info("🔄 Force sync initiated")
        await self._sync_iteration()

        return {
            "status": self.status.value,
            "is_internet_available": self.is_internet_available,
            "last_sync_time": self.last_sync_time.isoformat() if self.last_sync_time else None,
            "stats": self.stats.copy(),
        }

    def get_status(self) -> Dict[str, Any]:
        """
        Get current sync manager status
        
        Returns:
            Status dictionary
        """
        pending_records = self.local_storage.get_pending_syncs(limit=1)

        return {
            "is_running": self.is_running,
            "status": self.status.value,
            "is_internet_available": self.is_internet_available,
            "last_connectivity_check": self.last_connectivity_check.isoformat() if self.last_connectivity_check else None,
            "last_sync_time": self.last_sync_time.isoformat() if self.last_sync_time else None,
            "sync_interval": self.sync_interval,
            "stats": self.stats.copy(),
            "storage_stats": self.local_storage.get_storage_stats(),
        }

    def update_sync_interval(self, interval: int):
        """
        Update the sync interval
        
        Args:
            interval: New interval in seconds
        """
        if interval < 10:
            logger.warning("⚠️  Interval too low, setting to 10 seconds")
            interval = 10
        self.sync_interval = interval
        logger.info(f"✅ Sync interval updated to {interval}s")


# Global instance
_sync_manager = None


async def get_sync_manager(
    local_storage=None,
    firebase_sync=None,
    connectivity_checker=None,
    sync_interval: int = 60,
) -> SyncManager:
    """Get or create the global sync manager instance"""
    global _sync_manager

    if _sync_manager is None:
        # Import here to avoid circular imports
        from .local_storage import get_local_storage
        from .firebase_sync import get_firebase_sync
        from .connectivity import ConnectivityChecker

        local_storage = local_storage or get_local_storage()
        firebase_sync = firebase_sync or get_firebase_sync()
        connectivity_checker = connectivity_checker or ConnectivityChecker

        _sync_manager = SyncManager(
            local_storage,
            firebase_sync,
            connectivity_checker,
            sync_interval,
        )

    return _sync_manager
