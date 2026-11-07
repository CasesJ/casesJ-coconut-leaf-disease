# Stage 2G-B canary preparation (inactive)

This document describes a later, separately approved deployment. The checked-in
defaults remain `INFERENCE_DEPLOYMENT_MODE=render_local`,
`HYBRID_CANARY_ENABLED=false`, `HYBRID_UPLOAD_ROUTES_ENABLED=false`,
`HYBRID_WORKER_ROUTES_ENABLED=false`, `HYBRID_CLEANUP_ENABLED=false`, and
`hybridUploads: false` in `static/index.html`.

## Isolation and memory model

- In a one-farmer canary, keep `INFERENCE_DEPLOYMENT_MODE=render_local`.
  The existing OpenVINO model stays loaded on Render for other farmers. Full
  model unloading is possible only in a separate global `hybrid` deployment
  after all Render-local inference routes, including monitoring and webcam,
  have an approved migration or fail-closed behavior.
- Exactly one Firebase UID is allowed when `HYBRID_CANARY_ENABLED=true`.
  The server checks the UID on every hybrid upload route. `/auth/verify-token`
  and `/auth/user` return only a boolean upload capability to that farmer.
  The frontend requires both the reviewed static switch and the server value.
- The canary's legacy `/detect/image` request is rejected by header-only
  middleware before multipart parsing. Other farmers keep that route.
- A hybrid raw original is streamed in 64 KiB chunks through the existing
  owner-checked private media route. Render does not cache or decode it.
  Thumbnail and annotated fallbacks may not decode it on Render. A worker
  creates one bounded thumbnail from its already decoded local inference
  image and stores it under a deterministic authenticated Cloudinary ID.
  Browser overlays continue using the saved original-coordinate boxes.
- Because the model remains loaded for non-canary users, a 512 MiB Render
  canary requires a live RSS/cgroup check under simultaneous normal use.
  Local tests do not establish a production peak-RAM guarantee.

## Configuration inventory (do not enter secrets in source or browser)

Render encrypted environment, for a future approved canary window:

```text
INFERENCE_DEPLOYMENT_MODE=render_local
INFERENCE_BACKEND=openvino
HYBRID_CANARY_ENABLED=true
HYBRID_CANARY_USER_UIDS=<one exact verified farmer Firebase UID>
HYBRID_UPLOAD_ROUTES_ENABLED=true
HYBRID_WORKER_ROUTES_ENABLED=true
HYBRID_CLEANUP_ENABLED=false
HYBRID_WORKER_SHARED_SECRET=<new high-entropy secret>
```

Keep the current production Firebase service-account configuration and
Cloudinary account variables unchanged. `FIRESTORE_EMULATOR_HOST` and demo
project IDs must be absent from Render and the worker. Keep a copy of the
current Render deployment and environment values for rollback.

Local Windows worker private process environment:

```text
HYBRID_RENDER_API_URL=https://<actual-service>.onrender.com
HYBRID_WORKER_ID=<stable non-secret laptop ID>
HYBRID_WORKER_SHARED_SECRET=<same private Render secret>
CLOUDINARY_CLOUD_NAME=<existing account name>
CLOUDINARY_API_KEY=<private API key>
CLOUDINARY_API_SECRET=<private API secret>
INFERENCE_BACKEND=openvino
OPENVINO_MODEL_DIR=<reviewed local OpenVINO directory>
HYBRID_WORKER_POLL_SECONDS=10
HYBRID_WORKER_RECONCILE_SECONDS=60
HYBRID_WORKER_REQUEST_TIMEOUT_SECONDS=90
```

The worker sets `COCONUTAI_WORKER_PROCESS=1` internally, skips the web `.env`,
and needs no expert account or Firebase Admin credential. Inject secrets with
a private process environment or credential manager. Do not paste values in a
PowerShell command history, issue tracker, or frontend file. Check the worker
without contacting Render with `python hybrid_worker.py --help`. For an
approved live window, start with `python hybrid_worker.py --once`, then use
`python hybrid_worker.py` for continuous outbound HTTPS polling. The worker
opens no inbound port.

## Pre-activation checks

1. Confirm the exact browser `location.origin` from the deployed Render page.
   The repo does not establish the `onrender.com` hostname. Confirm Cloudinary
   accepts an `OPTIONS` preflight and one approved disposable browser upload
   from that exact HTTPS origin. Do not use a wildcard CORS origin.
2. Confirm the reviewed OpenVINO artifacts and parity result on the laptop.
   Worker RAM must exceed the measured full DJI pipeline peak (~658 MiB local).
3. Verify the inactive deployment first: no hybrid upload routes, no hybrid
   browser switch, normal farmer upload and monitoring still work.
4. Verify exactly one intended Firebase UID, verified sign-in, and that the
   UID does not belong to an expert. No production Firestore query is required
   to set the allowlist.
5. Test the approved canary with one disposable image, then the reviewed DJI
   source only after explicit permission for its real Cloudinary upload.
   Verify SHA, original metadata, boxes, one canonical record, thumbnail,
   original display, monitoring baseline, and no private URL exposure.
6. Record Render RSS, cgroup current/limit, and OOM counter before/after
   login, upload authorization, confirmation, worker completion, record view,
   thumbnail, original stream, and a simultaneous normal farmer request.
   Stop if memory approaches the instance limit or any raw source is decoded
   on Render.

## Rollout and monitoring

After a separately approved deployment, keep `hybridUploads: false` until
backend checks pass. Then deploy the reviewed frontend switch as `true`.
The server still denies every non-allowlisted farmer. Start one worker with
`--once`, inspect the job and canonical record, then start continuous polling.
Monitor upload-intent expiry, pending age, lease expiry, retry count,
`record_commit_status`, reconciliation count, media response status, and
Render cgroup/RSS. Worker logs must remain categorical and must not print
private GPS, signed URLs, credentials, or image contents.

The worker calls bounded record reconciliation on startup and periodically.
An offline laptop leaves verified uploads pending; expired leases are
recovered at the next claim, with the existing retry limit. Render sleep may
delay a poll; the worker uses a 90-second request timeout and capped retry
backoff. A completed job is projected into its deterministic
`users/{uid}/detections/hybrid_{jobId}` record transactionally.

## Rollback

1. Disable `HYBRID_UPLOAD_ROUTES_ENABLED` first. This removes the canary
   capability and reopens the existing Render-local upload for that farmer.
   Revert the frontend switch to `hybridUploads: false` in the next build.
2. If safe to drain existing jobs, leave worker routes and one worker running
   until completed jobs reconcile. Otherwise stop the worker and disable
   `HYBRID_WORKER_ROUTES_ENABLED`; pending jobs remain in Firestore for a later
   controlled recovery.
3. Keep `INFERENCE_DEPLOYMENT_MODE=render_local`. Do not delete or rewrite
   jobs, raw sources, thumbnails, disease records, or monitoring references.
4. Verify normal uploads and media for a non-canary farmer and the canary,
   and compare Render memory against the pre-canary baseline.

## Read-only orphan review

Automatic Cloudinary cleanup remains disabled. The bounded
`HybridJobStore.review_cleanup_candidates()` query reports failed jobs,
cleanup-pending jobs, and expired unconfirmed intents. It returns the owner
UID, job ID, exact original public ID and possible deterministic thumbnail ID.
The worker reconciliation endpoint returns only a candidate count; it does
not delete or reveal asset details to browsers.

For each candidate, a future authorized operator must read the exact job,
check that it is not pending, leased, or completed, check whether
`users/{uid}/detections/hybrid_{jobId}` exists, and check any Farmer Monitoring
baseline reference. Then inspect Cloudinary Admin metadata by exact public ID
and asset ID. A missing Admin result is not evidence that another asset may be
deleted. Record decisions for manual review. This procedure performs no
Cloudinary deletion; any deletion requires separate approval and an exact
asset-reference check immediately before the action.

## Remaining limits

- The canary keeps Render's model loaded for all other farmers. Their existing
  full-resolution Render upload can still exceed 512 MiB for the problematic
  DJI image. A one-user canary cannot remove that risk while preserving the
  existing Render-local workflow for everyone else.
- Cloudinary and Firestore cannot share an atomic transaction. Deterministic
  IDs, Admin verification, reconciliation, and manual orphan review contain
  partial failures, but a provider outage can delay completion or leave an
  asset awaiting review.
- The Firestore Emulator cannot prove Render RAM headroom, production
  Cloudinary CORS, or production Firestore indexes. Those checks belong to a
  separately approved controlled deployment window.
