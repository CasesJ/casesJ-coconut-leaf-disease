"""
Firebase Sync Manager

Handles syncing locally stored detections to Firebase
with retry logic and exponential backoff.
"""

import logging
import asyncio
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta
import json

logger = logging.getLogger(__name__)

# Try to import Firebase modules
try:
    from firebase_admin import firestore
    from google.cloud.exceptions import GoogleCloudError
    FIREBASE_AVAILABLE = True
except ImportError:
    FIREBASE_AVAILABLE = False
    logger.warning("⚠️  Firebase modules not available")


class FirebaseSync:
    """
    Manages sync operations to Firebase Firestore/Realtime DB
    with automatic retry and backoff logic
    """

    # Configuration
    MAX_RETRIES = 5
    BASE_BACKOFF = 2  # seconds
    MAX_BACKOFF = 300  # 5 minutes

    def __init__(self, firestore_client=None):
        """
        Initialize Firebase sync
        
        Args:
            firestore_client: Optional Firestore client. If None, will attempt to get default.
        """
        self.fs = firestore_client
        self.sync_history = {}  # Track sync attempts

        if self.fs is None and FIREBASE_AVAILABLE:
            try:
                self.fs = firestore.client()
                logger.info("✅ Firebase Firestore client initialized")
            except Exception as e:
                logger.warning(f"⚠️  Could not initialize Firestore: {e}")

    def is_available(self) -> bool:
        """Check if Firebase is available for syncing"""
        return self.fs is not None and FIREBASE_AVAILABLE

    async def sync_detection(
        self,
        detection_data: Dict[str, Any],
        user_id: str,
        collection: str = "detections",
    ) -> tuple[bool, Optional[str]]:
        """
        Sync a single detection to Firebase
        
        Args:
            detection_data: Detection record to sync
            user_id: User ID for Firestore path
            collection: Firestore collection name
            
        Returns:
            (success: bool, error_message: Optional[str])
        """
        if not self.is_available():
            return False, "Firebase not available"

        detection_id = detection_data.get("id", "")
        
        try:
            # Prepare data for Firestore
            data_to_sync = {
                "id": detection_data.get("id"),
                "email": detection_data.get("email"),
                "timestamp": detection_data.get("timestamp"),
                "inference_results": detection_data.get("inference_results"),
                "gps_data": detection_data.get("gps_data"),
                "image_path": detection_data.get("image_path"),
                "synced_at": datetime.utcnow().isoformat(),
                "synced_from": "local_storage",
            }

            # Write to Firestore
            doc_ref = self.fs.collection(collection).document(detection_id)
            doc_ref.set(data_to_sync, merge=True)

            logger.info(f"✅ Detection synced to Firebase: {detection_id}")
            self._record_sync_attempt(detection_id, success=True)

            return True, None

        except Exception as e:
            error_msg = f"Firebase sync error: {str(e)}"
            logger.error(f"❌ {error_msg}")
            self._record_sync_attempt(detection_id, success=False, error=str(e))
            return False, error_msg

    async def batch_sync_detections(
        self,
        detections: List[Dict[str, Any]],
        user_id: str,
        batch_size: int = 100,
    ) -> Dict[str, Any]:
        """
        Sync multiple detections to Firebase in batches
        
        Args:
            detections: List of detection records to sync
            user_id: User ID for Firestore path
            batch_size: Number of records per batch
            
        Returns:
            {
                "total": int,
                "synced": int,
                "failed": int,
                "errors": List[str]
            }
        """
        if not self.is_available():
            return {
                "total": len(detections),
                "synced": 0,
                "failed": len(detections),
                "errors": ["Firebase not available"],
            }

        results = {
            "total": len(detections),
            "synced": 0,
            "failed": 0,
            "errors": [],
        }

        # Process in batches
        for i in range(0, len(detections), batch_size):
            batch = detections[i : i + batch_size]

            tasks = [
                self.sync_detection(detection, user_id)
                for detection in batch
            ]

            batch_results = await asyncio.gather(*tasks, return_exceptions=True)

            for success, error in batch_results:
                if success:
                    results["synced"] += 1
                else:
                    results["failed"] += 1
                    if error:
                        results["errors"].append(error)

            # Small delay between batches to avoid rate limiting
            await asyncio.sleep(0.1)

        return results

    def calculate_backoff(self, retry_count: int) -> int:
        """
        Calculate exponential backoff with jitter
        
        Args:
            retry_count: Current retry attempt (0-based)
            
        Returns:
            Seconds to wait before next retry
        """
        import random

        backoff = min(self.BASE_BACKOFF ** retry_count, self.MAX_BACKOFF)
        jitter = random.uniform(0, backoff * 0.1)
        return int(backoff + jitter)

    def should_retry(self, detection_id: str, attempt_count: int) -> bool:
        """
        Determine if a failed sync should be retried
        
        Args:
            detection_id: ID of the detection
            attempt_count: Number of sync attempts so far
            
        Returns:
            True if should retry, False if max retries reached
        """
        if attempt_count >= self.MAX_RETRIES:
            logger.warning(f"⚠️  Max retries ({self.MAX_RETRIES}) reached for {detection_id}")
            return False
        return True

    def _record_sync_attempt(
        self,
        detection_id: str,
        success: bool,
        error: Optional[str] = None,
    ):
        """Record a sync attempt in history"""
        if detection_id not in self.sync_history:
            self.sync_history[detection_id] = []

        self.sync_history[detection_id].append({
            "timestamp": datetime.utcnow().isoformat(),
            "success": success,
            "error": error,
        })

    async def push_detection_data(
        self,
        user_id: str,
        detection_data: Dict[str, Any],
        retry_count: int = 0,
    ) -> bool:
        """
        Push a single detection with automatic retry
        
        Args:
            user_id: User ID
            detection_data: Detection record
            retry_count: Current retry attempt (internal)
            
        Returns:
            True if successful, False otherwise
        """
        success, error = await self.sync_detection(detection_data, user_id)

        if not success and self.should_retry(detection_data.get("id"), retry_count):
            backoff = self.calculate_backoff(retry_count)
            logger.info(f"🔄 Retrying in {backoff}s (attempt {retry_count + 1}/{self.MAX_RETRIES})")
            await asyncio.sleep(backoff)
            return await self.push_detection_data(user_id, detection_data, retry_count + 1)

        return success

    def get_sync_history(self, detection_id: str) -> List[Dict[str, Any]]:
        """Get sync attempt history for a detection"""
        return self.sync_history.get(detection_id, [])

    def clear_sync_history(self):
        """Clear sync attempt history (for testing or cleanup)"""
        self.sync_history.clear()


# Global instance
_firebase_sync = None


def get_firebase_sync(firestore_client=None) -> FirebaseSync:
    """Get or create the global Firebase sync instance"""
    global _firebase_sync
    if _firebase_sync is None:
        _firebase_sync = FirebaseSync(firestore_client)
    return _firebase_sync
