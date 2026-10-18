# Firebase Hosting deployment

This system needs two Google services:

- **Firebase Hosting** publishes the browser application in `static/`.
- **Cloud Run** runs FastAPI and the YOLO model. Firebase Hosting proxies each
  API request to it, so the browser keeps using the same site URL.

The configured Firebase project is `coconut-leaf-disease-dcf9a`, Cloud Run
service is `coconut-detector-api`, and the region is `asia-southeast1`
(Singapore).

## 1. Install and sign in

Install the [Google Cloud CLI](https://cloud.google.com/sdk/docs/install) and
the [Firebase CLI](https://firebase.google.com/docs/cli#install_the_firebase_cli).
Then, from this repository root, run:

```powershell
gcloud auth login
gcloud config set project coconut-leaf-disease-dcf9a
firebase login
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com
```

## 2. Create deployment secrets

Do not upload `.env` or `firebase-service-account.json`. Create a Cloud Run
environment-variable file outside the repository, for example
`C:\temp\coconut-run-env.yaml`:

```yaml
EXPERT_ACCOUNT_EMAIL: "expert@example.com"
EXPERT_ACCOUNT_PASSWORD: "replace-with-a-strong-password"
CORS_ALLOWED_ORIGINS: "https://coconut-leaf-disease-dcf9a.web.app,https://coconut-leaf-disease-dcf9a.firebaseapp.com"
FIREBASE_SERVICE_ACCOUNT_JSON: '{"type":"service_account",...}'
```

Use the complete one-line content of a newly rotated Firebase service-account
JSON for `FIREBASE_SERVICE_ACCOUNT_JSON`. Keep this file private; it contains
credentials.

## 3. Deploy the API, then Hosting

Run these commands from the repository root:

```powershell
gcloud run deploy coconut-detector-api --source . --region asia-southeast1 --allow-unauthenticated --env-vars-file C:\temp\coconut-run-env.yaml --memory 4Gi --cpu 2 --timeout 300
firebase deploy --only hosting
```

The API must be deployed first because `firebase.json` rewrites requests to
that Cloud Run service. The live site will be:

`https://coconut-leaf-disease-dcf9a.web.app`

## Notes

- Cloud Run has an ephemeral filesystem. The application already sends image
  assets to Firebase Storage when valid service-account credentials are set;
  do not rely on the local SQLite database or local upload folders for
  production persistence.
- The Cloud Run service is public so Firebase Hosting can proxy to it. The app
  protects user operations with Firebase ID-token verification.
- Redeploy after a code change with the same two commands in step 3.
