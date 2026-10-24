# Firebase service-account credential incident response

## What changed

On 2026-10-09, a Firebase service-account JSON file previously committed to
Git history was identified as exposed. The application now reads the full
service-account JSON from the `FIREBASE_SERVICE_ACCOUNT_JSON` environment
variable, which must be supplied by the deployment platform's encrypted secret
store. The existing `GOOGLE_APPLICATION_CREDENTIALS` file-path setting remains
only for local development with a file that is never committed.

No credential value belongs in this repository, issue tracker, CI log, or
documentation.

## Required operator actions

1. In Google Cloud Console, open project `coconut-leaf-disease-dcf9a` and go
   to **IAM & Admin → Service Accounts**. Find the service account named in
   the exposed JSON file.
2. Open **Keys**, delete the exposed key, and create a replacement only if the
   deployment cannot use workload identity or another keyless mechanism.
   Deleting the old key is the revocation step; simply creating a new key is
   not enough.
3. In the deployment provider, create/update the encrypted secret
   `FIREBASE_SERVICE_ACCOUNT_JSON` with the complete contents of the new JSON
   key. Do not print this variable in deployment commands or logs.
4. Redeploy and verify Firebase Admin operations. Then delete the downloaded
   replacement from local disk if it is no longer needed.
5. Review Cloud Audit Logs for the service account from the exposure date
   onward, and reduce its IAM roles to the minimum required.

## Git cleanup and collaborator actions

The repository history must be rewritten to remove the credential path, then
force-pushed. After the replacement history is published, every collaborator
must make a fresh clone (or carefully reset to the rewritten default branch).
They must not push an old clone, branch, fork, pull request, release asset,
or CI cache containing the old commit.

History removal reduces accidental distribution but does not revoke a key.
Treat the deleted key as permanently exposed even after cleanup.

## Local development

Copy `.env.example` to an ignored `.env` file and set either
`GOOGLE_APPLICATION_CREDENTIALS` to a credential file stored outside the repo,
or a local `FIREBASE_SERVICE_ACCOUNT_JSON` value. Prefer a least-privilege,
short-lived or keyless identity where your environment supports it.

## Current-system review ? October 24, 2026

This document preserves the historical incident and required operator actions. Source review does not establish whether key revocation, audit review, or Git history cleanup has been completed.

The current backend also uses authenticated Cloudinary evidence storage. Keep CLOUDINARY_API_SECRET and all other deployment secrets outside committed files and frontend code. Protected image APIs enforce farmer/expert access; authenticated asset storage does not replace API authorization.

Local startup requires EXPERT_ACCOUNT_EMAIL and EXPERT_ACCOUNT_PASSWORD in addition to configured service credentials. See [startup](START_HERE.md) and [deployment](FIREBASE_DEPLOY.md). The documentation update neither rotates credentials nor rewrites repository history.
