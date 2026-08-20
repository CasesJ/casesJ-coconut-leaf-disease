# CoconutAI Disease Detection System

## Start the application

1. Create and activate a Python virtual environment.
2. Run `pip install -r requirements.txt`.
3. Configure Firebase credentials and `.env` when remote services are required.
4. Start the application: `uvicorn main:app --reload`.
5. Open `http://127.0.0.1:8000`.

## Current workflow

1. A farmer uploads a coconut-leaf image.
2. YOLO11 produces detections, confidence values, and an annotated image.
3. The system saves a Detection Record locally and synchronizes with Firebase when available.
4. The record begins as **Pending**.
5. An expert verifies it or saves an expert recommendation.
6. The farmer receives a verification alert in the notification bell and the record becomes **Verified**.

## System screens

- **Dashboard:** Total Detections, Diseased Trees, Healthy Trees, Mapped Locations, and Disease Prevalence.
- **Disease Detection:** upload, annotated output, confidence, and recommendations.
- **Disease Map:** MapLibre GL locations, severity, areas, and heatmap.
- **Detection Records:** saved uploads, status, location, recommendations, and PDF report.
- **Expert Review:** All, Pending, and Verified filters with verification controls.
- **Settings:** profile, YOLO11/FastAPI/MapLibre GL information, and logout.

## Main services

| Service | Purpose |
|---|---|
| FastAPI | UI, REST API, and WebSocket stream |
| YOLO11 / OpenVINO assets | Coconut disease inference |
| Firebase Auth | Email/password sign-in |
| Firebase RTDB / Firestore | Remote synchronization when configured |
| SQLite (`hybrid_storage.db`) | Local-first Detection Records |
| MapLibre GL | Disease map |

## Important endpoints

- `POST /detect/image`
- `GET /records?user_id=<uid>`
- `GET /expert/records?status=all|pending|verified`
- `POST /expert/records/{user_id}/{record_id}/verify`
- `GET /notifications`
- `POST /notifications/mark-read`
- `POST /notifications/clear`
- `GET /health`

See `DATA_DICTIONARY.md` for fields and `TROUBLESHOOTING.md` for common problems.
