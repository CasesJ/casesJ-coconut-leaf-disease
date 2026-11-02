"""Pair an archived upload handler and current handler with real local weights.

python tools/benchmark_model_pipeline.py path/to/baseline/app/api/detections.py
Only synthetic images are used. Authentication and persistence are doubles;
tests/conftest.py blocks all deployment/network access before app/model imports.
"""
import ast
import asyncio
from contextlib import redirect_stdout
import hashlib
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
runpy.run_path(str(ROOT / 'tests/conftest.py'))
import numpy as np
from PIL import Image
from fastapi import UploadFile
from app.api import detections
from drone_gps import DroneGPS


def summary(values):
    return {'n': len(values), 'median_ms': statistics.median(values),
            'p95_ms': sorted(values)[int(.95 * (len(values) - 1))]}


async def main():
    started = time.perf_counter()
    with redirect_stdout(sys.stderr):
        from model import detector
    cold_import = (time.perf_counter() - started) * 1000
    photo = BytesIO()
    with Image.fromarray(np.random.default_rng(42).integers(0, 256, (3000, 4000, 3), dtype=np.uint8)) as image:
        image.save(photo, format='JPEG', quality=92)
    contents = photo.getvalue()
    measurements = {'before': {}, 'after': {}}
    backend_stages = {}
    original_predict = detector.yolo_model.predict

    def predict(*args, **kwargs):
        result = original_predict(*args, **kwargs)
        backend_stages.clear()
        if result:
            backend_stages.update(result[0].speed)
        return result

    async def allowed(*args, **kwargs):
        pass

    def persist(**kwargs):
        return True

    with patch('app.infrastructure.firebase.auth.verify_token', return_value={'uid': 'benchmark'}), \
         patch('drone_gps.get_drone_gps', return_value=DroneGPS()), \
         patch.object(detections.limiter, 'rolling', allowed), \
         patch.object(detections.limiter, 'image_quota_24h', allowed), \
         patch.object(detections, 'save_detection_record', persist), \
         patch.object(detector.yolo_model, 'predict', predict), redirect_stdout(sys.stderr):
        tree = ast.parse(Path(sys.argv[1]).read_text(encoding='utf-8-sig'))
        function = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == 'detect_image')
        function.decorator_list = []
        namespace = vars(detections).copy()
        exec(compile(ast.Module(body=[function], type_ignores=[]), 'baseline-handler', 'exec'), namespace)
        variants = {'before': namespace['detect_image'], 'after': detections.detect_image}
        expected = None
        first_call = None
        for iteration in range(9):
            pairs = list(variants.items())
            for name, handler in pairs if iteration % 2 == 0 else reversed(pairs):
                start = time.perf_counter()
                result = await handler(SimpleNamespace(headers={'Authorization': 'Bearer test'}),
                    UploadFile(filename='synthetic.jpg', file=BytesIO(contents)))
                total = (time.perf_counter() - start) * 1000
                stable = {key: result[key] for key in ('detections', 'image_dimensions', 'annotated_image_base64', 'image_metadata')}
                if expected is None:
                    expected = stable
                    first_call = total
                assert stable == expected, 'Inference/preview/metadata output changed'
                # Exclude one warmup pair, including predictor initialization.
                if iteration == 0:
                    continue
                values = {**result['performance_ms'], 'handler_wall_ms': total,
                          **{f'model_{key}_ms': value for key, value in backend_stages.items()}}
                for key, value in values.items():
                    measurements[name].setdefault(key, []).append(value)
    print(json.dumps({'fixture': 'synthetic 4000x3000 JPEG, real model, fake auth/persistence; alternating order',
                     'weights_sha256': hashlib.sha256((ROOT / 'weights.pt').read_bytes()).hexdigest(),
                     'cold_model_import_ms': cold_import, 'first_handler_call_ms': first_call,
                     'identical_outputs': True,
                     'measurements': {name: {key: summary(values) for key, values in stages.items()}
                                      for name, stages in measurements.items()}}, indent=2))


if __name__ == '__main__':
    asyncio.run(main())
