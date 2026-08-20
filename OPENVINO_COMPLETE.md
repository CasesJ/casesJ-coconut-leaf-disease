# Model Runtime Status

CoconutAI uses its configured YOLO11 detection workflow with model assets in `best_openvino_model/` and `weights.pt`. OpenVINO assets are retained for supported runtime/export paths; FastAPI remains the application entry point.

## Verify runtime

1. Install dependencies from `requirements.txt`.
2. Start `uvicorn main:app --reload`.
3. Open the web UI and submit a coconut-leaf image.
4. Confirm an annotated image, detection labels, confidence scores, and a Detection Record are returned.

Do not rely on historical YOLO26 performance statements in older documentation; the system UI and metadata identify the current model as **YOLO11**.
