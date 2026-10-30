"""Offline service-level checks with the existing in-memory Firestore double."""
import copy
import json
import runpy
import time

from benchmark_record_defaults import ROOT, load_function, summary
from app.domain import detection
from app.services import detection_service, export_service


def main():
    fixtures = runpy.run_path(str(ROOT / "tests/test_firestore_only_detection_store.py"))
    db = fixtures["Firestore"]()
    for i in range(500):
        record = fixtures["_record"](f"synthetic-{i}", image_url=f"https://example.test/{i}.jpg",
                                      annotated_image_url=f"https://example.test/{i}-annotated.jpg")
        db.data[("users", "farmer-a", "detections", record["id"])] = record
    initial = copy.deepcopy(db.data)
    detection_service._get_firestore = lambda: (db, True)
    counters = {"queries": 0, "documents": 0}
    query = fixtures["Query"]
    original_stream = query.stream

    def stream(self):
        result = original_stream(self)
        counters["queries"] += 1
        counters["documents"] += len(result)
        return result

    query.stream = stream
    variants = {"before": load_function(), "after": detection.apply_record_defaults}
    outputs, results = {}, {}
    for workload in ("expert_cache_miss", "expert_cache_hit", "farmer_records", "csv_cache_miss"):
        results[workload] = {}
        times = {name: [] for name in variants}
        reads_by_variant = {}
        for sample in range(60):
            pairs = list(variants.items())
            for name, fn in pairs if sample % 2 == 0 else reversed(pairs):
                detection_service.apply_record_defaults = fn
                detection_service.invalidate_expert_records_cache()
                if workload == "expert_cache_hit":
                    detection_service.get_all_user_records(500)
                start_counts = dict(counters)
                start = time.perf_counter()
                if workload == "farmer_records":
                    output = detection_service.get_user_records("farmer-a")
                elif workload == "csv_cache_miss":
                    output = export_service.generate_detections_csv()
                else:
                    output = detection_service.get_all_user_records(500)
                times[name].append((time.perf_counter() - start) * 1000)
                reads = {key: counters[key] - start_counts[key] for key in counters}
                reads_by_variant[name] = reads
                if workload in outputs:
                    assert output == outputs[workload]
                else:
                    outputs[workload] = output
                assert db.data == initial
        for name in variants:
            results[workload][name] = {**summary(times[name]), "reads_per_operation": reads_by_variant[name]}
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
