"""
Local Storage Manager - SQLite + JSON Persistence

Handles offline storage of detection results with support for
both SQLite database and JSON file backups.
"""

import sqlite3
import json
import logging
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Optional
from dataclasses import asdict, dataclass
import threading

logger = logging.getLogger(__name__)


@dataclass
class DetectionRecord:
    """Schema for a detection record"""
    id: Optional[str] = None
    user_id: str = ""
    email: str = ""
    timestamp: str = ""
    inference_results: dict = None
    gps_data: dict = None
    image_path: str = ""
    is_synced: bool = False
    sync_attempts: int = 0
    error_message: str = ""
    
    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        if self.inference_results:
            data["inference_results"] = json.dumps(self.inference_results)
        if self.gps_data:
            data["gps_data"] = json.dumps(self.gps_data)
        return data


class LocalStorageManager:
    """
    Manages local storage with SQLite database and JSON backup.
    Thread-safe for concurrent access.
    """

    def __init__(
        self,
        db_path: str = "hybrid_storage.db",
        json_backup_dir: str = "detection_records",
        enable_json_backup: bool = True,
    ):
        """
        Initialize local storage manager
        
        Args:
            db_path: Path to SQLite database file
            json_backup_dir: Directory for JSON file backups
            enable_json_backup: Whether to save JSON backups alongside SQLite
        """
        self.db_path = Path(db_path)
        self.json_backup_dir = Path(json_backup_dir)
        self.enable_json_backup = enable_json_backup
        self._lock = threading.RLock()

        # Create directories if they don't exist
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        if self.enable_json_backup:
            self.json_backup_dir.mkdir(parents=True, exist_ok=True)

        # Initialize database
        self._init_db()

    def _init_db(self):
        """Initialize SQLite database schema"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS detections (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    email TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    inference_results TEXT,
                    gps_data TEXT,
                    image_path TEXT,
                    is_synced BOOLEAN DEFAULT 0,
                    sync_attempts INTEGER DEFAULT 0,
                    error_message TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_user_id 
                ON detections(user_id)
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_is_synced 
                ON detections(is_synced)
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_timestamp 
                ON detections(timestamp)
                """
            )
            conn.commit()
            logger.info("✅ Database initialized")

    def _get_connection(self) -> sqlite3.Connection:
        """Get thread-local database connection"""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def save_detection(
        self,
        detection_record: DetectionRecord,
    ) -> str:
        """
        Save detection record to local storage
        
        Args:
            detection_record: DetectionRecord instance
            
        Returns:
            Detection ID
        """
        if not detection_record.id:
            detection_record.id = f"{detection_record.user_id}_{int(datetime.utcnow().timestamp() * 1000)}"

        if not detection_record.timestamp:
            detection_record.timestamp = datetime.utcnow().isoformat()

        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                try:
                    cursor.execute(
                        """
                        INSERT OR REPLACE INTO detections 
                        (id, user_id, email, timestamp, inference_results, gps_data, 
                         image_path, is_synced, sync_attempts, error_message)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            detection_record.id,
                            detection_record.user_id,
                            detection_record.email,
                            detection_record.timestamp,
                            json.dumps(detection_record.inference_results) if detection_record.inference_results else None,
                            json.dumps(detection_record.gps_data) if detection_record.gps_data else None,
                            detection_record.image_path,
                            detection_record.is_synced,
                            detection_record.sync_attempts,
                            detection_record.error_message,
                        ),
                    )
                    conn.commit()
                    logger.info(f"✅ Detection saved: {detection_record.id}")

                    # Save JSON backup if enabled
                    if self.enable_json_backup:
                        self._save_json_backup(detection_record)

                    return detection_record.id

                except Exception as e:
                    logger.error(f"❌ Error saving detection: {e}")
                    raise

    def _save_json_backup(self, record: DetectionRecord):
        """Save JSON backup of detection"""
        try:
            user_dir = self.json_backup_dir / record.user_id
            user_dir.mkdir(parents=True, exist_ok=True)

            filename = user_dir / f"detection_{record.timestamp.replace(':', '-')}.json"
            
            record_data = {
                "id": record.id,
                "user_id": record.user_id,
                "email": record.email,
                "timestamp": record.timestamp,
                "inference_results": record.inference_results,
                "gps_data": record.gps_data,
                "image_path": record.image_path,
                "is_synced": record.is_synced,
                "sync_attempts": record.sync_attempts,
            }
            
            with open(filename, "w") as f:
                json.dump(record_data, f, indent=2)
            
            logger.debug(f"💾 JSON backup saved: {filename}")
        except Exception as e:
            logger.warning(f"⚠️  Failed to save JSON backup: {e}")

    def get_pending_syncs(self, limit: int = 100) -> List[Dict[str, Any]]:
        """
        Get all records that haven't been synced yet
        
        Args:
            limit: Maximum number of records to return
            
        Returns:
            List of unsync'd detection records
        """
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT * FROM detections 
                    WHERE is_synced = 0 
                    ORDER BY created_at ASC 
                    LIMIT ?
                    """,
                    (limit,),
                )
                rows = cursor.fetchall()
                return [self._row_to_dict(row) for row in rows]

    def mark_synced(self, detection_id: str, error_message: str = ""):
        """
        Mark a detection as successfully synced to Firebase
        
        Args:
            detection_id: ID of the detection to mark
            error_message: Optional error message if sync failed
        """
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    UPDATE detections 
                    SET is_synced = 1, 
                        error_message = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (error_message, detection_id),
                )
                conn.commit()
                logger.info(f"✅ Detection marked as synced: {detection_id}")

    def increment_sync_attempts(self, detection_id: str, error_message: str = ""):
        """
        Increment sync attempt counter
        
        Args:
            detection_id: ID of the detection
            error_message: Optional error message from sync attempt
        """
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    UPDATE detections 
                    SET sync_attempts = sync_attempts + 1,
                        error_message = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (error_message, detection_id),
                )
                conn.commit()
                logger.debug(f"Sync attempts incremented: {detection_id}")

    def get_detection(self, detection_id: str) -> Optional[Dict[str, Any]]:
        """Get a single detection by ID"""
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM detections WHERE id = ?", (detection_id,))
                row = cursor.fetchone()
                return self._row_to_dict(row) if row else None

    def get_user_detections(
        self, user_id: str, synced_only: bool = False, limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Get all detections for a user"""
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                if synced_only:
                    cursor.execute(
                        """
                        SELECT * FROM detections 
                        WHERE user_id = ? AND is_synced = 1
                        ORDER BY timestamp DESC 
                        LIMIT ?
                        """,
                        (user_id, limit),
                    )
                else:
                    cursor.execute(
                        """
                        SELECT * FROM detections 
                        WHERE user_id = ?
                        ORDER BY timestamp DESC 
                        LIMIT ?
                        """,
                        (user_id, limit),
                    )
                rows = cursor.fetchall()
                return [self._row_to_dict(row) for row in rows]

    def get_storage_stats(self) -> Dict[str, Any]:
        """Get statistics about local storage"""
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                # Total records
                cursor.execute("SELECT COUNT(*) as count FROM detections")
                total = cursor.fetchone()["count"]

                # Synced records
                cursor.execute("SELECT COUNT(*) as count FROM detections WHERE is_synced = 1")
                synced = cursor.fetchone()["count"]

                # Pending records
                cursor.execute("SELECT COUNT(*) as count FROM detections WHERE is_synced = 0")
                pending = cursor.fetchone()["count"]

                # Failed records (5+ sync attempts)
                cursor.execute("SELECT COUNT(*) as count FROM detections WHERE sync_attempts >= 5")
                failed = cursor.fetchone()["count"]

                # Database size
                db_size = self.db_path.stat().st_size if self.db_path.exists() else 0

                return {
                    "total_records": total,
                    "synced_records": synced,
                    "pending_records": pending,
                    "failed_records": failed,
                    "database_size_bytes": db_size,
                    "database_path": str(self.db_path),
                }

    def _row_to_dict(self, row) -> Dict[str, Any]:
        """Convert SQLite row to dictionary"""
        if not row:
            return None
        
        data = dict(row)
        # Parse JSON fields
        if data.get("inference_results"):
            try:
                data["inference_results"] = json.loads(data["inference_results"])
            except:
                pass
        if data.get("gps_data"):
            try:
                data["gps_data"] = json.loads(data["gps_data"])
            except:
                pass
        return data

    def clear_old_synced_records(self, days: int = 30):
        """
        Delete old synced records to free up space
        
        Args:
            days: Age in days of records to delete
        """
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    DELETE FROM detections 
                    WHERE is_synced = 1 
                    AND datetime(created_at) < datetime('now', '-' || ? || ' days')
                    """,
                    (days,),
                )
                conn.commit()
                logger.info(f"🗑️  Deleted {cursor.rowcount} old synced records")


# Global instance
_local_storage = None


def get_local_storage(
    db_path: str = "hybrid_storage.db",
    json_backup_dir: str = "detection_records",
) -> LocalStorageManager:
    """Get or create the global local storage instance"""
    global _local_storage
    if _local_storage is None:
        _local_storage = LocalStorageManager(db_path, json_backup_dir)
    return _local_storage
