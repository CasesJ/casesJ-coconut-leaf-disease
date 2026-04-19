# OpenVINO Quick Reference Guide

## 🚀 Installation (Choose Your OS)

### Windows
```bash
python setup_openvino.bat
```

### Linux/Mac
```bash
chmod +x setup_openvino.sh
./setup_openvino.sh
```

### Manual Installation
```bash
pip install -r models/requirements.txt
python setup_openvino.py
```

---

## 📝 Common Tasks

### 1. Run Single Image Detection
```bash
python -c "
import cv2
from model import detector

image = cv2.imread('leaf.jpg')
result = detector.predict(image, conf=50)
cv2.imwrite('result.jpg', result['image'])
print(f'Found {len(result[\"detections\"])} detections')
"
```

### 2. Test Real-Time Webcam
```bash
python realtime_stream.py
```
Press `q` to quit, `s` to save frame.

### 3. Run Batch Processing
```bash
python -c "
from pathlib import Path
import cv2
from model import detector

for img_path in Path('images').glob('*.jpg'):
    image = cv2.imread(str(img_path))
    result = detector.predict(image, conf=50)
    print(f'{img_path.name}: {len(result[\"detections\"])} detections')
"
```

### 4. Start FastAPI Server
```bash
python main.py
# Then open: http://localhost:8000
```

### 5. Get Disease Recommendations
```bash
python -c "
import cv2
from model import detector

image = cv2.imread('disease.jpg')
result = detector.predict(image, conf=50)

for det in result['detections']:
    rec = detector.get_fertilizer_recommendation(det['class'], det['confidence'])
    print(f'Disease: {rec[\"disease\"]}')
    print(f'Treatment: {rec[\"treatment\"]}')
"
```

### 6. Test Performance
```bash
python -c "
import time
import numpy as np
from model import detector

image = np.random.randint(0, 255, (480, 640, 3), dtype=np.uint8)

start = time.time()
for _ in range(10):
    detector.predict(image, conf=50)
elapsed = time.time() - start

print(f'Average: {(elapsed/10)*1000:.2f}ms')
print(f'Average FPS: {10/elapsed:.2f}')
"
```

### 7. Use Different Confidence Levels
```python
from model import detector
import cv2

image = cv2.imread('leaf.jpg')

# Fast (higher threshold)
fast = detector.predict(image, conf=75)

# Balanced (medium threshold)
balanced = detector.predict(image, conf=50)

# Accurate (lower threshold)
accurate = detector.predict(image, conf=25)

print(f"Fast: {len(fast['detections'])} detections")
print(f"Balanced: {len(balanced['detections'])} detections")
print(f"Accurate: {len(accurate['detections'])} detections")
```

### 8. Save Results as JSON
```python
import json
import cv2
from model import detector

image = cv2.imread('leaf.jpg')
result = detector.predict(image, conf=50)

# Save detections
data = {
    'num_detections': len(result['detections']),
    'detections': result['detections']
}

with open('results.json', 'w') as f:
    json.dump(data, f, indent=2)
```

### 9. Stream from Video File
```python
import cv2
from model import detector

cap = cv2.VideoCapture('video.mp4')

while True:
    ret, frame = cap.read()
    if not ret:
        break
    
    result = detector.predict(frame, conf=50)
    cv2.imshow('Detection', result['image'])
    
    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

cap.release()
cv2.destroyAllWindows()
```

### 10. Integrate with FastAPI
```python
from fastapi import FastAPI, File, UploadFile
import cv2
import numpy as np
from model import detector

app = FastAPI()

@app.post("/detect")
async def detect(file: UploadFile = File(...)):
    contents = await file.read()
    nparr = np.frombuffer(contents, np.uint8)
    image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    
    result = detector.predict(image, conf=50)
    
    return {"detections": result['detections']}

# Run with: uvicorn script:app --reload
```

---

## ⚙️ Configuration Quick Changes

### Change Inference Device
Edit `openvino_config.py`:
```python
MODEL_CONFIG = {
    "device": "GPU",  # CPU, GPU, MYRIAD, AUTO
}
```

### Change Confidence Threshold
```python
result = detector.predict(image, conf=60)  # 0-100
```

### Change Model Path
Edit `model.py` line 11:
```python
model_xml = "path/to/best.xml"
model_bin = "path/to/best.bin"
```

---

## 🐛 Debugging

### Check Installation
```bash
python -c "import openvino; print(f'OpenVINO {openvino.__version__} installed')"
```

### Test Model Loading
```bash
python -c "from model import detector; print('Model loaded OK')"
```

### Check Model Files
```bash
ls best_openvino_model/
# or on Windows
dir best_openvino_model\
```

### Enable Debug Logging
```python
import logging
logging.basicConfig(level=logging.DEBUG)

from model import detector
result = detector.predict(image, conf=50)
```

---

## 📊 Performance Monitoring

### Quick FPS Test
```bash
python -c "
import time
import numpy as np
from model import detector

img = np.random.randint(0, 255, (480, 640, 3), dtype=np.uint8)
times = []

for _ in range(30):
    start = time.time()
    detector.predict(img, conf=50)
    times.append(time.time() - start)

print(f'Avg: {(sum(times)/len(times))*1000:.1f}ms')
print(f'FPS: {len(times)/sum(times):.1f}')
"
```

### Memory Usage
```bash
python -c "
import psutil
from model import detector

process = psutil.Process()
mem_before = process.memory_info().rss / 1024 / 1024

# Use detector...
result = detector.predict(image, conf=50)

mem_after = process.memory_info().rss / 1024 / 1024
print(f'Memory used: {mem_after - mem_before:.1f} MB')
"
```

---

## 🔧 Troubleshooting Checklist

- [ ] Python 3.8+ installed: `python --version`
- [ ] OpenVINO installed: `pip list | grep openvino`
- [ ] Model files exist: `ls best_openvino_model/`
- [ ] Can import detector: `python -c "from model import detector"`
- [ ] Can load image: `python -c "import cv2; img = cv2.imread('test.jpg')"`
- [ ] Can run inference: `python test_openvino_realtime.py`
- [ ] No network errors (should work offline)

---

## 📚 File Reference

| File | Purpose |
|------|---------|
| `model.py` | OpenVINO inference engine |
| `openvino_config.py` | Configuration settings |
| `test_openvino_realtime.py` | Test suite |
| `realtime_stream.py` | Webcam streaming |
| `setup_openvino.py` | Setup wizard |
| `OPENVINO_SETUP.md` | Detailed setup guide |
| `OPENVINO_EXAMPLES.md` | Code examples |

---

## 🎯 API Quick Reference

### detector.predict(image, conf=50)
- **image**: numpy array (BGR format)
- **conf**: confidence threshold (0-100)
- **returns**: dict with `detections` and `image`

### Detection Format
```json
{
  "class": "Healthy",
  "confidence": 0.95,
  "bbox": [x1, y1, x2, y2]
}
```

### detector.get_fertilizer_recommendation(disease_name, confidence)
- **disease_name**: string (e.g., "Cercospora")
- **confidence**: float (0-1 or 0-100)
- **returns**: dict with treatment/prevention/fertilizer

---

## 🌐 Web Interface (FastAPI)

If running `python main.py`:

```
API Base: http://localhost:8000
Web UI:   http://localhost:8000/static/
Docs:     http://localhost:8000/docs
```

Upload image → Get results with bounding boxes

---

## 💡 Pro Tips

1. **Faster Processing**: Use higher confidence threshold
2. **Better Accuracy**: Use lower confidence threshold
3. **Save Memory**: Process one image at a time
4. **Best FPS**: Use GPU if available
5. **Batch Jobs**: Process multiple images in a loop
6. **Error Handling**: Always check result['detections']

---

## 🆘 Getting Help

1. Check `TROUBLESHOOTING.md`
2. Run `python setup_openvino.py`
3. Enable debug logging
4. Check system logs
5. Verify model files integrity

---

**Quick Link**: `OPENVINO_INTEGRATION.md` for complete guide
