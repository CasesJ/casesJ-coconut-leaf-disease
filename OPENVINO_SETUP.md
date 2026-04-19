# OpenVINO Real-Time Integration Guide

## Quick Setup

### 1. Install Dependencies
```bash
# Install OpenVINO and other requirements
pip install -r models/requirements.txt
```

### 2. Verify Model Files
Ensure these files exist in `best_openvino_model/` folder:
- ✅ `best.xml` - Model architecture
- ✅ `best.bin` - Model weights  
- ✅ `metadata.yaml` - Model metadata

### 3. Test OpenVINO Integration

Run the test script to verify everything works:
```bash
python test_openvino_realtime.py
```

This will:
- Load the OpenVINO model
- Process a test image
- Display detections with bounding boxes
- Show inference time (FPS)

## System Requirements

- **CPU**: Intel/AMD processor (OpenVINO optimized for Intel)
- **RAM**: 4GB minimum (8GB recommended)
- **Disk**: ~500MB for OpenVINO runtime
- **OS**: Windows, Linux, or macOS

## Performance Tips

### 1. **Device Selection**
- `"CPU"` - Works on any machine (default)
- `"GPU"` - Requires Intel/NVIDIA GPU + OpenVINO GPU plugin
- `"MYRIAD"` - Intel Movidius (optional accelerator)

To use GPU in `model.py`:
```python
self.compiled_model = self.core.compile_model(str(model_xml), "GPU")
```

### 2. **Inference Optimization**
- **Batch Processing**: Process multiple images simultaneously
- **Multi-threading**: Use async requests for webcam/video streams
- **Input Resizing**: Model expects 640x640 - pre-resize helps

### 3. **Real-Time Webcam Usage**

```python
import cv2
from model import detector

cap = cv2.VideoCapture(0)  # Webcam

while True:
    ret, frame = cap.read()
    if not ret:
        break
    
    result = detector.predict(frame, conf=50)
    
    cv2.imshow("Coconut Disease Detection", result["image"])
    
    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

cap.release()
cv2.destroyAllWindows()
```

## Troubleshooting

### Issue: "Model files not found"
**Solution**: Ensure `best_openvino_model/` folder is in the same directory as `model.py`

### Issue: "ModuleNotFoundError: No module named 'openvino'"
**Solution**: Install OpenVINO:
```bash
pip install openvino>=2024.0.0
```

### Issue: Low FPS (slow inference)
**Solution**: 
- Check CPU usage (may be bottlenecked)
- Use GPU device if available
- Reduce input image size
- Lower confidence threshold (faster filtering)

### Issue: Inaccurate detections
**Solution**:
- Adjust confidence threshold (adjust `conf` parameter)
- Ensure good lighting conditions
- Check that models are properly trained for your use case

## Performance Benchmarks

Typical inference times on different hardware:

| Hardware | Model | FPS | Latency |
|----------|-------|-----|---------|
| Intel i5 (CPU) | YOLO11n | 8-12 | 80-120ms |
| Intel i7 (CPU) | YOLO11n | 12-15 | 65-85ms |
| RTX 3060 (GPU) | YOLO11n | 30-45 | 22-33ms |

*Note: Actual performance depends on image resolution and complexity*

## Integration with Main App

The OpenVINO model is already integrated into `main.py`. Simply:

1. Start the FastAPI server:
```bash
python main.py
```

2. Upload images or use WebSocket for real-time streaming:
```bash
curl -X POST http://localhost:8000/detect \
  -F "file=@leaf_image.jpg" \
  -H "Authorization: Bearer <your_token>"
```

## API Endpoints

- `POST /detect` - Single image detection
- `WebSocket /ws/detect` - Real-time video stream
- `POST /recommend` - Get disease recommendations

## Model Details

- **Architecture**: YOLO11n (Ultralytics)
- **Input**: 640x640 RGB images
- **Classes**: 6 disease types + healthy
- **Output**: Bounding boxes + confidence scores
- **Precision**: FP32
- **Size**: ~15MB

## Next Steps

1. ✅ Test with `test_openvino_realtime.py`
2. ✅ Deploy with `python main.py`
3. ✅ Use with drone GPS integration from `drone_gps.py`
4. ✅ Monitor with Firebase logs

---
**Last Updated**: April 2026  
**Model Version**: YOLO11n OpenVINO IR  
