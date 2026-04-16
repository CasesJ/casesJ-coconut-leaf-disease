import cv2
import numpy as np
from roboflow import Roboflow
import json
import tempfile
import os

# Initialize Roboflow
rf = Roboflow(api_key="aDUrwpjim8hRnimT1Mvp")
project = rf.workspace().project("coconut_disease_detection-ln7be-dyfjs")
roboflow_model = project.version(3).model

print("🔍 Testing API with different confidence levels...\n")

# Try different confidence values
for conf_level in [1, 5, 10, 20, 30, 40, 50]:
    # Create a test image with different patterns
    test_image = np.ones((480, 640, 3), dtype=np.uint8) * 80  # Slightly darker
    cv2.ellipse(test_image, (320, 240), (150, 200), 45, 0, 360, (34, 139, 34), -1)  # Green ellipse
    cv2.circle(test_image, (200, 150), 50, (101, 67, 33), -1)  # Brown circle
    
    # Add some texture
    for i in range(50):
        pt1 = (np.random.randint(0, 640), np.random.randint(0, 480))
        pt2 = (np.random.randint(0, 640), np.random.randint(0, 480))
        cv2.line(test_image, pt1, pt2, (50, 100, 50), 1)
    
    # Save to temp file
    with tempfile.NamedTemporaryFile(delete=False, suffix='.jpg') as tmp_file:
        temp_path = tmp_file.name
        cv2.imwrite(temp_path, test_image)  # Saved properly
    
    try:
        results = roboflow_model.predict(temp_path, confidence=conf_level)
        json_data = results.json()
        pred_count = len(json_data.get('predictions', []))
        
        print(f"Confidence {conf_level}%: {pred_count} predictions found")
        if pred_count > 0:
            for pred in json_data.get('predictions', []):
                print(f"   → {pred.get('class', 'unknown')}: {pred.get('confidence', 0):.2%}")
    except Exception as e:
        print(f"Confidence {conf_level}%: ERROR - {e}")
    finally:
        try:
            os.unlink(temp_path)
        except:
            pass

print("\n" + "="*60)
print("✅ Test complete. Check if model is receiving requests correctly.")
print("\n💡 IMPORTANT: This test uses synthetic images.")
print("   For real detection, upload ACTUAL coconut leaf photos!")
