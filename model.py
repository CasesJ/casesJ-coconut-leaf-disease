import cv2
import numpy as np
from roboflow import Roboflow
import tempfile
import os

# Initialize Roboflow with your API key
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

    def predict(self, image: np.ndarray, conf: float = 20) -> dict:
        try:
            print(f"🔍 Starting prediction with confidence threshold: {conf}")
            
            # ✅ FIX: Save image to temporary file (Roboflow API requires file path)
            with tempfile.NamedTemporaryFile(delete=False, suffix='.jpg') as tmp_file:
                temp_path = tmp_file.name
                # Convert BGR to RGB for proper color handling
                image_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
                cv2.imwrite(temp_path, cv2.cvtColor(image_rgb, cv2.COLOR_RGB2BGR))
            
            # Pass file path to Roboflow API
            results = self.model.predict(temp_path, confidence=conf)
            
            # Clean up temp file
            try:
                os.unlink(temp_path)
            except:
                pass
            
            print(f"📦 Raw results type: {type(results)}")
            print(f"📦 Raw results: {results}")
            
            detections = []

            # Try different ways to extract predictions from Roboflow response
            predictions = []
            
            if hasattr(results, 'json'):
                print("✅ Results has json() method")
                predictions_data = results.json()
                print(f"📋 JSON data: {predictions_data}")
                predictions = predictions_data.get('predictions', [])
            elif isinstance(results, dict) and 'predictions' in results:
                print("✅ Results is dict with predictions key")
                predictions = results.get('predictions', [])
            elif isinstance(results, list):
                print("✅ Results is a list")
                predictions = results
            else:
                print(f"⚠️ Unexpected results format: {type(results)}")
                predictions = []
            
            print(f"📊 Found {len(predictions)} predictions")
            
            if len(predictions) == 0:
                print("⚠️ No predictions found - model might not detect anything or confidence is too high")
                return {"detections": detections, "image": image}
            
            for idx, pred in enumerate(predictions):
                try:
                    print(f"\n🎯 Processing prediction {idx + 1}: {pred}")
                    
                    x = int(pred.get('x', pred.get('X', 0)))
                    y = int(pred.get('y', pred.get('Y', 0)))
                    width = int(pred.get('width', pred.get('w', 1)))
                    height = int(pred.get('height', pred.get('h', 1)))
                    confidence = float(pred.get('confidence', pred.get('conf', 0)))
                    
                    # Extract class name from prediction
                    # Roboflow might return class name or class ID, so handle both
                    raw_class = pred.get('class', pred.get('predicted_classes', 'unknown'))
                    
                    if isinstance(raw_class, list):
                        raw_class = raw_class[0] if raw_class else 'unknown'
                    
                    # Try to convert to int (class ID), otherwise use as string (class name)
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

                    # Draw bounding box on image (Green for Healthy, Red for diseases)
                    color = (0, 200, 100) if class_name.lower() == "healthy" else (0, 60, 220)
                    cv2.rectangle(image, (x1, y1), (x2, y2), color, 3)
                    
                    # Draw text with background for better visibility
                    text = f"{class_name} {confidence:.2%}"
                    font = cv2.FONT_HERSHEY_SIMPLEX
                    font_scale = 0.7
                    thickness = 2
                    text_size = cv2.getTextSize(text, font, font_scale, thickness)[0]
                    
                    # Background rectangle for text
                    text_x = x1
                    text_y = max(30, y1 - 10)
                    cv2.rectangle(image, (text_x - 3, text_y - text_size[1] - 6), 
                                (text_x + text_size[0] + 3, text_y + 3), color, -1)
                    
                    # White text
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

    def get_fertilizer_recommendation(self, disease_name: str, confidence: float) -> dict:
        """
        Provides farmer-friendly fertilizer recommendations based on detected disease.
        
        Args:
            disease_name: Name of detected disease
            confidence: Confidence level (0-1 or 0-100)
        
        Returns:
            dict with Fertilizer, Treatment, and Prevention recommendations
        """
        
        # Normalize confidence to 0-100 range
        if confidence > 1:
            conf_percent = confidence
        else:
            conf_percent = confidence * 100
        
        # Recommendations database for coconut diseases in Davao
        recommendations = {
            'bud root': {
                'fertilizer': 'Apply NPK 12:12:17 with micronutrients. Use 500g per tree every 3 months to strengthen root system.',
                'treatment': 'Remove infected leaves. Apply fungicide (Mancozeb 80%) at 2g per liter of water weekly for 4 weeks.',
                'prevention': 'Ensure good drainage. Avoid waterlogging. Maintain proper spacing between trees for air circulation.'
            },
            'caterpillars': {
                'fertilizer': 'Use NPK 10:10:10 balanced fertilizer monthly to boost tree vigor and recovery.',
                'treatment': 'Spray Bacillus thuringiensis (Bt) organic insecticide every 7 days until pest is controlled.',
                'prevention': 'Monitor leaves regularly. Remove infected fronds manually. Encourage natural predators like birds.'
            },
            'cercospora': {
                'fertilizer': 'Apply potassium-rich fertilizer (NPK 8:8:16) to improve leaf resistance. Every 4 weeks.',
                'treatment': 'Prune affected leaves. Spray Chlorothalonil fungicide (1.5%) weekly for 3-4 weeks.',
                'prevention': 'Remove fallen leaves. Space trees properly. Water at base, not on leaves. Improve air flow.'
            },
            'drying of leaflets': {
                'fertilizer': 'Feed with Magnesium sulfate (1kg per tree) and NPK 15:15:15 every 6 weeks.',
                'treatment': 'Apply potassium nitrate solution (2%) via foliar spray twice weekly for 4 weeks.',
                'prevention': 'Water deeply twice weekly during dry season. Mulch soil base. Use drip irrigation if possible.'
            },
            'healthy': {
                'fertilizer': 'Apply balanced NPK 12:12:12 every 3 months. Use phosphate rich during flowering season.',
                'treatment': 'No treatment needed. Continue regular monitoring.',
                'prevention': 'Maintain regular fertilization. Water consistently. Remove dead leaves. Monitor for pests.'
            },
            'pestaltiopsis': {
                'fertilizer': 'Use NPK 10:10:20 with Zinc supplement (5kg Zn per hectare annually).',
                'treatment': 'Remove all infected fronds. Spray Copper fungicide (1%) every 10 days for 6 weeks.',
                'prevention': 'Maintain tree vigor with proper fertilization. Prune dead branches. Ensure soil drainage.'
            }
        }
        
        disease_lower = disease_name.lower().strip()
        
        # Get recommendation or use default
        rec = recommendations.get(disease_lower, recommendations['healthy'])
        
        # Add confidence note
        confidence_note = ""
        if conf_percent >= 85:
            confidence_note = " (High confidence - follow recommendations closely)"
        elif conf_percent >= 70:
            confidence_note = " (Good confidence - follow recommendations)"
        else:
            confidence_note = " (Lower confidence - consult agricultural expert to confirm)"
        
        return {
            'disease': disease_name,
            'confidence': round(conf_percent, 2),
            'fertilizer': rec['fertilizer'],
            'treatment': rec['treatment'],
            'prevention': rec['prevention'],
            'note': f"Davao region recommendations{confidence_note}"
        }


# Singleton — loaded once on startup
detector = CoconutDiseaseDetector()