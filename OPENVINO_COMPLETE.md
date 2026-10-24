# Model Runtime Status

Updated against the local source on October 24, 2026. The filename refers to a retained historical export, not the active model selection.

## Active model

model.py requires the repository-root weights.pt and loads it through Ultralytics. The application identifies it as YOLO26 v6. Missing weights, missing Ultralytics, or a model load failure raises an error; startup does not automatically select best_openvino_model as a fallback.

OpenVINO code and exported assets remain in the repository, and openvino is imported by the model module. Keep dependencies installed as specified in requirements.txt. The detector adapter in app/infrastructure/ml/detector.py re-exports the model singleton.

The response model metadata and the loaded weights' class names are the runtime reference. Do not assume a historical class list matches a replacement model.

## Verify a running system

1. Configure the required environment described in [START_HERE.md](START_HERE.md).
2. Start python main.py or uvicorn app.main:app --reload.
3. Sign in and upload a supported symptom image.
4. Inspect returned model metadata, labels, confidence, dimensions, and annotated preview.
5. For qualifying detections, confirm a pending Firestore record and protected original evidence.
6. Check upload guidance at primary confidence >= 50%; lower-confidence results stay hidden on that screen.

Display and recording thresholds are 5%. Annotated upload responses are temporary renderings; persisted previews use original image evidence plus SVG metadata overlays. Follow-up evidence is shown without detection overlays.

Inference errors return an unavailable error and are not persisted as successful empty analyses. Successful results without qualifying upload detections create no detection record.
