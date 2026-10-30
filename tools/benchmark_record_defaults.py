"""Offline paired benchmark: python tools/benchmark_record_defaults.py.

Install repository network/credential guards before importing application code.
Compare the working implementation with the source captured before optimization.
"""
import ast
import copy
import json
import platform
from pathlib import Path
import runpy
import statistics
import subprocess
import sys
import time
import tracemalloc

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
runpy.run_path(str(ROOT / "tests/conftest.py"))
from app.domain import detection


BASELINE_REF = "852070a2f3932b814d181e5662a875eb1ee54f18"


def load_function():
    source = subprocess.check_output(
        ["git", "show", f"{BASELINE_REF}:app/domain/detection.py"], cwd=ROOT, encoding="utf-8")
    tree = ast.parse(source)
    function = next(node for node in tree.body
                    if isinstance(node, ast.FunctionDef) and node.name == "apply_record_defaults")
    namespace = vars(detection).copy()
    exec(compile(ast.Module(body=[function], type_ignores=[]), "baseline", "exec"), namespace)
    return namespace[function.name]


def summary(values):
    return {"n": len(values), "median_ms": statistics.median(values),
            "p95_ms": sorted(values)[int(.95 * (len(values) - 1))],
            "stdev_ms": statistics.stdev(values), "min_ms": min(values), "max_ms": max(values)}


def main():
    before = load_function()
    after = detection.apply_record_defaults
    records = [{"id": f"synthetic-{i}", "user_id": "benchmark-user",
                "image_url": f"https://example.test/original/{i}.jpg",
                "annotated_image_url": f"https://example.test/annotated/{i}.jpg",
                "detections": [{"class": "healthy", "confidence": .9}],
                "gps_data": {"latitude": 7.35, "longitude": 125.64}}
               for i in range(500)]
    assert [before(copy.deepcopy(r)) for r in records] == [after(copy.deepcopy(r)) for r in records]
    results = {"baseline_ref": BASELINE_REF, "python": sys.version,
               "platform": platform.platform(), "records": len(records)}
    timings = {"before": [], "after": []}
    cpu = {"before": [], "after": []}
    # First call is separate; this is not process/model cold-start time.
    for name, fn in (("before", before), ("after", after)):
        batch = copy.deepcopy(records)
        start = time.perf_counter()
        for record in batch:
            fn(record)
        results[name + "_first_batch_ms"] = (time.perf_counter() - start) * 1000
    for sample in range(60):
        pairs = (("before", before), ("after", after))
        for name, fn in pairs if sample % 2 == 0 else reversed(pairs):
            batch = copy.deepcopy(records)
            cpu_start = time.process_time()
            start = time.perf_counter()
            for record in batch:
                fn(record)
            timings[name].append((time.perf_counter() - start) * 1000)
            cpu[name].append((time.process_time() - cpu_start) * 1000)
    for name, fn in (("before", before), ("after", after)):
        batch = copy.deepcopy(records)
        tracemalloc.start()
        for record in batch:
            fn(record)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        results[name] = {**summary(timings[name]), "cpu_median_ms": statistics.median(cpu[name]),
                         "cpu_total_ms": sum(cpu[name]), "python_peak_bytes": peak}
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
