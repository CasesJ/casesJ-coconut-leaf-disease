"""Offline stage and event-loop benchmark; never contacts deployment services.

Run before/after from the same checkout: python tools/benchmark_responsiveness.py
The detector and persistence are controlled doubles, not real AI/cloud timings.
"""
import asyncio
from io import BytesIO
import json
from pathlib import Path
import runpy
import statistics
import sys
import time
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
runpy.run_path(str(ROOT / "tests/conftest.py"))

import numpy as np
from fastapi import UploadFile
from PIL import Image
from app.api import detections, records, notifications
from app.core import security
from drone_gps import DroneGPS


def summary(values):
    return {"n": len(values), "median_ms": round(statistics.median(values), 3),
            "p95_ms": round(sorted(values)[int(.95 * (len(values) - 1))], 3)}


async def measure(call):
    gaps = []
    running = True

    async def heartbeat():
        previous = time.perf_counter()
        while running:
            await asyncio.sleep(.005)
            now = time.perf_counter()
            gaps.append((now - previous) * 1000)
            previous = now

    task = asyncio.create_task(heartbeat())
    await asyncio.sleep(0)
    start = time.perf_counter()
    result = await call()
    elapsed = (time.perf_counter() - start) * 1000
    await asyncio.sleep(.006)
    running = False
    await task
    return result, elapsed, max(gaps)


async def main():
    output = {"fixture": "synthetic 4000x3000 JPEG; fake inference 20ms, persistence 30ms",
              "python": sys.version, "stages": {}, "workloads": {}}
    photo = BytesIO()
    with Image.fromarray(np.random.default_rng(42).integers(0, 256, (3000, 4000, 3), dtype=np.uint8)) as image:
        image.save(photo, format="JPEG", quality=92)
    contents = photo.getvalue()

    def predict(image, conf, **kwargs):
        time.sleep(.020)
        return {"image": image, "detections": [{"class": "Healthy", "confidence": .9, "bbox": [1, 1, 50, 50]}]}

    def save(**kwargs):
        time.sleep(.030)
        kwargs["timings"].update({"cloudinary_upload_ms": 20.0, "firestore_write_ms": 10.0})
        return True

    async def allowed(*args, **kwargs):
        pass

    async def upload():
        return await detections.detect_image(
            SimpleNamespace(headers={"Authorization": "Bearer test"}),
            UploadFile(filename="synthetic.jpg", file=BytesIO(contents)),
        )

    stages, totals, stalls = {}, [], []
    fake_model = SimpleNamespace(detector=SimpleNamespace(
        predict=predict, backend="benchmark-double", active_model_path=None, class_names={}))
    with patch.dict(sys.modules, {"model": fake_model}), \
         patch("app.infrastructure.firebase.auth.verify_token", return_value={"uid": "benchmark"}), \
         patch("drone_gps.get_drone_gps", return_value=DroneGPS()), \
         patch.object(detections.limiter, "rolling", allowed), \
         patch.object(detections.limiter, "image_quota_24h", allowed), \
         patch.object(detections, "save_detection_record", save):
        for _ in range(12):
            result, elapsed, stall = await measure(upload)
            assert result["recorded_count"] == 1 and result["image_dimensions"] == {"width": 4000, "height": 3000}
            totals.append(elapsed)
            stalls.append(stall)
            for key, value in result["performance_ms"].items():
                stages.setdefault(key, []).append(value)
    output["upload_bytes"] = len(contents)
    output["stages"] = {key: summary(values) for key, values in stages.items()}
    output["workloads"]["upload"] = {"total": summary(totals), "max_heartbeat_gap": summary(stalls)}

    def slow(value):
        def call(*args, **kwargs):
            time.sleep(.040)
            return value
        return call

    with patch.object(records, "get_user_records", slow([])), \
         patch.object(notifications, "get_user_notifications", slow([])), \
         patch.object(security, "verify_token", slow({"uid": "benchmark"})):
        for name, call in {
            "records_read": lambda: records.get_records({"uid": "benchmark"}),
            "notifications_read": lambda: notifications.get_notifications({"uid": "benchmark"}),
            "auth_dependency": lambda: security.verify_firebase_token(SimpleNamespace(credentials="test")),
        }.items():
            elapsed, gaps = [], []
            for _ in range(12):
                _, duration, gap = await measure(call)
                elapsed.append(duration)
                gaps.append(gap)
            output["workloads"][name] = {"total": summary(elapsed), "max_heartbeat_gap": summary(gaps)}
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
