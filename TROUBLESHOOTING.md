# Troubleshooting CoconutAI

Updated against the local source on October 24, 2026.

## Startup and login

- Install requirements.txt in the active virtual environment and launch from the repository root.
- EXPERT_ACCOUNT_EMAIL and EXPERT_ACCOUNT_PASSWORD must be populated before configuration imports.
- Confirm weights.pt and Ultralytics are available. The retained OpenVINO export is not an automatic startup fallback.
- python main.py and python run.py print their chosen port; they may select another if 8000 is busy.
- Check backend Firebase Admin credentials and frontend project configuration. Sign out/in to refresh an expired token.

## Missing records or images

- Confirm Firestore access to users/{uid}/detections. Record failures do not switch to SQLite/JSON backups.
- Configure all three CLOUDINARY credentials. New images require durable authenticated storage.
- Images load through protected API routes using the signed-in token. Opening an asset without its authorized session may fail.
- Browser record previews reconstruct boxes from original image dimensions and bounding-box metadata. Follow-up comparisons intentionally show the original image.
- A successful upload with no detections at the 5% recording threshold creates no record; inference failures are errors, not valid empty results.

## Recommendations and expert review

- Upload guidance is hidden below 50% primary confidence. At exactly 50%, it is visible.
- Every saved upload remains pending until expert verification or a selected-record expert recommendation action.
- Low-confidence expert prioritization uses below 50%.
- A monitoring baseline must be a verified diseased upload with matching treatment guidance.
- Existing tree links must be preserved; an explicitly selected tree must belong to the farmer.

## Missing map points

Only usable embedded EXIF/DJI EXIF GPS locates uploaded images. Missing metadata is shown as Unknown and is not pinned. Browser coordinates, submitted form fields, default coordinates, and the farm map center do not replace missing upload GPS. Check MapLibre and its configured tile service separately.

## Treatment and inspections

- Farmers have three follow-up image uploads per tree ID across cases, including historical usage. HTTP 409 indicates exhausted allowance; this is separate from inference rate limits.
- Farmer observations are preliminary. Identity review and visible-outcome review are separate expert decisions.
- Missing whole-tree references may leave identity uncertain. GPS is supporting evidence.
- A revision conflict during identity review requires reloading current evidence and review state before resubmitting.
- Case completion is an explicit expert Finish action; an improving observation does not automatically mark the tree healthy.
- Inspection timeline now spans the full row. The separate Recorded treatment plan panel was removed, but treatment snapshots remain stored. Tree GPS location replaces the recommendation/application comparison and shows No GPS location available when embedded coordinates are absent.

## Layout and hosted updates

Farmer records have fixed-size image previews with details alongside, stacking on narrow screens. After deployment, refresh cached frontend assets if the older layout remains visible. Confirm both backend and static assets were deployed; see [deployment](FIREBASE_DEPLOY.md).

For HTTP 429 and upload size limits, see [abuse controls](ABUSE_CONTROLS.md). In-process quotas are not shared across workers/instances.

## Slow image delivery

Monitoring comparison images load eagerly, and expert baseline selection options load independently of case details. Baseline media resolves its case snapshot directly instead of loading the entire care overview. Incomplete historical snapshots can resolve the exact farmer-owned baseline detection.

Successful Cloudinary downloads are reused in a process-local cache for 60 seconds, capped at 32 MiB; versions have separate entries. Authentication and record ownership checks still run for each API request, and responses remain private/no-store. Failed downloads are not cached.

Browser image requests abort after 20 seconds and show a timeout message; reopening the record retries. This bounds the browser wait, not upstream server execution. A 404 indicates a missing or unusable reference, not an upload that simply needs more time. If the original evidence is absent from every referenced storage source, it cannot be recovered by a loading optimization.
