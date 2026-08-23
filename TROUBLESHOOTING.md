# Troubleshooting CoconutAI

## Application will not start

- Run `pip install -r requirements.txt` in the active virtual environment.
- Start from the repository root with `uvicorn main:app --reload`.
- If port 8000 is busy, stop the existing server or select another port.

## Login or Firebase problems

- Confirm Firebase scripts and project settings in `static/index.html` are correct.
- Confirm backend Firebase credentials and `.env` values are available when remote services are required.
- Sign out and sign in again to refresh the bearer token.

## Recommendations and expert review

- Recommendations are intentionally hidden for upload results below 50% confidence.
- A result at or above 50% shows its recommendation below the disease detection, but is still pending expert verification.
- **Needs Review** in Expert Review is the low-confidence queue (below 50%).
- Expert verification or saving an expert recommendation changes the selected record to verified and notifies its farmer.

## Records or maps are missing

- Check `hybrid_storage.db` and `detection_records/` for local records.
- GPS fallback coordinates may be used when an image has no location metadata.
- Confirm MapLibre and its tile service are reachable.

## Detection problems

- Confirm `weights.pt` and `best_openvino_model/` exist.
- Use a clear, supported coconut-leaf image and retry.
- Review the FastAPI console for model, OpenVINO, or image-decoding errors.
