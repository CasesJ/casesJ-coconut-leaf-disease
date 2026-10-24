# CoconutAI Disease Detection System

Updated against the local source on October 24, 2026.

## Start locally

From the repository root in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
python main.py
```

Copy the example only if you do not already have a .env file. Before starting, populate the ignored .env with EXPERT_ACCOUNT_EMAIL and EXPERT_ACCOUNT_PASSWORD (required at configuration import), Firebase Admin credentials, and CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, and CLOUDINARY_API_SECRET. Use FIREBASE_SERVICE_ACCOUNT_JSON or a private file referenced by GOOGLE_APPLICATION_CREDENTIALS.

Open the URL printed by the launcher, normally http://localhost:8000. The launcher selects a nearby free port when needed. python run.py is an alternative; uvicorn app.main:app --reload uses an explicitly selected port. Local backend API documentation is at /docs.

## Current workflow

1. A signed-in farmer uploads a JPEG or PNG showing a coconut disease symptom, including leaf, bud, or trunk evidence. The default limit is 10,000,000 bytes and 40 megapixels.
2. The local weights.pt model runs through Ultralytics; the application labels it YOLO26 v6. Detections are displayed and eligible for recording from 5% confidence.
3. A saved upload starts as pending_verification. A successful result without qualifying detections creates no new record. Inference or persistence failures return errors.
4. Upload guidance appears at primary confidence of at least 50%. Below 50%, guidance is hidden on that screen and the pending upload is prioritized for expert review.
5. Experts verify records or save record-specific recommendations, verifying the selected record, recording an audit event, and notifying the farmer.
6. Experts can create care records from verified diseased uploads with treatment guidance, registering a new tree ID or selecting an existing farmer-owned tree.
7. Farmers can plan treatment, record actual applications, and submit up to three follow-up images per tree ID across cases. Experts separately review tree identity and visible outcomes and explicitly finish cases when confirming the tree is healthy.

## Screens and evidence

- Farmer Detection Records use a fixed 180 ? 180 image preview on the left and details on the right. Narrow screens stack the card with a 160 ? 160 preview. Clicking opens the enlarged image.
- Expert Review provides a pending queue, verification, recommendation editing, and audit history.
- Care records show baseline and original follow-up images, a full-width Inspection timeline, and baseline/inspection GPS coordinates when embedded in the evidence images. The separate Recorded treatment plan panel is removed; stored treatment snapshots remain.
- Uploaded images are mapped only with valid embedded EXIF/DJI EXIF GPS. Missing GPS is Unknown and unmapped. The farm map center is separate.
- Dashboard summaries, reports, notifications, and account settings support the workflow.

Confidence does not verify a diagnosis or establish recovery. Firestore is authoritative for records; Cloudinary stores authenticated originals. Browser SVG overlays reconstruct boxes from metadata instead of persisting new annotated copies.

## Documentation

See [architecture](ARCHITECTURE_DIAGRAMS.md), [data dictionary](DATA_DICTIONARY.md), [API schemas](JSON_SCHEMA_DOCUMENTATION.md), [recommendations](RECOMMENDATIONS_ENHANCEMENT.md), [storage](HYBRID_STORAGE_README.md), [runtime](OPENVINO_COMPLETE.md), [abuse controls](ABUSE_CONTROLS.md), [deployment](FIREBASE_DEPLOY.md), and [troubleshooting](TROUBLESHOOTING.md).
