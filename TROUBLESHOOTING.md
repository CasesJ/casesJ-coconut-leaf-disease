# Troubleshooting CoconutAI

## Application will not start

- Run `pip install -r requirements.txt` in the active virtual environment.
- Start from the repository root: `uvicorn main:app --reload`.
- If port 8000 is busy, stop the existing local server or run Uvicorn on another port.

## Login or Firebase problems

- Confirm Firebase scripts can load and the project settings in `static/index.html` are correct.
- Confirm backend Firebase credentials and `.env` values are available when using remote services.
- Sign out and sign in again to refresh the bearer token.

## Expert verification or notifications

- An upload must be **Pending** before the expert can verify it.
- Expert Review supports All, Pending, and Verified records.
- Restart FastAPI after code changes that add endpoints, including `/notifications/clear`.
- Opening the bell marks alerts viewed; **Clear history** removes only the signed-in user's notification history.

## Records or maps are missing

- Check `hybrid_storage.db` and `detection_records/` for local records.
- GPS fallback coordinates may be used when an image has no location metadata.
- Confirm MapLibre and its tile services are reachable.

## Detection problems

- Confirm model assets exist in `best_openvino_model/` and `weights.pt`.
- Use supported image formats and retry with a clear coconut-leaf image.
- Review the backend console for model, OpenVINO, or image-decoding errors.
