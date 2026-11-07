# Hybrid polling inference

## Status

This is a staged, **inactive** design. `INFERENCE_DEPLOYMENT_MODE=render_local`
remains the default and preserves the existing Render-local OpenVINO path.
Hybrid mode fails closed unless its worker secret is present, and it must not be
selected until every implementation and verification gate below is complete.

`INFERENCE_DEPLOYMENT_MODE` selects placement (`render_local` or `hybrid`). It
is independent of `INFERENCE_BACKEND` (`openvino` or `ultralytics`).

## Why polling

The local computer makes outbound HTTPS requests to the existing Render
`onrender.com` application. No custom domain, inbound port, Cloudflare Tunnel,
or public local endpoint is needed.

The worker polls a protected Render endpoint, atomically leases one job, then
downloads the source from Cloudinary using a short-lived URL included only in
that lease response. The local computer never accepts arbitrary inbound traffic.

## Direct browser evidence upload

1. Browser authenticates to Render with Firebase.
2. Render creates a pending job and issues a single-use, short-expiry Cloudinary
   **signed upload** authorization for an exact public ID under the job.
3. Browser uploads the original file directly to Cloudinary as an
   `authenticated` image. Render never reads or decodes those bytes.
4. Browser confirms returned asset metadata to Render. Render validates the
   exact public ID, type, format, size, owner, and idempotency key before placing
   the job in the worker queue.

The browser receives an API key and short-lived signature, never the Cloudinary
API secret. The asset is authenticated from creation, not made public first.

## Worker protocol

All worker requests carry an HMAC signature over the method, path, timestamp,
nonce, and SHA-256 of the body. Render rejects expired timestamps, replayed
nonces, invalid signatures, and non-worker routes. The shared secret exists
only in Render secrets and the local worker's protected environment.

- `POST /internal/inference/claim`: atomically leases one pending job.
- `POST /internal/inference/{job_id}/heartbeat`: extends a valid lease.
- `POST /internal/inference/{job_id}/complete`: accepts one matching result.
- `POST /internal/inference/{job_id}/fail`: records a bounded retryable failure.

Persistent jobs live in Firestore, not Render's filesystem. Lease claim/expiry,
idempotency, and state changes use Firestore transactions.

## Asset and record safety

- No disease/follow-up record is committed until a worker result is accepted.
- The worker runs existing MPO/JPEG normalization, EXIF/GPS/XMP extraction,
  OpenVINO inference, coordinate mapping, and evidence preparation locally.
- Render validates the result and atomically creates the existing record.
- A confirmed Firestore failure deletes only the exact pending Cloudinary asset.
- An uncertain write retains it; reconciliation checks the exact idempotency
  record before cleanup.
- Reclaiming an expired job never creates a second asset: public ID and source
  SHA-256 are fixed by its idempotency key.

## Limits and availability

The existing 50 MB limit remains. Render's Free filesystem is ephemeral, so it
is never durable upload storage. Render may sleep after 15 idle minutes and can
restart; worker polling wakes it and tolerates startup latency. Worker downloads
from Cloudinary directly, avoiding Render outbound image transfer.

## Activation gates

Before hybrid mode may be enabled:

1. Implement and test signed browser authorization and confirmation.
2. Implement worker HMAC authentication, transactional Firestore leases,
   retries, expiry, result validation, and reconciliation.
3. Prove hybrid mode never invokes Render decode/model code.
4. Run backend parity tests on the supplied 8064x6048 DJI MPO: pixels,
   EXIF/GPS/XMP/ICC, detections, confidence, boxes, and NMS.
5. Test duplicate upload, worker restart, expired lease, Firestore ambiguity,
   Cloudinary failure, offline worker, and monitoring follow-up workflows.
