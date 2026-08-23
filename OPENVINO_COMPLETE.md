# Model Runtime Status

CoconutAI identifies its active upload model as **YOLO26 v6**. Model assets include `weights.pt` and the retained OpenVINO export in `best_openvino_model/`. FastAPI is the application entry point.

## Verify runtime

1. Install dependencies from `requirements.txt`.
2. Start `uvicorn main:app --reload`.
3. Upload a coconut-leaf image.
4. Confirm the annotated image, labels, confidence scores, and a Detection Record are returned.
5. Confirm a recommendation appears only when the primary confidence is at least 50%.

OpenVINO assets support configured runtime/export paths. The active model metadata returned by `POST /detect/image` is the authoritative runtime label.
