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

# Create a test image
test_image = np.random.randint(0, 255, (480, 640, 3), dtype=np.uint8)

print("🔍 Testing Roboflow API...")
print(f"Model object type: {type(roboflow_model)}")

# Save image to temporary file (Roboflow API requires file path)
with tempfile.NamedTemporaryFile(delete=False, suffix='.jpg') as tmp_file:
    temp_path = tmp_file.name
    cv2.imwrite(temp_path, test_image)

print(f"Temp image saved to: {temp_path}")
print(f"File size: {os.path.getsize(temp_path)} bytes")

try:
    results = roboflow_model.predict(temp_path, confidence=40)
    print(f"\n✅ API Response received!")
    print(f"Response type: {type(results)}")
    
    # Get JSON
    json_data = results.json()
    print(f"\nJSON data: {json.dumps(json_data, indent=2, default=str)}")
    
    print(f"\nImage dimensions in response: {json_data.get('image', {})}")
    print(f"Predictions found: {len(json_data.get('predictions', []))}")
    
except Exception as e:
    print(f"\n❌ Error: {e}")
    import traceback
    traceback.print_exc()
finally:
    # Clean up
    try:
        os.unlink(temp_path)
        print(f"\n🗑️ Temp file cleaned up")
    except:
        pass


