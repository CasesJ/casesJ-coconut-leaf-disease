"""Mixed-request responsiveness and cache correctness under concurrent reads."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from threading import Barrier, Event
from types import SimpleNamespace
import sys
import time
from unittest.mock import patch

import httpx
from PIL import Image
import pytest

from app.api import monitoring
from app.core.security import verify_firebase_token
from app.main import app
from app.services import detection_service as store
from test_firestore_only_detection_store import Firestore, Query, _record


@pytest.fixture
def cached_store(monkeypatch):
    db = Firestore()
    db.data[('users', 'farmer-a', 'detections', 'one')] = _record('one')
    monkeypatch.setattr(store, '_get_firestore', lambda: (db, True))
    store.invalidate_expert_records_cache()
    yield db
    store.invalidate_expert_records_cache()


def test_concurrent_cache_misses_share_one_query_and_isolate_results(cached_store, monkeypatch):
    queries = []
    original = Query.stream
    barrier = Barrier(4)

    def stream(query):
        queries.append(1)
        time.sleep(.03)
        return original(query)

    monkeypatch.setattr(Query, 'stream', stream)

    def read(_):
        barrier.wait(timeout=3)
        return store.get_all_user_records(500)

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(read, range(4)))
    assert len(queries) == 1
    assert all(value == results[0] for value in results)
    results[0][0]['detections'][0]['class'] = 'changed'
    assert results[1][0]['detections'][0]['class'] == 'Bud Rot'
    assert store.get_all_user_records()[0]['detections'][0]['class'] == 'Bud Rot'


def test_write_during_query_cannot_republish_an_outdated_cache(cached_store, monkeypatch):
    original = Query.stream
    queries = []

    def stream(query):
        docs = original(query)
        queries.append(1)
        if len(queries) == 1:
            cached_store.data[('users', 'farmer-a', 'detections', 'two')] = _record('two')
            store.invalidate_expert_records_cache()
        return docs

    monkeypatch.setattr(Query, 'stream', stream)
    assert len(store.get_all_user_records()) == 1
    assert len(store.get_all_user_records()) == 2
    assert len(queries) == 2


def test_failed_query_releases_load_lock_for_retry(cached_store):
    with patch.object(Query, 'stream', side_effect=RuntimeError('offline')):
        with pytest.raises(store.DetectionStoreUnavailableError):
            store.get_all_user_records()
    assert len(store.get_all_user_records()) == 1


def test_monitoring_inference_does_not_block_another_request(monkeypatch):
    started, release = Event(), Event()
    photo = BytesIO()
    Image.new('RGB', (32, 32)).save(photo, format='JPEG')

    def predict(image, conf, *, annotate):
        assert not annotate
        started.set()
        assert release.wait(3), 'Inference blocked the event loop'
        return {'image': image, 'detections': []}

    async def allowed(*args, **kwargs):
        pass

    fake_model = SimpleNamespace(detector=SimpleNamespace(predict=predict, class_names={0: 'Healthy'}))
    monkeypatch.setitem(sys.modules, 'model', fake_model)
    monkeypatch.setattr(monitoring.store, 'get_case', lambda *_: {
        'id': 'case-1', 'farmer_id': 'farmer-a', 'tree_id': 'tree-1',
        'baseline_detection_id': 'baseline', 'treatment_record_id': 'treatment', 'disease': 'Bud Rot'})
    monkeypatch.setattr(monitoring.store, 'follow_up_upload_allowance', lambda *_: {'remaining': 3})
    monkeypatch.setattr(monitoring.limiter, 'rolling', allowed)
    monkeypatch.setattr(monitoring.limiter, 'image_quota_24h', allowed)
    monkeypatch.setattr(monitoring, 'upload_asset', lambda *_: {'provider': 'test'})
    monkeypatch.setattr(monitoring.store, 'save_follow_up', lambda *_: None)
    app.dependency_overrides[verify_firebase_token] = lambda: {'uid': 'farmer-a'}

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            upload = asyncio.create_task(client.post('/monitoring/cases/case-1/follow-ups',
                files={'file': ('leaf.jpg', photo.getvalue(), 'image/jpeg')}))
            try:
                assert await asyncio.to_thread(started.wait, 3)
                response = await asyncio.wait_for(client.get('/classes'), timeout=1)
                assert response.status_code == 200
                assert not upload.done()
            finally:
                release.set()
            assert (await upload).status_code == 201

    asyncio.run(exercise())
