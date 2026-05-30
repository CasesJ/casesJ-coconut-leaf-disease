import cv2
import numpy as np
from openvino import Core
import os
from pathlib import Path

class CoconutDiseaseDetector:
    def __init__(self):
        """Initialize OpenVINO detector with best.xml and best.bin model files"""
        # Get the model path - navigate to best_openvino_model folder
        current_dir = Path(__file__).parent
        model_xml = current_dir / "best_openvino_model" / "best.xml"
        model_bin = current_dir / "best_openvino_model" / "best.bin"
        
        # Verify model files exist
        if not model_xml.exists() or not model_bin.exists():
            raise FileNotFoundError(f"Model files not found. XML: {model_xml}, BIN: {model_bin}")
        
        print(f"[OK] Loading OpenVINO model from {model_xml}")
        
        # Initialize OpenVINO
        self.core = Core()
        self.compiled_model = self.core.compile_model(str(model_xml), "CPU")
        self.infer_request = self.compiled_model.create_infer_request()
        
        # Get model input/output info
        self.input_layer = self.compiled_model.input(0)
        self.output_layer = self.compiled_model.output(0)
        
        self.input_shape = self.input_layer.shape
        self.model_height = int(self.input_shape[2])
        self.model_width = int(self.input_shape[3])
        
        print(f"[OK] Model loaded successfully. Input shape: {self.input_shape}")
        
        # Class names from metadata (YOLO11n model classes)
        self.class_names = {
            0: 'Caterpillars',
            1: 'Cercospora',
            2: 'Drying of Leaflets',
            3: 'Healthy',
            4: 'Pestalotiopsis',
            5: 'bud root'
        }

    def _non_max_suppression(self, detections, nms_threshold=0.45):
        """
        Apply Non-Maximum Suppression to remove overlapping detections
        
        Args:
            detections: List of detection dicts with 'bbox', 'confidence', 'class'
            nms_threshold: IOU threshold for NMS (default 0.45)
        
        Returns:
            Filtered list of detections
        """
        if len(detections) == 0:
            return detections
        
        # Convert to numpy for easier processing
        boxes = np.array([d['bbox'] for d in detections])
        confidences = np.array([d['confidence'] for d in detections])
        
        # Calculate areas
        x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
        areas = (x2 - x1 + 1) * (y2 - y1 + 1)
        
        # Sort by confidence (descending)
        order = np.argsort(-confidences)
        
        keep = []
        while len(order) > 0:
            i = order[0]
            keep.append(i)
            
            if len(order) == 1:
                break
            
            # Calculate IOU with all remaining boxes
            ious = []
            xi1 = np.maximum(x1[i], x1[order[1:]])
            yi1 = np.maximum(y1[i], y1[order[1:]])
            xi2 = np.minimum(x2[i], x2[order[1:]])
            yi2 = np.minimum(y2[i], y2[order[1:]])
            
            inter = np.maximum(xi2 - xi1 + 1, 0) * np.maximum(yi2 - yi1 + 1, 0)
            union = areas[i] + areas[order[1:]] - inter
            iou = inter / union
            
            # Keep boxes with IOU below threshold
            order = order[1:][iou < nms_threshold]
        
        return [detections[i] for i in keep]

    def predict(self, image: np.ndarray, conf: float = 50) -> dict:
        """
        Real-time inference using OpenVINO model
        
        Args:
            image: Input image as numpy array (BGR format)
            conf: Confidence threshold (0-100, default 50 for better filtering)
        
        Returns:
            dict with detections and annotated image
        """
        try:
            print(f"[PREDICT] Starting OpenVINO prediction with confidence threshold: {conf}")
            
            # Normalize confidence to 0-1 range if needed
            confidence_threshold = conf / 100.0 if conf > 1 else conf
            
            # Store original image dimensions
            original_height, original_width = image.shape[:2]
            
            # Preprocess image for model input
            # YOLO26s expects 640x640 RGB images normalized to 0-1
            resized_image = cv2.resize(image, (self.model_width, self.model_height))
            resized_image = cv2.cvtColor(resized_image, cv2.COLOR_BGR2RGB)
            
            # Normalize to 0-1 range
            input_data = resized_image.astype(np.float32) / 255.0
            
            # Add batch dimension (1, 3, 640, 640)
            input_data = np.transpose(input_data, (2, 0, 1))
            input_data = np.expand_dims(input_data, 0)
            
            print(f"[OK] Input prepared - shape: {input_data.shape}")
            
            # Run inference
            self.infer_request.infer([input_data])
            output = self.infer_request.get_output_tensor(0).data
            
            print(f"[MODEL] Output shape: {output.shape}")
            
            # Parse YOLO26 output format: [1, 300, 6]
            # Each detection: [x1, y1, x2, y2, confidence, class_id]
            # Output is already in pixel coordinates (0-640) and post-processed
            
            raw_detections = []  # Collect all detections first
            
            # Extract predictions from output shape [1, 300, 6]
            if len(output.shape) == 3:
                predictions = output[0]  # [300, 6]
            else:
                predictions = output  # Already [300, 6]
            
            print(f"[DATA] Predictions shape: {predictions.shape}")
            
            # First pass: Collect all predictions above confidence threshold
            if predictions.shape[0] > 0:
                for pred in predictions:
                    try:
                        # Extract bbox coordinates and confidence
                        # Format: [x1, y1, x2, y2, confidence, class_id]
                        x1_norm, y1_norm, x2_norm, y2_norm = pred[:4]
                        class_conf = pred[4]
                        class_id = int(pred[5])
                        
                        # Filter by confidence threshold
                        if class_conf < confidence_threshold:
                            continue
                        
                        # Coordinates are in normalized 0-640 pixel space
                        # Scale to actual image dimensions
                        scale_x = original_width / self.model_width
                        scale_y = original_height / self.model_height
                        
                        x1 = int(max(0, x1_norm * scale_x))
                        y1 = int(max(0, y1_norm * scale_y))
                        x2 = int(min(original_width, x2_norm * scale_x))
                        y2 = int(min(original_height, y2_norm * scale_y))
                        
                        # Skip invalid boxes or very small detections
                        if x2 <= x1 or y2 <= y1:
                            continue
                        
                        # Get class name
                        class_name = self.class_names.get(class_id, 'unknown')
                        
                        raw_detections.append({
                            "class": class_name,
                            "class_id": class_id,
                            "confidence": float(class_conf),
                            "bbox": [x1, y1, x2, y2]
                        })
                    except Exception as e:
                        continue
            
            print(f"[NMS] Found {len(raw_detections)} raw detections before NMS")
            
            # Second pass: Apply NMS to remove overlapping boxes
            detections = self._non_max_suppression(raw_detections, nms_threshold=0.45)
            
            print(f"[FILTER] After NMS: {len(detections)} detections")
            
            # Draw detections on image
            for det in detections:
                x1, y1, x2, y2 = det['bbox']
                class_name = det['class']
                confidence = det['confidence']
                
                print(f"   [+] {class_name} - Confidence: {confidence:.2%}")
                
                # Draw bounding box (Green for Healthy, Red for diseases)
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
            
            # Format output detections
            output_detections = [{
                "class": d['class'],
                "confidence": round(d['confidence'], 3),
                "bbox": d['bbox']
            } for d in detections]
            
            print(f"[OK] Successfully processed with {len(output_detections)} final detections")
            return {"detections": output_detections, "image": image}
            
        except Exception as e:
            print(f"[ERROR] Prediction error: {e}")
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
                'fertilizer': 'Use slow-release potassium-rich fertilizer (NPK 10:10:20) only AFTER disease control. Avoid high-nitrogen fertilizers.',
                'treatment': 'PRIMARY: Metalaxyl-based fungicide - spray 0.2% solution into central bud every 7-10 days for 3-4 applications (most effective). ALTERNATIVE: Copper-based (Bordeaux 1% or copper hydroxide 0.5%) sprayed every 2 weeks. SURGICAL: Remove affected spear leaf if only buds infected; apply fungicide paste to cuts. COMBO: Start metalaxyl (weeks 1-3), then copper (weeks 4+).',
                'prevention': 'CRITICAL: Ensure excellent soil drainage (Phytophthora thrives in wet soil). Remove infected fronds immediately. Maintain 8-9m spacing for air circulation. Avoid crown injuries. Inspect central bud weekly during rainy season. Monitor for yellowing/browning.'
            },
            'caterpillars': {
                'fertilizer': 'Use NPK 10:10:10 balanced fertilizer monthly to boost tree vigor and recovery.',
                'treatment': 'PRIMARY: Spray Bacillus thuringiensis (Bt) at 1.5-2g per liter every 7 days for 3-4 weeks (organic option). ALTERNATIVE: Spinosad (0.5%) every 5-7 days or Phosphine (0.05%) for severe infestations. MANUAL: Handpick affected leaves in early morning when caterpillars are most active.',
                'prevention': 'Monitor leaves regularly for egg clusters and caterpillar droppings. Remove heavily infested fronds. Encourage natural predators like birds and parasitic wasps. Maintain tree vigor.'
            },
            'cercospora': {
                'fertilizer': 'Apply potassium-rich fertilizer (NPK 8:8:16) to improve leaf resistance. Every 4 weeks during treatment.',
                'treatment': 'PRIMARY: Spray Chlorothalonil (0.75%) weekly for 4-5 weeks. ALTERNATIVE: Copper-based fungicide (Bordeaux 1% or copper hydroxide 0.5%) every 10 days. COMBINATION: Alternate Chlorothalonil and Mancozeb (0.2%) weekly to prevent fungicide resistance. Prune ALL heavily infected leaves and burn them (do not compost).',
                'prevention': 'Remove fallen leaves immediately. Space trees 8-9m apart for air circulation. Water at soil level, avoid wetting leaves. Ensure good drainage. Sanitize pruning tools with bleach between cuts.'
            },
            'drying of leaflets': {
                'fertilizer': 'CRITICAL: Apply Magnesium sulfate (Epsom salt) 1-2kg per tree monthly. NPK 12:8:20 with micronutrients (Zn, Fe, B) weekly via foliar spray for 6-8 weeks. Soil application every 3 months.',
                'treatment': 'Foliar spray with potassium nitrate (2%) or potassium chloride (3%) twice weekly for 6 weeks. Include micronutrient complex (boron, zinc, iron). Systemic fungicide Carbendazim (0.1%) if fungal secondary infection suspected.',
                'prevention': 'Water deeply 2-3 times weekly during dry season (morning and evening). Mulch 10cm around base to retain soil moisture. Use drip irrigation for consistent water delivery. Monitor soil pH (coconut prefers 5.5-8.0). Improve soil with compost/coconut husk.'
            },
            'healthy': {
                'fertilizer': 'Apply balanced NPK 12:12:12 every 3 months. Use phosphate rich during flowering season.',
                'treatment': 'No treatment needed. Continue regular monitoring.',
                'prevention': 'Maintain regular fertilization. Water consistently. Remove dead leaves. Monitor for pests.'
            },
            'pestaltiopsis': {
                'fertilizer': 'Use NPK 10:10:20 with Zinc supplement (5-10kg Zn per hectare annually). Apply monthly during treatment to strengthen tree immunity.',
                'treatment': 'PRIMARY: Copper-based fungicide (Bordeaux mixture 1% or copper hydroxide 0.5%) sprayed every 10 days for 6-8 weeks. ALTERNATIVE: Azoxystrobin (0.1%) or Carbendazim (0.1%) every 7-10 days. CRITICAL: Remove ALL infected/dead fronds and burn them (not compost). Apply fungicide paste to all cut surfaces.',
                'prevention': 'Prune dead/weak branches regularly to improve air circulation. Ensure excellent soil drainage. Remove fallen diseased fronds immediately. Maintain tree vigor with consistent fertilization. Space trees properly. Avoid wounding trees (main infection route).'
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