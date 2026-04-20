# Hybrid Storage System - Architecture & Verification Checklist

## 🏗️ System Architecture

### High-Level Architecture Diagram

```
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃                      Farmer's Device (Mobile/Browser)             ┃
┃                    Takes Coconut Leaf Photos                      ┃
┗━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛
                          │
                          │ HTTP/HTTPS
                          ▼
        ┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
        ┃    FastAPI Backend Server        ┃
        ┃   (main_hybrid.py)               ┃
        ┃                                  ┃
        ┃  Routes:                         ┃
        ┃  • /detect/image                 ┃
        ┃  • /storage/*                    ┃
        ┃  • /auth/*                       ┃
        ┗━━━━━┳━━━━━━━━━━┳━━━━━━━━━━┳━━━━┛
              │          │          │
        ┌─────▼──┐  ┌───▼────┐  ┌─▼──────────┐
        │OpenVINO│  │ Hybrid │  │ Connectivity
        │ Model  │  │Storage │  │ Checker
        │(.xml)  │  │Manager │  │ (DNS/HTTP)
        └────────┘  └──┬───┬─┘  └────────────┘
                       │   │
            ┌──────────┘   └──────────┐
            │                         │
       ┌────▼─────┐           ┌──────▼──────┐
       │  SQLite   │           │  Firebase   │
       │ Database  │           │   Firestore │
       │ (primary) │           │  (when ☁️) │
       └──────┬────┘           └─────────────┘
              │
              │
       ┌──────▼──────────────┐
       │  JSON Backups       │
       │  detection_records/ │
       │  (human-readable)   │
       └─────────────────────┘
```

### Detailed Data Flow Diagram

```
FARMER UPLOAD
     │
     ├─→ [Check Authentication]
     │    └─→ Firebase Token Valid? → Continue : Reject
     │
     ├─→ [OpenVINO Inference]
     │    └─→ Runs Locally (Always ✅)
     │        Outputs: Detections with confidence scores
     │
     ├─→ [Filter High-Confidence]
     │    └─→ Keep only >= 50% confidence
     │
     ├─→ [Create Detection Record]
     │    └─→ {user_id, email, timestamp, results, gps}
     │
     ├─→ [Save to SQLite]
     │    ├─→ Insert into detections table
     │    ├─→ Mark: is_synced = false
     │    └─→ Return: detection_id ✅
     │
     ├─→ [Save JSON Backup]
     │    └─→ detection_records/{user_id}/detection_*.json
     │
     ├─→ [Check Internet]
     │    └─→ DNS connectivity test (3s timeout)
     │
     └─→ [Decision Point]
          │
          ├─ ONLINE ✅
          │  └─→ [Firebase Sync NOW]
          │     ├─→ Success? Mark synced ✅
          │     └─→ Fail? Keep as pending 🔄
          │
          └─ OFFLINE 🔴
             └─→ [Mark as Pending]
                ├─→ Will sync in background
                └─→ No data loss ✅
```

### Background Sync Loop

```
SYNC MANAGER BACKGROUND TASK
Every 60 seconds:
│
├─→ [Check Internet Connectivity]
│   └─→ DNS test to multiple servers
│       └─→ Is Connected?
│           │
│           ├─ NO → Sleep 60s, retry later
│           │
│           └─ YES → Continue
│
├─→ [Get Pending Records from SQLite]
│   └─→ SELECT * FROM detections WHERE is_synced = 0
│       └─→ Limit to 100 records per batch
│
├─→ [For Each Pending Record]
│   │
│   ├─→ [Check Retry Attempts]
│   │   └─→ Attempts < 5?
│   │       ├─ NO → Mark failed ❌
│   │       └─ YES → Continue
│   │
│   ├─→ [Attempt Firebase Sync]
│   │   └─→ POST to Firestore
│   │       ├─ Success? 
│   │       │  ├─→ Mark: is_synced = 1 ✅
│   │       │  └─→ Update sync time
│   │       │
│   │       └─ Fail?
│   │          ├─→ Increment: sync_attempts += 1
│   │          ├─→ Store: error_message
│   │          └─→ Calculate backoff
│   │
│   └─→ [Retry Logic]
│       └─→ If failed:
│           ├─→ Wait: 2^attempts seconds
│           ├─→ Add jitter: ±10%
│           ├─→ Max wait: 5 minutes
│           └─→ Try again on next cycle
│
├─→ [Update Statistics]
│   ├─→ total_syncs += 1
│   ├─→ successful_syncs += count
│   ├─→ failed_syncs += count
│   └─→ total_records_synced += count
│
└─→ [Sleep 60 seconds, repeat...]
```

---

## 📊 Component Interaction Diagram

```
┌──────────────────────────────────────────────────────────────────┐
│                     FastAPI Application                           │
├──────────────────────────────────────────────────────────────────┤
│                                                                   │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │              Detection Endpoint (POST /detect/image)     │    │
│  │                                                         │    │
│  │  1. Verify Firebase Token ──→ [firebase_config]        │    │
│  │  2. Read Image ──────────────→ [opencv]                │    │
│  │  3. Run Inference ───────────→ [model.py / OpenVINO]   │    │
│  │  4. Save Locally ────────────→ [LocalStorageManager]   │    │
│  │     ├─ SQLite Database                                  │    │
│  │     ├─ JSON Backup                                      │    │
│  │     └─ Return detection_id                              │    │
│  │  5. Check Internet ──────────→ [ConnectivityChecker]   │    │
│  │  6. If Online: Sync ─────────→ [FirebaseSync]          │    │
│  │                                                         │    │
│  └─────────────────────────────────────────────────────────┘    │
│                                                                   │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │         Background Sync Task (SyncManager)              │    │
│  │                                                         │    │
│  │  Runs every 60 seconds:                                │    │
│  │  • Check connectivity [ConnectivityChecker]            │    │
│  │  • Get pending records [LocalStorageManager]           │    │
│  │  • Sync each record [FirebaseSync]                     │    │
│  │  • Update status [statistics]                          │    │
│  │                                                         │    │
│  └─────────────────────────────────────────────────────────┘    │
│                                                                   │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │          Status Endpoints (GET /storage/*)              │    │
│  │                                                         │    │
│  │  • /connectivity ─→ [ConnectivityChecker]              │    │
│  │  • /sync-status ──→ [SyncManager.get_status()]         │    │
│  │  • /pending ──────→ [LocalStorageManager.get_pending]  │    │
│  │  • /stats ────────→ [LocalStorageManager.get_stats()]  │    │
│  │  • /health ───────→ [All components check]             │    │
│  │                                                         │    │
│  └─────────────────────────────────────────────────────────┘    │
│                                                                   │
└──────────────────────────────────────────────────────────────────┘

         │              │              │
         ▼              ▼              ▼
    ┌────────┐    ┌──────────┐    ┌────────┐
    │ SQLite │    │ Firebase │    │  JSON  │
    │   DB   │    │Firestore │    │Backups │
    └────────┘    └──────────┘    └────────┘
```

---

## 🔄 State Machine: Record Lifecycle

```
                         START
                           │
                           ▼
              ┌──────────────────────┐
              │ NEW DETECTION RECORD │
              └────────┬─────────────┘
                       │
                       │ Save to SQLite
                       ▼
              ┌──────────────────────┐
              │  PENDING_SYNC        │◄──── Waiting for internet
              │  (in SQLite)         │      or background sync
              └────────┬─────────────┘
                       │
                ┌──────┴──────┐
                │             │
         Is Online?     Start Sync Task
                │             │
         ┌──────▼──┐     ┌────▼──────┐
         │  YES    │     │ SYNCING   │
         └────┬────┘     │ (Attempt  │
              │          │  to push  │
         Firebase        │  to FB)   │
          Sync Now       └────┬──────┘
              │                │
              │          ┌─────┴─────┐
              │          │           │
          ┌───▼─────┐  Success     Failure
          │SYNCED ✅ │    │           │
          │(Complete)│    │      ┌────▼──────┐
          └──────────┘    │      │Retry <= 5?│
                          │      └────┬───┬──┘
                          │           │   │
                          │        YES│   │NO
                          │           │   │
                          │           │   └──→ ┌──────────┐
                          │      ┌────▼──┐    │ FAILED ❌ │
                          │      │PENDING│    │(Max      │
                          │      │(wait) │    │ retries) │
                          │      └───┬───┘    └──────────┘
                          │          │
                          │      (retry after
                          │       backoff delay)
                          │
                          ▼
                    ┌──────────────┐
                    │   DELETED    │
                    │ (or archived)│
                    └──────────────┘
```

---

## 📈 Timing Diagram: Offline-to-Online Transition

```
TIME │ Event                   │ SQLite │ Firebase │ Status
─────┼────────────────────────┼────────┼──────────┼─────────────
0s   │ Detection uploaded     │        │          │
     │ Image processed        │        │          │
     │ OpenVINO inference     │        │          │
     │ High-confidence found  │        │          │
─────┼────────────────────────┼────────┼──────────┼─────────────
10ms │ Saved to SQLite        │ ✅     │          │ "Saved locally"
     │ JSON backup created    │ ✅     │          │
─────┼────────────────────────┼────────┼──────────┼─────────────
15ms │ Check internet...      │ ✅     │          │ "Checking..."
     │ No connectivity ❌      │ ✅     │ ❌       │ "OFFLINE"
─────┼────────────────────────┼────────┼──────────┼─────────────
     │ [60+ seconds pass]     │ ✅     │ ❌       │ [waiting]
     │ Farmer moves to WiFi   │ ✅     │ ❌       │ [waiting]
─────┼────────────────────────┼────────┼──────────┼─────────────
65s  │ Sync manager check     │ ✅     │ ?        │ "Checking..."
     │ Connectivity restored  │ ✅     │ ✅       │ "ONLINE!"
─────┼────────────────────────┼────────┼──────────┼─────────────
66s  │ Get pending records    │ ✅     │ ✅       │ "Syncing..."
     │ Found 1 pending        │ ✅     │ ✅       │
─────┼────────────────────────┼────────┼──────────┼─────────────
68s  │ Push to Firebase       │ ✅     │ ✅       │ "Syncing..."
     │ Success response       │ ✅     │ ✅       │
─────┼────────────────────────┼────────┼──────────┼─────────────
70s  │ Mark as synced         │ ✅     │ ✅       │ "SYNCED ✅"
     │ Update timestamp       │ ✅     │ ✅       │
─────┼────────────────────────┼────────┼──────────┼─────────────
```

---

## 🧪 Verification Checklist

### Pre-Deployment Verification

#### 1. Dependencies Installation
- [ ] Run: `pip install -r requirements_hybrid.txt`
- [ ] No errors during installation
- [ ] All packages imported successfully
- [ ] Python version >= 3.8

#### 2. File Structure Verification
- [ ] `hybrid_storage/` directory exists
- [ ] `hybrid_storage/__init__.py` present
- [ ] `hybrid_storage/connectivity.py` present
- [ ] `hybrid_storage/local_storage.py` present
- [ ] `hybrid_storage/firebase_sync.py` present
- [ ] `hybrid_storage/sync_manager.py` present
- [ ] `main_hybrid.py` present
- [ ] `test_hybrid_storage.py` present
- [ ] Documentation files present (4x .md files)

#### 3. Module Import Verification
```bash
python -c "from hybrid_storage import *; print('✅ Imports successful')"
```
- [ ] No ImportError
- [ ] All modules load without errors
- [ ] Firebase modules load (or warning if offline mode)

#### 4. Firebase Configuration
- [ ] Service account JSON downloaded
- [ ] `GOOGLE_APPLICATION_CREDENTIALS` environment variable set
- [ ] Firebase project ID matches: `coconut-leaf-disease-dcf9a`
- [ ] Firestore database accessible (test)

#### 5. Database Initialization
```bash
python -c "from hybrid_storage.local_storage import LocalStorageManager; s = LocalStorageManager(); print(s.get_storage_stats())"
```
- [ ] `hybrid_storage.db` file created
- [ ] No "database locked" errors
- [ ] Statistics query succeeds
- [ ] All indexes created

---

### Runtime Verification

#### 6. Server Startup
```bash
python main_hybrid.py
```
- [ ] Server starts without errors
- [ ] "Startup complete" message shown
- [ ] All modules initialized
- [ ] Sync manager started
- [ ] No port conflicts (default 8000)

#### 7. API Endpoint Verification
```bash
# In another terminal while server running
curl http://localhost:8000/
curl http://localhost:8000/docs
curl http://localhost:8000/storage/health
curl http://localhost:8000/storage/connectivity
```
- [ ] Root endpoint responds with HTML
- [ ] Swagger UI loads at /docs
- [ ] Health endpoint returns `"status": "healthy"`
- [ ] Connectivity check returns `"is_connected"` boolean

#### 8. Authentication Verification
- [ ] Get valid Firebase token
- [ ] Test authenticated endpoint with token:
  ```bash
  curl -H "Authorization: Bearer {TOKEN}" \
       http://localhost:8000/storage/sync-status
  ```
- [ ] Returns sync status (not 401)
- [ ] Test with invalid token:
  ```bash
  curl -H "Authorization: Bearer invalid" \
       http://localhost:8000/storage/sync-status
  ```
- [ ] Returns 401 Unauthorized

#### 9. Storage Operations Verification
```bash
# Run test script
python test_hybrid_storage.py
```
- [ ] Demo 1: Connectivity check - Shows status
- [ ] Demo 2: Local storage - Record saved
- [ ] Demo 3: Storage stats - Numbers displayed
- [ ] Demo 4: Pending records - List shown
- [ ] Demo 5: User detections - History retrieved
- [ ] Demo 6: Retry logic - Backoff calculated
- [ ] Demo 7: Sync manager - Status shown
- [ ] Demo 8: Force sync - Results displayed
- [ ] Demo 9: JSON backups - Files listed
- [ ] All demos complete without errors

#### 10. Detection Endpoint Test
```python
# Create test image and upload
import requests

with open("test_image.jpg", "rb") as f:
    files = {"file": f}
    data = {"lat": 7.08, "lng": 125.63}
    headers = {"Authorization": f"Bearer {TOKEN}"}
    
    response = requests.post(
        "http://localhost:8000/detect/image",
        files=files,
        data=data,
        headers=headers
    )
    
result = response.json()
```
- [ ] Response status 200
- [ ] Contains "detections" field
- [ ] Contains "storage_status" field
- [ ] "local": true (saved locally)
- [ ] "firebase": true or false (depending on connectivity)
- [ ] Detection saved to SQLite
- [ ] JSON backup created

#### 11. Database Query Verification
```bash
sqlite3 hybrid_storage.db "SELECT COUNT(*) FROM detections;"
sqlite3 hybrid_storage.db "SELECT id, email, is_synced FROM detections LIMIT 5;"
```
- [ ] Returns record count
- [ ] Shows recent detections
- [ ] Columns match schema
- [ ] Data integrity verified

#### 12. Background Sync Verification
- [ ] Stop internet (or simulate with code)
- [ ] Make detection
- [ ] Record shows `is_synced = 0`
- [ ] Restore internet
- [ ] Wait 60+ seconds
- [ ] Check sync status: `GET /storage/sync-status`
- [ ] "pending_records" decreased
- [ ] "successful_syncs" increased
- [ ] View record: should show `is_synced = 1`

---

### Performance Verification

#### 13. Database Performance
- [ ] Query 1000 records: < 100ms
- [ ] Insert 100 records: < 1s
- [ ] Database size: < 100MB for 10k records
- [ ] No query timeouts

#### 14. Connectivity Check Performance
- [ ] DNS check: 1-3 seconds
- [ ] Multiple rapid checks: no hanging
- [ ] Timeout triggers after 3 seconds
- [ ] Fallback to next DNS server works

#### 15. Memory Usage
- [ ] Server memory: < 500MB base
- [ ] After 1000 detections: < 1GB
- [ ] No memory leaks (monitor over time)
- [ ] Background task doesn't accumulate memory

#### 16. Concurrency Test
```bash
ab -n 100 -c 10 -H "Authorization: Bearer TOKEN" \
   http://localhost:8000/storage/sync-status
```
- [ ] Handles 10 concurrent requests
- [ ] No database locked errors
- [ ] Response times consistent
- [ ] All requests succeed

---

### Offline Mode Verification

#### 17. Offline Scenario
- [ ] Disconnect internet
- [ ] Check: `GET /storage/connectivity` → `"is_connected": false`
- [ ] Make detection
- [ ] Detection saves to SQLite ✅
- [ ] Detection NOT synced to Firebase ✅
- [ ] No errors in logs
- [ ] `GET /storage/pending` shows record

#### 18. Offline-to-Online Transition
- [ ] Keep records pending (offline)
- [ ] Restore internet
- [ ] Wait 60 seconds
- [ ] Check: `GET /storage/sync-status` → sync happened
- [ ] Pending count decreased
- [ ] Check: `GET /storage/pending` → empty or fewer
- [ ] Check Firebase console → records appeared

#### 19. Retry Mechanism
- [ ] Simulate Firebase error
- [ ] Make detection (Firebase fails)
- [ ] Record stored with `sync_attempts = 1`
- [ ] Wait for next sync cycle
- [ ] Attempts incremented
- [ ] Eventually syncs or marks failed (after 5 attempts)

---

### Security Verification

#### 20. Authentication
- [ ] Valid token required for protected endpoints
- [ ] Invalid token returns 401
- [ ] User isolation: can't access other user's records
- [ ] SQL injection attempts handled

#### 21. Credentials
- [ ] Firebase credentials NOT in source code
- [ ] Environment variables used correctly
- [ ] No credentials logged
- [ ] Service account JSON secure

#### 22. Data Protection
- [ ] SQLite file readable only by app process
- [ ] JSON backups contain no sensitive data
- [ ] Passwords never stored
- [ ] Encryption considered for deployment

---

### Documentation Verification

#### 23. Documentation Quality
- [ ] HYBRID_STORAGE_SETUP.md: Complete and accurate
- [ ] HYBRID_STORAGE_QUICK_REFERENCE.md: Up-to-date
- [ ] HYBRID_STORAGE_DEPLOYMENT.md: Production-ready
- [ ] HYBRID_STORAGE_SUMMARY.md: Architecture clear
- [ ] Code comments: Present and helpful
- [ ] README: Updated with new system

#### 24. API Documentation
- [ ] Swagger UI working: /docs
- [ ] ReDoc working: /redoc
- [ ] All endpoints documented
- [ ] Request/response examples present
- [ ] Auth requirements clear

---

### Final Acceptance

#### 25. Acceptance Criteria
- [ ] All 24 verification items complete
- [ ] No critical errors in logs
- [ ] Performance acceptable
- [ ] Security checklist passed
- [ ] Documentation accurate
- [ ] Team trained on system
- [ ] Monitoring alerts configured
- [ ] Backup plan in place
- [ ] Rollback procedure documented
- [ ] Go/No-Go decision: **GO** ✅

---

## 📋 Sign-Off Checklist

```
Hybrid Storage System - Production Ready Verification

Date: _______________
Verified By: _______________
Organization: _______________

CHECKLIST ITEMS
├─ [✅] All 25 verification items checked
├─ [✅] No critical issues found
├─ [✅] Performance meets requirements
├─ [✅] Security requirements met
├─ [✅] Documentation reviewed
├─ [✅] Team trained
├─ [✅] Backups configured
├─ [✅] Monitoring alerts active
├─ [✅] Disaster recovery plan ready
└─ [✅] APPROVED FOR PRODUCTION

Signature: _______________
```

---

## 🎉 System Ready!

When all verification items are checked, your system is:

✅ **Fully Functional** - All features working
✅ **Production Ready** - Error handling in place
✅ **Monitored** - Health checks and alerts active
✅ **Documented** - Team understands system
✅ **Secured** - Authentication and encryption
✅ **Scalable** - Ready for growth
✅ **Backed Up** - Data protection in place
✅ **Recoverable** - Disaster plan ready

**You're ready to serve farmers! 🥥👨‍🌾**
