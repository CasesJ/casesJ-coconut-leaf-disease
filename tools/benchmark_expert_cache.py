"""Concurrent expert reads against an offline 500-document Firestore double."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import runpy
import sys
from threading import Barrier, Lock
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
runpy.run_path(str(ROOT / 'tests/conftest.py'))
from app.services import detection_service as service


def main():
    fixture = runpy.run_path(str(ROOT / 'tests/test_firestore_only_detection_store.py'))
    store = fixture['Firestore']()
    for i in range(500):
        record = fixture['_record'](f'synthetic-{i}', image_url=f'https://example.test/{i}.jpg',
                                    annotated_image_url=f'https://example.test/{i}-annotated.jpg')
        store.data[('users', 'farmer-a', 'detections', record['id'])] = record
    service._get_firestore = lambda: (store, True)
    query = fixture['Query']
    original_stream = query.stream
    counts = {'queries': 0, 'documents': 0}
    lock = Lock()

    def stream(self):
        time.sleep(.040)
        docs = original_stream(self)
        with lock:
            counts['queries'] += 1
            counts['documents'] += len(docs)
        return docs

    query.stream = stream
    runs = []
    for _ in range(12):
        service.invalidate_expert_records_cache()
        barrier = Barrier(4)

        def read():
            barrier.wait(timeout=5)
            return service.get_all_user_records(500)

        started = time.perf_counter()
        before = counts.copy()
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(lambda _: read(), range(4)))
        elapsed = (time.perf_counter() - started) * 1000
        assert all(result == results[0] and len(result) == 500 for result in results)
        runs.append({'duration_ms': elapsed, **{key: counts[key] - before[key] for key in counts}})
    print(json.dumps({'fixture': '4 simultaneous readers, 500 records, 40ms fake query delay', 'runs': runs}, indent=2))


if __name__ == '__main__':
    main()
