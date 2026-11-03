"""Isolated compatibility and RSS test.  Does not import production app/model.py."""
from __future__ import annotations

import argparse
import gc
import json
import os
import pickle
import shutil
import sys
from pathlib import Path

import cv2
import numpy as np
import psutil

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(__file__).resolve().parent / "generated_from_current_weights_openvino_model"
WEIGHTS = ROOT / "weights.pt"
IMAGES = [ROOT / "static" / "coco-farm.jpg", ROOT / "static" / "coco-farm-exif.jpg"]
THRESHOLDS = (0.05, 0.50)  # Current upload threshold and current stream threshold.
RETIRED = {"cercospora", "leaf rot"}


def rss_mib() -> float:
    return psutil.Process().memory_info().rss / 1024 / 1024


def load_recommendation_models() -> None:
    """Match production's eager pickle loading, including the optional feature engineer."""
    base = ROOT / "models" / "ml_recommendations"
    for name in ("treatment_clf.pkl", "fertilizer_clf.pkl", "preventive_clf.pkl", "treatment_encoder.pkl"):
        with (base / name).open("rb") as stream:
            pickle.load(stream)
    try:
        with (base / "feature_engineer.pkl").open("rb") as stream:
            pickle.load(stream)
    except Exception:
        pass  # Production also falls back when this optional pickle cannot import.


def normalize_names(names) -> dict[int, str]:
    return {int(key): str(value) for key, value in dict(names).items()}


def production_postprocess(result, names: dict[int, str], confidence: float) -> list[dict]:
    """Mirror model.py: class filtering and its second 0.45-IoU NMS."""
    boxes = result.boxes
    if boxes is None or not len(boxes):
        return []
    raw = []
    for xyxy, score, class_id in zip(boxes.xyxy.cpu().numpy(), boxes.conf.cpu().numpy(), boxes.cls.cpu().numpy()):
        class_id = int(class_id)
        score = float(score)
        label = names.get(class_id, "unknown")
        if score < confidence or label.lower().strip().replace("_", " ") in RETIRED:
            continue
        raw.append({"class_id": class_id, "class": label, "confidence": score,
                    "bbox": [float(x) for x in xyxy]})
    raw.sort(key=lambda detection: detection["confidence"], reverse=True)
    keep: list[dict] = []
    for detection in raw:
        ax1, ay1, ax2, ay2 = detection["bbox"]
        area_a = max(0.0, ax2 - ax1 + 1) * max(0.0, ay2 - ay1 + 1)
        for accepted in keep:
            bx1, by1, bx2, by2 = accepted["bbox"]
            inter = max(0.0, min(ax2, bx2) - max(ax1, bx1) + 1) * max(0.0, min(ay2, by2) - max(ay1, by1) + 1)
            union = area_a + max(0.0, bx2 - bx1 + 1) * max(0.0, by2 - by1 + 1) - inter
            if union and inter / union >= 0.45:
                break
        else:
            keep.append(detection)
    return keep


def iou(a: list[float], b: list[float]) -> float:
    ax1, ay1, ax2, ay2 = a; bx1, by1, bx2, by2 = b
    intersection = max(0.0, min(ax2, bx2) - max(ax1, bx1)) * max(0.0, min(ay2, by2) - max(ay1, by1))
    union = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1) + max(0.0, bx2 - bx1) * max(0.0, by2 - by1) - intersection
    return intersection / union if union else 0.0


def compare(pt: list[dict], ov: list[dict]) -> dict:
    unmatched = set(range(len(ov)))
    matched, missing = [], []
    for detection in pt:
        candidates = [(iou(detection["bbox"], ov[index]["bbox"]), index) for index in unmatched
                      if ov[index]["class_id"] == detection["class_id"]]
        if not candidates:
            missing.append(detection); continue
        overlap, index = max(candidates)
        unmatched.remove(index)
        matched.append({"class": detection["class"], "pytorch_confidence": detection["confidence"],
                        "openvino_confidence": ov[index]["confidence"],
                        "confidence_delta": ov[index]["confidence"] - detection["confidence"], "iou": overlap,
                        "pytorch_bbox": detection["bbox"], "openvino_bbox": ov[index]["bbox"]})
    return {"matched": matched, "missing_from_openvino": missing,
            "additional_in_openvino": [ov[index] for index in sorted(unmatched)]}


def export() -> Path:
    if OUT.is_dir():
        return OUT  # Test artifact is immutable; never overwrite an existing export.
    os.environ.setdefault("YOLO_CONFIG_DIR", str(ROOT / ".ultralytics"))
    from ultralytics import YOLO
    OUT.parent.mkdir(exist_ok=True)
    model = YOLO(str(WEIGHTS))
    # nms=False preserves the source export behavior. Ultralytics predict then applies
    # the same frontend postprocessing to both backends before our production NMS.
    exported = Path(model.export(format="openvino", imgsz=640, dynamic=False, half=False,
                                 int8=False, nms=False, project=str(OUT.parent), name=OUT.name, exist_ok=True))
    # Current Ultralytics chooses <weights stem>_openvino_model despite project/name.
    # Move only that newly-created output into this isolated directory.
    if exported.resolve() != OUT.resolve():
        if OUT.exists():
            raise RuntimeError(f"Refusing to overwrite existing compatibility export: {OUT}")
        shutil.move(str(exported), str(OUT))
    return OUT


def compatibility(exported: Path) -> dict:
    os.environ.setdefault("YOLO_CONFIG_DIR", str(ROOT / ".ultralytics"))
    from ultralytics import YOLO
    pt, ov = YOLO(str(WEIGHTS)), YOLO(str(exported))
    names = normalize_names(pt.names)
    report = {"classes": names, "images": []}
    for image_path in IMAGES:
        image = cv2.imread(str(image_path))
        if image is None:
            raise RuntimeError(f"Cannot read {image_path}")
        entry = {"image": str(image_path.relative_to(ROOT)), "image_shape": list(image.shape)}
        for threshold in THRESHOLDS:
            pt_result = pt.predict(source=image, imgsz=640, conf=threshold, verbose=False)[0]
            ov_result = ov.predict(source=image, imgsz=640, conf=threshold, verbose=False)[0]
            entry[str(threshold)] = compare(production_postprocess(pt_result, names, threshold),
                                            production_postprocess(ov_result, names, threshold))
        report["images"].append(entry)
    return report


def memory(backend: str, exported: Path | None) -> dict:
    """Fresh process mode: package imports, model load, recommendation pickles, then native-size image inference."""
    os.environ.setdefault("YOLO_CONFIG_DIR", str(ROOT / ".ultralytics"))
    before = rss_mib()
    if backend == "pytorch":
        from ultralytics import YOLO
        loaded = YOLO(str(WEIGHTS))
    elif backend == "openvino":
        from ultralytics import YOLO
        if exported is None:
            raise ValueError("OpenVINO export path is required")
        loaded = YOLO(str(exported))
    else:
        from openvino import Core
        if exported is None:
            raise ValueError("OpenVINO export path is required")
        core = Core()
        compiled = core.compile_model(core.read_model(exported / "weights.xml"), "CPU")
        request = compiled.create_infer_request()
        loaded = (compiled, request)
    after_model = rss_mib()
    load_recommendation_models()
    after_recommendations = rss_mib()
    peak = after_recommendations
    samples = []
    for image_path in IMAGES:
        image = cv2.imread(str(image_path))
        if backend == "openvino-raw":
            resized = cv2.cvtColor(cv2.resize(image, (640, 640)), cv2.COLOR_BGR2RGB)
            tensor = np.expand_dims(np.transpose(resized.astype(np.float32) / 255.0, (2, 0, 1)), 0)
            loaded[1].infer([tensor])
        else:
            loaded.predict(source=image, imgsz=640, conf=0.05, verbose=False)
        current = rss_mib(); peak = max(peak, current)
        samples.append({"image": image_path.name, "rss_after_inference_mib": current})
    return {"backend": backend, "rss_before_mib": before, "rss_after_model_mib": after_model,
            "rss_after_recommendations_mib": after_recommendations, "peak_rss_mib": peak, "samples": samples}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("export", "compare", "memory"))
    parser.add_argument("--backend", choices=("pytorch", "openvino", "openvino-raw"))
    args = parser.parse_args()
    if args.mode == "export":
        print(json.dumps({"exported": str(export())}, indent=2)); return
    exported = OUT
    if not exported.is_dir():
        raise SystemExit("Run export first; this deliberately will not use best_openvino_model.")
    outcome = compatibility(exported) if args.mode == "compare" else memory(args.backend, exported)
    print(json.dumps(outcome, indent=2))


if __name__ == "__main__":
    main()
