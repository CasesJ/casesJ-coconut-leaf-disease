"""Read-only local OpenVINO measurement for the approved DJI MPO fixture."""
from __future__ import annotations

import gc
import json
import os
import threading
import time
from pathlib import Path

import psutil

SOURCE = Path(r"C:\Users\CASES JAMES\Downloads\dji_fly_20261025_093334_166_1792892480817_photo (1).jpg")


def main() -> None:
    if os.environ.get("INFERENCE_BACKEND") != "openvino":
        raise SystemExit("Set INFERENCE_BACKEND=openvino for this measurement")
    if not SOURCE.is_file():
        raise SystemExit("Approved DJI fixture is not available")
    process = psutil.Process()
    peak = {"rss": process.memory_info().rss}
    stop = threading.Event()

    def sample():
        while not stop.wait(0.02):
            peak["rss"] = max(peak["rss"], process.memory_info().rss)

    monitor = threading.Thread(target=sample, daemon=True)
    monitor.start()
    started = time.monotonic()
    try:
        from app.api.detections import _map_detections_to_source, _prepare_upload
        from app.services.inference_service import run_image_inference
        from model import detector

        contents = SOURCE.read_bytes()
        read_ms = (time.monotonic() - started) * 1000
        prepared_at = time.monotonic()
        (inference_bytes, metadata, lat, lng, gps_source, gps_accuracy, image,
         dimensions, scale_x, scale_y, preparation) = _prepare_upload(contents, SOURCE.name, "stage2f-local")
        prepare_ms = (time.monotonic() - prepared_at) * 1000
        infer_at = time.monotonic()
        result = run_image_inference(detector, image, 0.05, annotate=False)
        infer_ms = (time.monotonic() - infer_at) * 1000
        detections = _map_detections_to_source(result["detections"], scale_x, scale_y)
        report = {
            "source_bytes": len(contents), "dimensions": dimensions,
            "inference_dimensions": {"width": int(image.shape[1]), "height": int(image.shape[0])},
            "decode_scale": max(scale_x, scale_y), "backend": detector.backend,
            "detections": [{"class": item.get("class"), "confidence": item.get("confidence"), "bbox": item.get("bbox")} for item in detections],
            "gps_present": lat is not None and lng is not None, "gps_source": gps_source,
            "xmp_present": bool(metadata.get("xmp") or metadata.get("is_dji")),
            "icc_present": bool(metadata.get("icc_profile")),
            "read_ms": round(read_ms, 1), "prepare_ms": round(prepare_ms, 1),
            "inference_ms": round(infer_ms, 1), "preparation_ms": {key: round(value, 1) for key,value in preparation.items()},
        }
        del result, image, inference_bytes, contents
        gc.collect()
        print(json.dumps(report, sort_keys=True))
    finally:
        stop.set(); monitor.join(timeout=1)
        print(json.dumps({"peak_rss_mib": round(peak["rss"] / 1024 / 1024, 1)}, sort_keys=True))


if __name__ == "__main__":
    main()
