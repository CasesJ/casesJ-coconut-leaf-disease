import cv2
import numpy as np
import os
from pathlib import Path
import re

from app.core.config import INFERENCE_BACKEND, OPENVINO_MODEL_DIR
from app.domain.disease import normalize_current_disease_label
from app.infrastructure.ml.roboflow import RoboflowWorkflowDetector

# Keep Ultralytics from touching the user's profile directory on Windows.
_ultralytics_config_dir = Path(__file__).resolve().parent / ".ultralytics"
_ultralytics_config_dir.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("YOLO_CONFIG_DIR", str(_ultralytics_config_dir))

if INFERENCE_BACKEND == "ultralytics":
    try:
        from ultralytics import YOLO
        ULTRALYTICS_AVAILABLE = True
    except Exception:
        YOLO = None
        ULTRALYTICS_AVAILABLE = False
else:
    # Do not import Ultralytics/PyTorch in the Render OpenVINO process.
    YOLO = None
    ULTRALYTICS_AVAILABLE = False

# These labels belong to older model datasets and must not be returned by the
# current application.  A model trained with Stem Bleeding examples is required
# before the detector itself can produce that class.
RETIRED_DETECTION_CLASSES = frozenset({"cercospora", "leaf rot"})

class CoconutDiseaseDetector:
    def __init__(self):
        """Initialize the required local YOLO26 v6 weights for inference."""
        current_dir = Path(__file__).parent
        self.current_dir = current_dir
        self.backend = INFERENCE_BACKEND
        self.active_model_path = None

        # Default class names from the existing dataset
        self.class_names = {
            0: "Caterpillars",
            1: "Cercospora",
            2: "Drying of Leaflets",
            3: "Healthy",
            4: "Grey Leaf Spot",
            5: "bud rot",
        }

        if self.backend == "roboflow":
            # Keep this compatibility wrapper so recommendation code and
            # legacy imports continue to refer to one detector singleton.
            self._hosted = RoboflowWorkflowDetector()
            self.backend = self._hosted.backend
            self.class_names = self._hosted.class_names
            return

        self.model_height = 640
        self.model_width = 640
        if self.backend == "ultralytics":
            model_pt = current_dir / "weights.pt"
            if not model_pt.is_file():
                raise FileNotFoundError(f"Required YOLO26 v6 model weights were not found: {model_pt}")
            if not ULTRALYTICS_AVAILABLE:
                raise RuntimeError("Ultralytics is required to run the local YOLO26 v6 weights.pt model.")
            try:
                print(f"[OK] Loading required YOLO26 v6 model from {model_pt}")
                self.yolo_model = YOLO(str(model_pt))
                self.active_model_path = model_pt
                self.class_names = self._normalize_class_names(getattr(self.yolo_model, "names", self.class_names))
                print("[OK] YOLO26 v6 weights loaded successfully")
            except Exception as exc:
                raise RuntimeError(f"Unable to load required YOLO26 v6 weights from {model_pt}.") from exc
        else:
            self._load_openvino_model()
        
    def _load_openvino_model(self) -> None:
        """Load only the reviewed dynamic IR exported from the current weights.pt."""
        xml_path = OPENVINO_MODEL_DIR / "weights.xml"
        bin_path = OPENVINO_MODEL_DIR / "weights.bin"
        metadata_path = OPENVINO_MODEL_DIR / "metadata.yaml"
        if not (xml_path.is_file() and bin_path.is_file() and metadata_path.is_file()):
            raise FileNotFoundError(f"Required OpenVINO model files are missing in {OPENVINO_MODEL_DIR}")
        metadata = metadata_path.read_text(encoding="utf-8")
        if "description: Ultralytics YOLO26s" not in metadata or "dynamic: true" not in metadata:
            raise RuntimeError("OpenVINO model provenance check failed; expected the reviewed dynamic YOLO26s export")
        names = {int(key): value.strip() for key, value in re.findall(r"^  (\d+):\s*(.+)$", metadata, re.MULTILINE)}
        # This validates the checked-in legacy OpenVINO export only.  Render
        # uses Roboflow and does not initialise this backend.
        expected = {0: "bud root", 1: "healthy", 2: "pestalotiopsis", 3: "stem bleeding"}
        if names != expected:
            raise RuntimeError("OpenVINO model class mapping does not match the current weights.pt export")
        try:
            from openvino import Core
            core = Core()
            compiled = core.compile_model(core.read_model(xml_path), "CPU")
            self.infer_request = compiled.create_infer_request()
            self.active_model_path = xml_path
            self.class_names = names
            print(f"[OK] Loaded reviewed dynamic OpenVINO YOLO26s model from {xml_path}")
        except Exception as exc:
            raise RuntimeError(f"Unable to load required OpenVINO model from {xml_path}.") from exc

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

    def predict(self, image: np.ndarray, conf: float = 50, *, annotate: bool = True) -> dict:
        """
        Real-time inference using the active detector backend
        
        Args:
            image: Input image as numpy array (BGR format)
            conf: Confidence threshold (0-100, default 50 for better filtering)
            annotate: Draw detection boxes and labels on the returned image.
        
        Returns:
            dict with detections, annotated image, and an inference status.
            A successful empty detection list is a genuine no-detection
            result. A backend error returns ``inference_status="failed"`` so
            callers never mistake it for a valid analysis.
        """
        try:
            print(f"[PREDICT] Starting {self.backend} prediction with confidence threshold: {conf}")
            if self.backend == "roboflow-serverless":
                return self._hosted.predict(image, conf=conf, annotate=annotate)
            if self.backend == "ultralytics":
                result = self._predict_with_ultralytics(image, conf, annotate=annotate)
            else:
                result = self._predict_with_openvino(image, conf, annotate=annotate)
            result["inference_status"] = "success"
            return result
        except Exception as error:
            print(f"[ERROR] Prediction error: {error}")
            import traceback
            traceback.print_exc()
            return {
                "detections": [],
                "image": image,
                "inference_status": "failed",
                "inference_error": "model_execution_failed",
            }

    def model_info(self) -> dict:
        if self.backend == "roboflow-serverless":
            return self._hosted.model_info()
        return {
            "name": "YOLO26 v6",
            "backend": self.backend,
            "weights": self.active_model_path.name if self.active_model_path else None,
            "classes": self.class_names,
        }

    def _predict_with_ultralytics(self, image: np.ndarray, conf: float = 50, *, annotate: bool = True) -> dict:
        """Run inference directly on weights.pt using Ultralytics."""
        confidence_threshold = conf / 100.0 if conf > 1 else conf
        original_height, original_width = image.shape[:2]
        image_total_area = original_width * original_height
        annotated_image = image.copy() if annotate else image
        # Use a fixed display-space annotation size. Scaling these values from
        # source pixels makes boxes overwhelm high-resolution uploads.
        box_thickness = 4
        font_scale = 0.9
        text_thickness = 2
        text_padding = 8

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

                    class_name = self.class_names.get(class_id, "unknown")
                    if class_name.lower().strip().replace('_', ' ') in RETIRED_DETECTION_CLASSES:
                        continue

                    if class_id != 3:
                        total_disease_bbox_area += (x2 - x1) * (y2 - y1)
                        primary_class_id = class_id

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

        for det in detections if annotate else ():
            x1, y1, x2, y2 = det['bbox']
            class_name = det['class']
            confidence = det['confidence']

            print(f"   [+] {class_name} - Confidence: {confidence:.2%}")

            color = (0, 200, 100) if class_name.lower() == "healthy" else (0, 60, 220)
            cv2.rectangle(annotated_image, (x1, y1), (x2, y2), color, box_thickness)

            text = f"{class_name} {confidence:.2%}"
            font = cv2.FONT_HERSHEY_SIMPLEX
            text_size = cv2.getTextSize(text, font, font_scale, text_thickness)[0]

            text_x = x1
            text_y = max(text_size[1] + text_padding, y1 - text_padding)
            cv2.rectangle(
                annotated_image,
                (text_x - text_padding, text_y - text_size[1] - text_padding),
                (text_x + text_size[0] + text_padding, text_y + text_padding),
                color,
                -1
            )
            cv2.putText(annotated_image, text, (text_x, text_y), font, font_scale, (255, 255, 255), text_thickness)

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

    def _predict_with_openvino(self, image: np.ndarray, conf: float = 50, *, annotate: bool = True) -> dict:
        """Run the reviewed dynamic IR with the exact single-image Ultralytics transform."""
        threshold = conf / 100.0 if conf > 1 else conf
        original_height, original_width = image.shape[:2]
        gain = min(self.model_height / original_height, self.model_width / original_width)
        resized_size = (round(original_width * gain), round(original_height * gain))
        pad_x = (self.model_width - resized_size[0]) % 32
        pad_y = (self.model_height - resized_size[1]) % 32
        transformed = cv2.resize(image, resized_size, interpolation=cv2.INTER_LINEAR) if resized_size != (original_width, original_height) else image
        left, right = round(pad_x / 2 - 0.1), round(pad_x / 2 + 0.1)
        top, bottom = round(pad_y / 2 - 0.1), round(pad_y / 2 + 0.1)
        transformed = cv2.copyMakeBorder(transformed, top, bottom, left, right, cv2.BORDER_CONSTANT, value=(114, 114, 114))
        input_data = np.ascontiguousarray(transformed[..., ::-1].transpose(2, 0, 1))[None].astype(np.float32) / 255.0
        self.infer_request.infer([input_data])
        predictions = self.infer_request.get_output_tensor(0).data[0]
        raw_detections = []
        for x1, y1, x2, y2, score, class_value in predictions:
            if float(score) <= threshold:  # Matches Ultralytics end-to-end confidence filtering.
                continue
            class_id = int(class_value)
            class_name = self.class_names.get(class_id, "unknown")
            if class_name.lower().strip().replace("_", " ") in RETIRED_DETECTION_CLASSES:
                continue
            x1, x2 = (x1 - left) / gain, (x2 - left) / gain
            y1, y2 = (y1 - top) / gain, (y2 - top) / gain
            box = [int(max(0, x1)), int(max(0, y1)), int(min(original_width, x2)), int(min(original_height, y2))]
            if box[2] > box[0] and box[3] > box[1]:
                raw_detections.append({"class": class_name, "class_id": class_id, "confidence": float(score), "bbox": box})
        detections = self._non_max_suppression(raw_detections, nms_threshold=0.45)
        annotated_image = image.copy() if annotate else image
        for det in detections if annotate else ():
            x1, y1, x2, y2 = det["bbox"]
            color = (0, 200, 100) if det["class"].lower() == "healthy" else (0, 60, 220)
            cv2.rectangle(annotated_image, (x1, y1), (x2, y2), color, 4)
            text = f"{det['class']} {det['confidence']:.2%}"
            size = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.9, 2)[0]
            text_y = max(size[1] + 8, y1 - 8)
            cv2.rectangle(annotated_image, (x1 - 8, text_y - size[1] - 8), (x1 + size[0] + 8, text_y + 8), color, -1)
            cv2.putText(annotated_image, text, (x1, text_y), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2)
        return {"detections": [{"class": d["class"], "confidence": round(d["confidence"], 3), "bbox": d["bbox"]} for d in detections], "image": annotated_image}

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
        
        # Hard-coded, reviewed recommendations are the only supported path.
        print(f"[FALLBACK] Using hardcoded recommendations")
        
        recommendations = {
            'bud rot': {
                'fertilizer': 'Do not use fertilizer as a cure. After removing active rot, restore palm vigor with organic matter and a soil-test-based coconut fertilizer program; avoid excess nitrogen on stressed palms.',
                'treatment': 'Remove infected parts and severely damaged coconut trees to prevent the spread of bud rot. Apply recommended fungicides, such as fosetyl-aluminum, to protect nearby healthy trees, following proper dosage and application guidelines.',
                'prevention': 'Improve drainage and air flow; clean the crown before the rainy season; remove and destroy infected nuts, fronds, and nearby alternate-host material; inspect nearby palms, especially within about 20 m, and avoid wounding the crown.'
            },
            'caterpillars': {
                'fertilizer': 'Use NPK 10:10:10 balanced fertilizer monthly to boost tree vigor and recovery.',
                'treatment': 'PRIMARY: Spray Bacillus thuringiensis (Bt) at 1.5-2g per liter every 7 days for 3-4 weeks (organic option). ALTERNATIVE: Spinosad (0.5%) every 5-7 days or Phosphine (0.05%) for severe infestations. MANUAL: Handpick affected leaves in early morning when caterpillars are most active.',
                'prevention': 'Monitor leaves regularly for egg clusters and caterpillar droppings. Remove heavily infested fronds. Encourage natural predators like birds and parasitic wasps. Maintain tree vigor.'
            },
            'drying of leaflets': {
                'fertilizer': 'Check soil and leaf nutrient status before fertilizing. Correct documented potassium, magnesium, or micronutrient deficiencies with a locally recommended coconut fertilizer program; do not apply high fertilizer rates to a severely stressed palm.',
                'treatment': 'Confirm whether the symptom is nutrient stress, drought, or pest injury before spraying. Remove only dead or heavily diseased fronds and use a locally registered fungicide only when a crop specialist confirms a fungal leaf disease.',
                'prevention': 'Maintain good drainage, mulch and water during dry periods, avoid crown and leaf injury, remove diseased debris, and monitor palms regularly so leaf symptoms are diagnosed early.'
            },
            'stem bleeding': {
                'fertilizer': 'Use a soil-test-based fertilizer program to maintain palm vigor. Do not treat stem bleeding with fertilizer alone.',
                'treatment': 'Have the diagnosis confirmed promptly. Remove diseased bark only when advised by a qualified agricultural specialist, protect exposed tissue as recommended locally, and improve drainage around affected palms.',
                'prevention': 'Avoid trunk wounds, disinfect tools between palms, maintain drainage, remove severely affected palms when advised, and inspect nearby trees regularly for fresh bleeding lesions.'
            },
            'healthy': {
                'fertilizer': 'Apply balanced NPK 12:12:12 every 3 months. Use phosphate rich during flowering season.',
                'treatment': 'No treatment needed. Continue regular monitoring.',
                'prevention': 'Maintain regular fertilization. Water consistently. Remove dead leaves. Monitor for pests.'
            },
            # Grey Leaf Spot is CoconutAI's current application label.  The
            # historical `pestalotiopsis` key below is an application-level
            # compatibility alias only; it does not claim that the diagnoses
            # are biologically interchangeable.
            'grey leaf spot': {
                'fertilizer': 'Use a soil-test-based coconut fertilizer program. Keep potassium and chloride adequate; PCA reports KCl nutrition can improve resistance to coconut fungal leaf spots. Fertilizer supports recovery but does not cure the infection.',
                'treatment': 'Mechanical Method: Remove and properly dispose of or burn severely infected fronds.\nChemical Method: Spray recommended fungicide only when needed.',
                'prevention': 'Use clean planting material, remove infected leaves promptly, keep nursery and field sanitation high, improve airflow and drainage, minimize leaf wounds, and monitor frequently in wet weather.'
            }
        }
        
        # Normalize model labels for lookup. Some model versions emit the
        # misspelling "bud root" although the supported disease is bud rot.
        disease_lower = normalize_current_disease_label(disease_name).lower()
        rec = recommendations.get(disease_lower)
        if rec is None:
            rec = {
                'fertilizer': 'Do not apply a disease-specific fertilizer recommendation until an agricultural expert reviews this unsupported model class.',
                'treatment': 'No automatic treatment is available for this unsupported model class. Keep the image and request expert review.',
                'prevention': 'Monitor the palm and nearby trees while awaiting expert review.',
            }
        
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
    
# Singleton — loaded once on startup
detector = CoconutDiseaseDetector()
