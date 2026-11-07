# Hybrid Stage 2E activation and rollback runbook

Stage 2E is implemented but inactive. The existing `render_local` browser path
remains the default until every checklist item below is deliberately completed.

## Preconditions

1. Preserve `INFERENCE_DEPLOYMENT_MODE=render_local` until the scheduled test window.
2. Confirm the local worker has the reviewed `weights.pt` or reviewed OpenVINO
   artifacts, and the intended `INFERENCE_BACKEND` matches the parity-tested
   backend.
3. Put the same new high-entropy `HYBRID_WORKER_SHARED_SECRET` in Render's
   encrypted environment and the local worker's private environment. Never put
   it in the browser, `.env.example`, Firebase, or source control.
4. Configure the local worker's Cloudinary credentials privately. Confirm its
   account can download authenticated raw assets.
5. In Cloudinary Console, allow the actual CoconutAI browser origin for direct
   upload CORS. Do not add wildcard origins.
6. Keep Firestore production project configuration unchanged. Emulator values
   (`FIRESTORE_EMULATOR_HOST`, `demo-coconutai-hybrid-tests`) must be absent
   from Render and the worker.
7. Retain the current Render-local deployment revision for rollback.

## Controlled end-to-end test procedure

1. Start the local worker with `--once` first and a private HTTPS Render URL;
   verify it has no listener or inbound port.
2. In a non-production test deployment only, set:

   ```text
   INFERENCE_DEPLOYMENT_MODE=hybrid
   HYBRID_UPLOAD_ROUTES_ENABLED=true
   HYBRID_WORKER_ROUTES_ENABLED=true
   HYBRID_CLEANUP_ENABLED=false
   ```

3. In the matching non-production frontend build only, change
   `hybridUploads: false` to `hybridUploads: true`.
4. Upload one disposable metadata-bearing JPEG. Record browser upload time,
   queue wait, worker download, inference, record commit, and media-display
   times from the result card and safe server diagnostics.
5. Confirm one document only exists at `users/{uid}/detections/hybrid_{jobId}`;
   it may contain the minimal authenticated Cloudinary asset reference required
   for server-side private media retrieval, but must contain no signed URL, API
   secret, ownership context, lease, or worker secret.
6. Confirm the record and a Monitoring baseline show the image through
   first-party `/media/...` URLs.
7. Repeat with a worker stopped before claim, stopped mid-inference, offline
   browser after direct upload, and a repeated submit. Verify lease recovery,
   one record maximum, and no re-upload after an ambiguous browser outcome.
8. Verify the full-resolution local inference result against the approved
   parity baseline for classes, confidences, boxes, GPS and metadata.

## Activation

Only after written approval and the controlled test above:

1. Set the three hybrid Render flags above, preserving the current model and
   Firebase configuration.
2. Deploy a frontend build whose explicit browser flag is `true`.
3. Start exactly one worker; begin with slow bounded polling (10 seconds).
4. Observe job status, lease recovery, record commits, and private-media reads
   for one controlled upload before allowing normal use.

## Immediate rollback

1. Set `HYBRID_UPLOAD_ROUTES_ENABLED=false` and
   `HYBRID_WORKER_ROUTES_ENABLED=false`, or restore
   `INFERENCE_DEPLOYMENT_MODE=render_local`.
2. Deploy/revert the frontend so `hybridUploads: false`.
3. Stop the local worker. Existing leased jobs recover after lease expiry;
   completed-but-uncommitted jobs are safe for the bounded reconciler.
4. Do not delete raw assets, jobs, records, compatibility assets, or farmer
   data during rollback. Existing `render_local` uploads continue unchanged.

## Expected availability behavior

- Worker offline/computer off: verified uploads stay pending; no record is
  created until a worker finishes and commits.
- Render Free sleep: browser polling may be delayed; the user sees a queued
  state and should check records later rather than re-uploading.
- Browser offline after direct upload: the browser refuses a blind duplicate
  upload when the provider outcome is ambiguous; job reconciliation remains
  available once connectivity returns.
- Cloudinary download/source-integrity failure: the job fails or retries by
  category; it cannot create a partial Disease Record.
