import cv2
import numpy as np
import os
from pathlib import Path
import pickle
from datetime import datetime
import json

# Keep Ultralytics from touching the user's profile directory on Windows.
_ultralytics_config_dir = Path(__file__).resolve().parent / ".ultralytics"
_ultralytics_config_dir.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("YOLO_CONFIG_DIR", str(_ultralytics_config_dir))

try:
    from ultralytics import YOLO
    ULTRALYTICS_AVAILABLE = True
except Exception:
    YOLO = None
    ULTRALYTICS_AVAILABLE = False

from openvino import Core

class CoconutDiseaseDetector:
    def __init__(self):
        """Initialize the required local YOLO26 v6 weights for inference."""
        current_dir = Path(__file__).parent
        self.current_dir = current_dir
        self.backend = "ultralytics"
        self.active_model_path = None

        # Default class names from the existing dataset
        self.class_names = {
            0: "Caterpillars",
            1: "Cercospora",
            2: "Drying of Leaflets",
            3: "Healthy",
            4: "Pestalotiopsis",
            5: "bud root",
        }

        model_pt = current_dir / "weights.pt"
        if not model_pt.is_file():
            raise FileNotFoundError(
                f"Required YOLO26 v6 model weights were not found: {model_pt}"
            )
        if not ULTRALYTICS_AVAILABLE:
            raise RuntimeError(
                "Ultralytics is required to run the local YOLO26 v6 weights.pt model. "
                "Install the dependencies and restart the application."
            )

        try:
            print(f"[OK] Loading required YOLO26 v6 model from {model_pt}")
            self.yolo_model = YOLO(str(model_pt))
            self.active_model_path = model_pt
            self.model_height = 640
            self.model_width = 640
            self.class_names = self._normalize_class_names(
                getattr(self.yolo_model, "names", self.class_names)
            )
            print("[OK] YOLO26 v6 weights loaded successfully")
        except Exception as exc:
            raise RuntimeError(
                f"Unable to load the required YOLO26 v6 weights from {model_pt}."
            ) from exc
        
        # ──── Load ML Recommendation Models ────
        print("[OK] Loading ML recommendation classifiers...")
        self.ml_models_loaded = False
        try:
            models_dir = current_dir / "models" / "ml_recommendations"
            
            # Load classifiers
            with open(models_dir / "treatment_clf.pkl", "rb") as f:
                self.treatment_clf = pickle.load(f)
            with open(models_dir / "fertilizer_clf.pkl", "rb") as f:
                self.fertilizer_clf = pickle.load(f)
            with open(models_dir / "preventive_clf.pkl", "rb") as f:
                self.preventive_clf = pickle.load(f)
            
            # Load encoders
            with open(models_dir / "treatment_encoder.pkl", "rb") as f:
                self.treatment_encoder = pickle.load(f)
            
            # Feature engineer is optional (has external dependency)
            self.feature_engineer = None
            try:
                with open(models_dir / "feature_engineer.pkl", "rb") as f:
                    self.feature_engineer = pickle.load(f)
                print("[OK] Feature engineer loaded")
            except Exception as e:
                print(f"[INFO] Feature engineer not available ({type(e).__name__}), using built-in features")
            
            self.ml_models_loaded = True
            print("[OK] ML recommendation models loaded successfully")
        except Exception as e:
            print(f"[WARNING] Could not load ML models - falling back to hardcoded recommendations: {e}")
            self.treatment_clf = None
            self.fertilizer_clf = None
            self.preventive_clf = None
            self.treatment_encoder = None
        
        # ──── Recommendation Mapping Tables ────
        # These map ML predictions (indices) to readable recommendations
        self.treatment_options = {
            0: "Bacillus thuringiensis (Bt) - Organic, safe for beneficial insects",
            1: "Metalaxyl-based fungicide - Systemic control for fungal diseases",
            2: "Copper-based fungicide (Bordeaux mixture) - Broad-spectrum fungal control",
            3: "Spinosad - Organic insecticide for caterpillars",
            4: "Chlorothalonil - Protective fungicide for leaf diseases",
            5: "Carbendazim - Systemic fungicide for comprehensive coverage",
            6: "Mechanical removal - Hand-pick affected parts and burn",
            7: "Combination therapy - Rotate fungicides weekly"
        }
        
        self.fertilizer_options = {
            0: "NPK 12:12:12 (Balanced) - General maintenance",
            1: "NPK 10:10:20 (High K) - Disease recovery and stress tolerance",
            2: "NPK 8:8:16 + Potassium - Leaf disease resistance",
            3: "NPK 10:10:10 + Micronutrients - Caterpillar damage recovery",
            4: "Magnesium sulfate (Epsom salt) - Nutrient deficiency correction",
            5: "NPK 12:8:20 with Zn, Fe, B - Comprehensive micronutrient support"
        }
        
        self.preventive_options = {
            0: "Remove fallen diseased leaves immediately and burn them",
            1: "Maintain 8-9m tree spacing for air circulation",
            2: "Monitor central bud and leaf undersides weekly",
            3: "Ensure excellent soil drainage - avoid waterlogging",
            4: "Sanitize pruning tools between cuts with bleach solution",
            5: "Encourage natural predators (birds, wasps for pest control)",
            6: "Water at soil level, avoid wetting foliage",
            7: "Prune dead/weak branches to improve canopy health",
            8: "Use drip irrigation for consistent moisture delivery",
            9: "Apply mulch (10cm) around base to retain soil moisture"
        }

    def _normalize_class_names(self, names) -> dict[int, str]:
        """Convert Ultralytics class metadata into a simple integer->label map."""
        if isinstance(names, dict):
            normalized = {}
            for key, value in names.items():
                try:
                    normalized[int(key)] = str(value)
                except Exception:
                    continue
            return normalized or self.class_names
        if isinstance(names, (list, tuple)):
            return {idx: str(name) for idx, name in enumerate(names)}
        return self.class_names

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
        Real-time inference using the active detector backend
        
        Args:
            image: Input image as numpy array (BGR format)
            conf: Confidence threshold (0-100, default 50 for better filtering)
        
        Returns:
            dict with detections and annotated image
        """
        try:
            print(f"[PREDICT] Starting {self.backend} prediction with confidence threshold: {conf}")
            if self.backend == "ultralytics":
                return self._predict_with_ultralytics(image, conf)
            return self._predict_with_openvino(image, conf)
        except Exception as e:
            print(f"[ERROR] Prediction error: {e}")
            import traceback
            traceback.print_exc()
            return {
                "detections": [],
                "image": image
            }

    def _predict_with_ultralytics(self, image: np.ndarray, conf: float = 50) -> dict:
        """Run inference directly on weights.pt using Ultralytics."""
        confidence_threshold = conf / 100.0 if conf > 1 else conf
        original_height, original_width = image.shape[:2]
        image_total_area = original_width * original_height
        annotated_image = image.copy()

        results = self.yolo_model.predict(source=image, imgsz=640, conf=confidence_threshold, verbose=False)
        result = results[0] if results else None

        raw_detections = []
        total_disease_bbox_area = 0
        primary_class_id = 3

        if result is not None and getattr(result, "boxes", None) is not None:
            xyxy = result.boxes.xyxy.cpu().numpy() if len(result.boxes) else np.empty((0, 4))
            confs = result.boxes.conf.cpu().numpy() if len(result.boxes) else np.empty((0,))
            clss = result.boxes.cls.cpu().numpy() if len(result.boxes) else np.empty((0,))

            for box, class_conf, class_id in zip(xyxy, confs, clss):
                try:
                    class_id = int(class_id)
                    class_conf = float(class_conf)
                    if class_conf < confidence_threshold:
                        continue

                    x1, y1, x2, y2 = [int(max(0, value)) for value in box]
                    x2 = min(original_width, x2)
                    y2 = min(original_height, y2)
                    if x2 <= x1 or y2 <= y1:
                        continue

                    if class_id != 3:
                        total_disease_bbox_area += (x2 - x1) * (y2 - y1)
                        primary_class_id = class_id

                    class_name = self.class_names.get(class_id, "unknown")
                    raw_detections.append({
                        "class": class_name,
                        "class_id": class_id,
                        "confidence": class_conf,
                        "bbox": [x1, y1, x2, y2]
                    })
                except Exception:
                    continue

        print(f"[NMS] Found {len(raw_detections)} raw detections before NMS")
        detections = self._non_max_suppression(raw_detections, nms_threshold=0.45)
        print(f"[FILTER] After NMS: {len(detections)} detections")

        for det in detections:
            x1, y1, x2, y2 = det['bbox']
            class_name = det['class']
            confidence = det['confidence']

            print(f"   [+] {class_name} - Confidence: {confidence:.2%}")

            color = (0, 200, 100) if class_name.lower() == "healthy" else (0, 60, 220)
            cv2.rectangle(annotated_image, (x1, y1), (x2, y2), color, 3)

            text = f"{class_name} {confidence:.2%}"
            font = cv2.FONT_HERSHEY_SIMPLEX
            font_scale = 0.7
            thickness = 2
            text_size = cv2.getTextSize(text, font, font_scale, thickness)[0]

            text_x = x1
            text_y = max(30, y1 - 10)
            cv2.rectangle(
                annotated_image,
                (text_x - 3, text_y - text_size[1] - 6),
                (text_x + text_size[0] + 3, text_y + 3),
                color,
                -1
            )
            cv2.putText(annotated_image, text, (text_x, text_y), font, font_scale, (255, 255, 255), thickness)

        output_detections = [{
            "class": d['class'],
            "confidence": round(d['confidence'], 3),
            "bbox": d['bbox']
        } for d in detections]

        print(f"[OK] Successfully processed with {len(output_detections)} final detections")

        return {
            "detections": output_detections,
            "image": annotated_image
        }

    def _predict_with_openvino(self, image: np.ndarray, conf: float = 50) -> dict:
        """Run inference using the OpenVINO export."""
        print(f"[PREDICT] Starting OpenVINO prediction with confidence threshold: {conf}")
        # Normalize confidence to 0-1 range if needed
        confidence_threshold = conf / 100.0 if conf > 1 else conf

        # Store original image dimensions
        original_height, original_width = image.shape[:2]
        image_total_area = original_width * original_height  # For disease severity calculation

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
                except Exception:
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

        return {
            "detections": output_detections,
            "image": image
        }

    def get_fertilizer_recommendation(self, disease_name: str, confidence: float, gps_data: dict = None, user_location: dict = None) -> dict:
        """
        Provides farmer-friendly recommendations based on detected disease.
        Uses ML-trained classifiers if available, falls back to hardcoded recommendations.
        Includes GPS location with automatic fallback to laptop/browser location.
        
        Args:
            disease_name: Name of detected disease
            confidence: Confidence level (0-1 or 0-100)
            gps_data: Dict with 'lat', 'lng' from drone EXIF (optional)
            user_location: Dict with 'lat', 'lng' from laptop/browser location (fallback)
        
        Returns:
            dict with Fertilizer, Treatment, Prevention recommendations and location data
        """
        
        # Normalize confidence to 0-100 range
        if confidence > 1:
            conf_percent = confidence
        else:
            conf_percent = confidence * 100
        
        # ──── GPS Handling with Fallback (Always Provides Location) ────
        # Default to Davao region center (always available as fallback)
        location_info = {
            'source': 'davao_default',
            'latitude': 7.0731,
            'longitude': 125.6123,
            'accuracy': 50000
        }
        
        if gps_data and (gps_data.get('lat') or gps_data.get('latitude')):
            # Use drone GPS (EXIF) - highest priority
            location_info['latitude'] = gps_data.get('lat') or gps_data.get('latitude')
            location_info['longitude'] = gps_data.get('lng') or gps_data.get('longitude')
            location_info['accuracy'] = gps_data.get('accuracy', 5.0)
            location_info['source'] = 'drone_exif'
            print(f"[GPS] Using drone EXIF coordinates: {location_info['latitude']:.6f}, {location_info['longitude']:.6f}")
        
        elif user_location and (user_location.get('lat') or user_location.get('latitude')):
            # Fallback to laptop/browser location - second priority
            location_info['latitude'] = user_location.get('lat') or user_location.get('latitude')
            location_info['longitude'] = user_location.get('lng') or user_location.get('longitude')
            location_info['accuracy'] = user_location.get('accuracy', 10.0)
            location_info['source'] = 'browser_geolocation'
            print(f"[GPS] Fallback to browser location: {location_info['latitude']:.6f}, {location_info['longitude']:.6f}")
        
        else:
            print(f"[GPS] No drone/browser GPS - using default Davao coordinates")
        
        # ──── ML Model Predictions ────
        # DISABLED: ML models are not well-tuned yet, using hardcoded recommendations instead
        if False and self.ml_models_loaded and self.treatment_clf is not None:
            try:
                print(f"[ML] Using trained ML classifiers for recommendations")
                
                # Infer severity from confidence
                if conf_percent >= 85:
                    severity = "high"
                elif conf_percent >= 70:
                    severity = "medium"
                else:
                    severity = "low"
                
                # Create features for prediction
                features = self._create_features(disease_name, conf_percent, severity)
                features_2d = features.reshape(1, -1)
                
                # Make predictions with MultiOutputClassifier
                treatment_pred = self.treatment_clf.predict(features_2d)[0]
                fertilizer_pred = self.fertilizer_clf.predict(features_2d)[0]
                preventive_pred = self.preventive_clf.predict(features_2d)[0]
                
                print(f"[ML] Predictions - Treatment idx: {treatment_pred}, Fertilizer idx: {fertilizer_pred}, Preventive idx: {preventive_pred}")
                
                # Decode predictions using mapping tables
                treatment = self.treatment_options.get(
                    int(treatment_pred[0]) if isinstance(treatment_pred, np.ndarray) else int(treatment_pred),
                    "Consult agricultural expert for specialized treatment"
                )
                
                fertilizer_idx = int(fertilizer_pred[0]) if isinstance(fertilizer_pred, np.ndarray) else int(fertilizer_pred)
                fertilizer = self.fertilizer_options.get(fertilizer_idx, "Balanced NPK 12:12:12 fertilizer")
                
                # Collect preventive measures
                preventive_list = []
                if isinstance(preventive_pred, np.ndarray):
                    for p_idx in preventive_pred:
                        try:
                            p_int = int(p_idx)
                            if p_int in self.preventive_options:
                                preventive_list.append(self.preventive_options[p_int])
                        except:
                            pass
                
                if not preventive_list:
                    preventive_list = [self.preventive_options.get(0, "Regular monitoring recommended")]
                
                confidence_note = ""
                if conf_percent >= 85:
                    confidence_note = " (High confidence - ML model prediction)"
                elif conf_percent >= 70:
                    confidence_note = " (Good confidence - ML model prediction)"
                else:
                    confidence_note = " (Lower confidence - consult agricultural expert)"
                
                return {
                    'disease': disease_name,
                    'confidence': round(conf_percent, 2),
                    'fertilizer': fertilizer,
                    'treatment': treatment,
                    'prevention': preventive_list,
                    'model': 'ML-Trained',
                    'location': location_info,
                    'note': f"ML-predicted recommendations{confidence_note}. Location: {location_info['source'].replace('_', ' ')}"
                }
            
            except Exception as e:
                print(f"[WARNING] ML prediction failed: {e}, falling back to hardcoded recommendations")
                import traceback
                traceback.print_exc()
            return {
                "detections": [],
                "image": image
            }
        # Fallback to hardcoded recommendations
        print(f"[FALLBACK] Using hardcoded recommendations")
        
        recommendations = {
            'bud root': {
                'fertilizer': 'Do not use fertilizer as a cure. After removing active rot, restore palm vigor with organic matter and a soil-test-based coconut fertilizer program; avoid excess nitrogen on stressed palms.',
                'treatment': 'Treat as urgent Phytophthora bud rot: contact the Philippine Coconut Authority or local agricultural office. Remove and safely destroy badly rotted tissue as directed. A trained applicator may use a locally registered phosphonate, metalaxyl, or copper hydroxide product directed to leaf axils/cankers strictly at the product label rate.',
                'prevention': 'Improve drainage and air flow; clean the crown before the rainy season; remove and destroy infected nuts, fronds, and nearby alternate-host material; inspect nearby palms, especially within about 20 m, and avoid wounding the crown.'
            },
            'caterpillars': {
                'fertilizer': 'Use NPK 10:10:10 balanced fertilizer monthly to boost tree vigor and recovery.',
                'treatment': 'PRIMARY: Spray Bacillus thuringiensis (Bt) at 1.5-2g per liter every 7 days for 3-4 weeks (organic option). ALTERNATIVE: Spinosad (0.5%) every 5-7 days or Phosphine (0.05%) for severe infestations. MANUAL: Handpick affected leaves in early morning when caterpillars are most active.',
                'prevention': 'Monitor leaves regularly for egg clusters and caterpillar droppings. Remove heavily infested fronds. Encourage natural predators like birds and parasitic wasps. Maintain tree vigor.'
            },
            'cercospora': {
                'fertilizer': 'Fertilize from a soil or leaf analysis. Maintain adequate potassium and chloride nutrition—PCA notes that KCl can increase coconut resistance to leaf-spot diseases. Do not apply fertilizer to replace sanitation or fungicide treatment.',
                'treatment': 'Prune only heavily affected fronds and remove them from the farm. Have the diagnosis confirmed, then use only a fungicide registered locally for coconut leaf spot, following the label for product, rate, interval, protective equipment, and pre-harvest restrictions.',
                'prevention': 'Maintain clean culture: remove diseased fallen leaves, improve spacing and airflow, avoid prolonged leaf wetness where possible, keep drainage working, and disinfect pruning tools between palms.'
            },
            'drying of leaflets': {
                'fertilizer': 'Check soil and leaf nutrient status before fertilizing. Correct documented potassium, magnesium, or micronutrient deficiencies with a locally recommended coconut fertilizer program; do not apply high fertilizer rates to a severely stressed palm.',
                'treatment': 'Confirm whether the symptom is leaf rot, nutrient stress, drought, or pest injury before spraying. Remove only dead or heavily diseased fronds and use a locally registered fungicide only when a crop specialist confirms a fungal leaf disease.',
                'prevention': 'Maintain good drainage, mulch and water during dry periods, avoid crown and leaf injury, remove diseased debris, and monitor palms regularly so leaf symptoms are diagnosed early.'
            },
            'leaf rot': {
                'fertilizer': 'Check soil and leaf nutrient status before fertilizing. Correct documented potassium, magnesium, or micronutrient deficiencies with a locally recommended coconut fertilizer program; do not apply high fertilizer rates to a severely stressed palm.',
                'treatment': 'Confirm whether the symptom is leaf rot, nutrient stress, drought, or pest injury before spraying. Remove only dead or heavily diseased fronds and use a locally registered fungicide only when a crop specialist confirms a fungal leaf disease.',
                'prevention': 'Maintain good drainage, mulch and water during dry periods, avoid crown and leaf injury, remove diseased debris, and monitor palms regularly so leaf symptoms are diagnosed early.'
            },
            'healthy': {
                'fertilizer': 'Apply balanced NPK 12:12:12 every 3 months. Use phosphate rich during flowering season.',
                'treatment': 'No treatment needed. Continue regular monitoring.',
                'prevention': 'Maintain regular fertilization. Water consistently. Remove dead leaves. Monitor for pests.'
            },
            'pestalotiopsis': {
                'fertilizer': 'Use a soil-test-based coconut fertilizer program. Keep potassium and chloride adequate; PCA reports KCl nutrition can improve resistance to coconut fungal leaf spots. Fertilizer supports recovery but does not cure the infection.',
                'treatment': 'Practice clean culture: prune and remove severely infected leaves and debris. After confirmation, apply only a fungicide registered locally for coconut fungal leaf spot, following its label exactly. Seek local extension advice before treating tall palms or widespread disease.',
                'prevention': 'Use clean planting material, remove infected leaves promptly, keep nursery and field sanitation high, improve airflow and drainage, minimize leaf wounds, and monitor frequently in wet weather.'
            }
        }
        
        # Normalize disease name for lookup
        disease_lower = disease_name.lower().strip().replace('_', ' ')
        rec = recommendations.get(disease_lower, recommendations['healthy'])
        
        # Log the lookup for debugging
        print(f"[RECOMMENDATION] Disease: '{disease_name}' -> Normalized: '{disease_lower}' -> Found: {disease_lower in recommendations}")
        
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
            'prevention': [rec['prevention']] if isinstance(rec['prevention'], str) else rec['prevention'],
            'model': 'Hardcoded',
            'location': location_info,
            'note': f"Davao region recommendations{confidence_note}. Location: {location_info['source'].replace('_', ' ')}"
        }
    
    def _create_features(self, disease_name: str, confidence: float, severity: str) -> np.ndarray:
        """
        Create feature vector for ML model prediction.
        Based on disease, confidence, and severity.
        
        Args:
            disease_name: Name of detected disease
            confidence: Confidence score (0-100)
            severity: Severity level (low, medium, high)
        
        Returns:
            Feature vector as numpy array
        """
        # Map disease to numeric code
        disease_map = {
            'caterpillars': 0,
            'cercospora': 1,
            'drying of leaflets': 2,
            'healthy': 3,
            'pestalotiopsis': 4,
            'bud root': 5
        }
        disease_code = disease_map.get(disease_name.lower().strip().replace('_', ' '), 3)
        
        # Map severity to numeric code
        severity_map = {'low': 0, 'medium': 1, 'high': 2}
        severity_code = severity_map.get(severity.lower(), 0)
        
        # Normalize confidence to 0-1
        conf_normalized = min(max(confidence / 100.0, 0), 1)
        
        # Create 13 features (matching the training data structure)
        # These are engineered features based on disease characteristics
        features = [
            disease_code,                    # 0: disease numeric code
            conf_normalized,                 # 1: normalized confidence
            severity_code,                   # 2: severity code
            conf_normalized * severity_code, # 3: confidence * severity interaction
            1.0 if severity_code == 2 else 0.0,  # 4: is_high_severity
            1.0 if confidence < 0.7 else 0.0,    # 5: is_low_confidence
            int(datetime.now().month),       # 6: current month (seasonal)
            int(datetime.now().day) / 31.0,  # 7: day of month (normalized)
            1.0 if disease_code in [0, 4] else 0.0,  # 8: is_pest_disease
            1.0 if disease_code in [1, 2, 5] else 0.0,  # 9: is_fungal_disease
            confidence / 100.0,              # 10: confidence (0-1)
            severity_code / 2.0,             # 11: normalized severity
            1.0 if disease_code == 3 else 0.0,  # 12: is_healthy
        ]
        
        return np.array(features, dtype=np.float32)


# Singleton — loaded once on startup
detector = CoconutDiseaseDetector()
