import cv2
import numpy as np
from roboflow import Roboflow
import tempfile
import os

# Initialize Roboflow with your API key (Cloud API - offloads computation to their servers)
rf = Roboflow(api_key="aDUrwpjim8hRnimT1Mvp")
project = rf.workspace().project("coconut_disease_detection-ln7be-dyfjs")
roboflow_model = project.version(4).model

class CoconutDiseaseDetector:
    def __init__(self):
        # Load the Roboflow YOLO 11 model
        self.model = roboflow_model
        # Class names from Roboflow project (6 disease classes)
        self.class_names = {
            0: 'bud root',
            1: 'Caterpillars',
            2: 'Cercospora',
            3: 'Drying of Leaflets',
            4: 'Healthy',
            5: 'Pestaltiopsis'
        }
        print("✅ CLOUD API: Using Roboflow cloud servers (low CPU usage)")
        print("   💾 Keeps laptop cool - computation on cloud")

    def predict(self, image: np.ndarray, conf: float = 20) -> dict:
        try:
            print(f"🔍 Cloud prediction with confidence threshold: {conf}")
            
            # Save image to temporary file (Roboflow API requires file path)
            with tempfile.NamedTemporaryFile(delete=False, suffix='.jpg') as tmp_file:
                temp_path = tmp_file.name
                cv2.imwrite(temp_path, image)
            
            # Send to Roboflow Cloud API
            results = self.model.predict(temp_path, confidence=conf)
            
            # Clean up temp file
            try:
                os.unlink(temp_path)
            except:
                pass
            
            print(f"📦 Cloud API response received")
            
            detections = []

            # Extract predictions from Roboflow response
            predictions = []
            
            if hasattr(results, 'json'):
                print("✅ Results has json() method")
                predictions_data = results.json()
                predictions = predictions_data.get('predictions', [])
            elif isinstance(results, dict) and 'predictions' in results:
                print("✅ Results is dict with predictions key")
                predictions = results.get('predictions', [])
            elif isinstance(results, list):
                print("✅ Results is a list")
                predictions = results
            
            print(f"📊 Found {len(predictions)} predictions")
            
            if len(predictions) == 0:
                print("ℹ️  No predictions found")
                return {"detections": detections, "image": image}
            
            for idx, pred in enumerate(predictions):
                try:
                    print(f"\n🎯 Processing prediction {idx + 1}")
                    
                    x = int(pred.get('x', pred.get('X', 0)))
                    y = int(pred.get('y', pred.get('Y', 0)))
                    width = int(pred.get('width', pred.get('w', 1)))
                    height = int(pred.get('height', pred.get('h', 1)))
                    confidence = float(pred.get('confidence', pred.get('conf', 0)))
                    
                    # Extract class name from prediction
                    raw_class = pred.get('class', pred.get('predicted_classes', 'unknown'))
                    
                    if isinstance(raw_class, list):
                        raw_class = raw_class[0] if raw_class else 'unknown'
                    
                    try:
                        class_id = int(raw_class)
                        class_name = self.class_names.get(class_id, 'unknown')
                    except (ValueError, TypeError):
                        class_name = str(raw_class)
                    
                    print(f"   Class: {class_name}, Confidence: {confidence:.2%}")
                    
                    # Convert to bbox format (x1, y1, x2, y2)
                    x1 = max(0, x - width // 2)
                    y1 = max(0, y - height // 2)
                    x2 = min(image.shape[1], x + width // 2)
                    y2 = min(image.shape[0], y + height // 2)

                    # Draw bounding box on image
                    color = (0, 200, 100) if class_name.lower() == "healthy" else (0, 60, 220)
                    cv2.rectangle(image, (x1, y1), (x2, y2), color, 3)
                    
                    # Draw text with background
                    text = f"{class_name} {confidence:.2%}"
                    font = cv2.FONT_HERSHEY_SIMPLEX
                    font_scale = 0.7
                    thickness = 2
                    text_size = cv2.getTextSize(text, font, font_scale, thickness)[0]
                    
                    text_x = x1
                    text_y = max(30, y1 - 10)
                    cv2.rectangle(image, (text_x - 3, text_y - text_size[1] - 6), 
                                (text_x + text_size[0] + 3, text_y + 3), color, -1)
                    cv2.putText(image, text, (text_x, text_y), font, font_scale, (255, 255, 255), thickness)

                    detections.append({
                        "class": class_name,
                        "confidence": round(confidence, 3),
                        "bbox": [x1, y1, x2, y2]
                    })
                except Exception as e:
                    print(f"❌ Error processing prediction {idx}: {e}")
                    continue

            print(f"✅ Successfully found {len(detections)} detections")
            return {"detections": detections, "image": image}
        except Exception as e:
            print(f"❌ Prediction error: {e}")
            import traceback
            traceback.print_exc()
            return {"detections": [], "image": image}

# Singleton — loaded once on startup
detector = CoconutDiseaseDetector()

# ─── ROBOFLOW CLOUD API NOTES ────────────────────────────────────────────────
# Using Cloud API (default)
# 
# ✅ Benefits for laptop:
#    - Computation offloaded to Roboflow servers
#    - CPU usage: ~5-10% (very low)
#    - No overheating
#    - Works on any machine (Windows, Mac, Linux)
#    - Reliable and stable
#
# ⚠️  Trade-offs:
#    - Requires internet connection
#    - API latency: 1-2 seconds per request
#    - GPS may drift slightly during drone flight
#    - Free tier has rate limits
#
# 📊 Performance Summary:
#    Cloud API:        1-2s latency (keeps laptop cool)
#    Local Roboflow:   200-500ms latency (high CPU)
#    Docker:           <100ms latency (very fast) - needs Docker installed
#
# To switch modes later:
# - For lower latency: Install Docker + see model.py for Docker setup
# - For offline use: Use local Roboflow (high CPU but works offline)