import json
import os
from datetime import datetime
from pathlib import Path

RECORDS_DIR = Path("detection_records")
RECORDS_DIR.mkdir(exist_ok=True)

def save_detection(user_id: str, email: str, detections: list) -> bool:
    """Save detection record to local JSON file (persistent storage)"""
    try:
        user_dir = RECORDS_DIR / user_id
        user_dir.mkdir(exist_ok=True)
        
        # Create timestamped filename
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = user_dir / f"detection_{timestamp}.json"
        
        record = {
            'timestamp': datetime.now().isoformat(),
            'email': email,
            'detections': detections,
            'count': len(detections),
            'source': 'upload'
        }
        
        with open(filename, 'w') as f:
            json.dump(record, f, indent=2)
        
        print(f"✅ Record saved to: {filename}")
        return True
    except Exception as e:
        print(f"❌ Failed to save record: {e}")
        return False

def get_user_detections(user_id: str) -> list:
    """Get all detection records for a user (persistent storage)"""
    try:
        user_dir = RECORDS_DIR / user_id
        
        if not user_dir.exists():
            print(f"ℹ️  No records for user {user_id}")
            return []
        
        records = []
        for filename in sorted(user_dir.glob("*.json"), reverse=True):
            try:
                with open(filename, 'r') as f:
                    record = json.load(f)
                    record['id'] = filename.stem
                    record['type'] = 'upload'
                    records.append(record)
            except Exception as e:
                print(f"⚠️  Failed to read {filename}: {e}")
        
        print(f"✅ Retrieved {len(records)} records from local storage")
        return records
    except Exception as e:
        print(f"❌ Failed to read records: {e}")
        return []