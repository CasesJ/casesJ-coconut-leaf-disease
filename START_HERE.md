# CoconutAI Disease Detection System

## Start the application

1. Create and activate a Python virtual environment.
2. Run `pip install -r requirements.txt`.
3. Configure Firebase credentials and `.env` when remote services are required.
4. Run `python main.py`.
5. Open `http://127.0.0.1:8000`.

## Current workflow

1. A farmer uploads a coconut-leaf image.
2. YOLO26 v6 returns detections, confidence scores, and an annotated image.
3. Reviewable detections (currently `>= 5%`) are stored in the farmer's Firestore detection collection with authenticated Cloudinary image references.
4. Every upload starts as `pending_verification`, regardless of its primary confidence, and requires expert verification.
5. A recommendation is shown below the upload result only when the primary disease confidence is **50% or higher**.
6. Experts can review every pending upload; the **Needs Review** filter prioritizes results at 50% or below.
7. An expert can verify a record or save an expert recommendation; either action marks the selected record verified and notifies its farmer.

## Screens

- **Dashboard:** farm detection and health totals.
- **Disease Detection:** image upload, annotated image, primary confidence, and high-confidence recommendation.
- **Disease Map:** MapLibre locations, severity, areas, and heatmap.
- **Detection Records:** saved uploads, verification status, location, expert guidance, and PDF report.
- **Expert Review:** all pending records, with a **Needs Review** filter for results below 50% confidence.
- **Settings:** account details and logout.

## Main services

| Service | Purpose |
|---|---|
| FastAPI | Web UI and REST API |
| YOLO26 v6 | Coconut disease inference |
| Firebase Auth | Email/password sign-in and role identity |
| Firebase Firestore | Authoritative detection, expert-review, and monitoring records |
| Cloudinary | Authenticated image evidence storage |
| MapLibre GL | Disease location map |

## Important endpoints

- `POST /detect/image`
- `POST /recommendations/fertilizer`
- `GET /detections/my-records`
- `GET /expert/records?status=all|pending|verified`
- `POST /expert/records/{user_id}/{record_id}/verify`
- `PUT /expert/recommendations/{disease}`
- `GET /notifications`
- `GET /reports/my-records.pdf`
- `GET /health`

See `DATA_DICTIONARY.md` for fields and `TROUBLESHOOTING.md` for common problems.
