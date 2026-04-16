import cv2
import numpy as np
from roboflow import Roboflow
import json
import tempfile
import os
import urllib.request

# Initialize Roboflow
rf = Roboflow(api_key="aDUrwpjim8hRnimT1Mvp")
project = rf.workspace().project("coconut_disease_detection-ln7be-dyfjs")
roboflow_model = project.version(3).model

print("🔍 Testing Roboflow API with real coconut leaf image...")
print(f"Model config: {roboflow_model}")

# Try to download a coconut leaf image from Roboflow test dataset or create a proper one
# For now, we'll use a simple colored image to test the API flow
# In production, use real images

# Create a more realistic test image (not random noise)
test_image = np.ones((480, 640, 3), dtype=np.uint8) * 100  # Gray background
# Add some green/brown patches to simulate leaves
cv2.rectangle(test_image, (50, 50), (300, 300), (34, 139, 34), -1)  # Forest green
cv2.rectangle(test_image, (350, 150), (550, 350), (101, 67, 33), -1)  # Brown

# Save image to temporary file
with tempfile.NamedTemporaryFile(delete=False, suffix='.jpg') as tmp_file:
    temp_path = tmp_file.name
    cv2.imwrite(temp_path, test_image)

print(f"✅ Test image saved to: {temp_path}")
print(f"   File size: {os.path.getsize(temp_path)} bytes")

try:
    print(f"\n🚀 Sending to Roboflow API (confidence=20)...")
    results = roboflow_model.predict(temp_path, confidence=20)
    print(f"✅ API Response received!")
    print(f"   Response type: {type(results)}")
    
    # Get JSON
    json_data = results.json()
    print(f"\n📊 Response Data:")
    print(f"   Image dimensions: {json_data.get('image', {})}")
    print(f"   Predictions found: {len(json_data.get('predictions', []))}")
    
    if json_data.get('predictions'):
        print(f"\n🎯 Detections:")
        for pred in json_data['predictions']:
            print(f"   - {pred}")
    else:
        print(f"\n⚠️  No predictions (normal for test image - use real coconut leaf photo)")
    
except Exception as e:
    print(f"\n❌ Error: {e}")
    import traceback
    traceback.print_exc()
finally:
    # Clean up
    try:
        os.unlink(temp_path)
        print(f"\n🗑️  Temp file cleaned up")
    except:
        pass
