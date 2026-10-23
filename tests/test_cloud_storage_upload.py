"""Offline regressions for monitoring evidence uploads."""

from urllib.parse import parse_qs, quote, urlsplit

import pytest
from firebase_admin import storage

from app.infrastructure.firebase import cloud_storage


class FakeBlob:
    def __init__(self, name, events, upload_error=None):
        self.name = name
        self.events = events
        self.upload_error = upload_error
        self._metadata = None
        self._cache_control = None

    @property
    def metadata(self):
        return self._metadata

    @metadata.setter
    def metadata(self, value):
        self._metadata = value
        self.events.append("metadata")

    @property
    def cache_control(self):
        return self._cache_control

    @cache_control.setter
    def cache_control(self, value):
        self._cache_control = value
        self.events.append("cache_control")

    def upload_from_string(self, content, content_type):
        self.events.append(("upload", content, content_type))
        if self.upload_error:
            raise self.upload_error

    def patch(self):
        pytest.fail("Monitoring evidence upload must not issue a metadata PATCH")


class FakeBucket:
    name = "demo-neuralcoco-tests.appspot.com"

    def __init__(self, events, upload_error=None):
        self.events = events
        self.upload_error = upload_error
        self.blobs = []

    def blob(self, name):
        blob = FakeBlob(name, self.events, self.upload_error)
        self.blobs.append(blob)
        return blob


@pytest.mark.parametrize(("kind", "content_type", "suffix"), [
    ("original", "image/jpeg", "original.jpg"),
    ("original", "image/png", "original.png"),
    ("annotated", "image/jpeg", "annotated.jpg"),
])
def test_monitoring_upload_sets_metadata_before_upload_and_preserves_url(
        monkeypatch, kind, content_type, suffix):
    events = []
    bucket = FakeBucket(events)
    monkeypatch.setattr(storage, "bucket", lambda: bucket)
    content = b"offline evidence bytes"

    url = cloud_storage.upload_monitoring_evidence(
        "farmer-a", "case-1", "follow-up-1", kind, content, content_type,
    )

    blob = bucket.blobs[0]
    expected_path = f"monitoring/farmer-a/case-1/follow-up-1/{suffix}"
    assert blob.name == expected_path
    assert events == ["metadata", "cache_control", ("upload", content, content_type)]
    assert blob.metadata == {"firebaseStorageDownloadTokens": parse_qs(urlsplit(url).query)["token"][0]}
    assert blob.cache_control == "private, max-age=3600"
    parsed = urlsplit(url)
    assert parsed.netloc == "firebasestorage.googleapis.com"
    assert parsed.path == f"/v0/b/{bucket.name}/o/{quote(expected_path, safe='')}"
    assert parse_qs(parsed.query) == {"alt": ["media"], "token": [blob.metadata["firebaseStorageDownloadTokens"]]}


def test_monitoring_upload_propagates_storage_failure_without_patch(monkeypatch):
    events = []
    bucket = FakeBucket(events, upload_error=OSError("offline storage failure"))
    monkeypatch.setattr(storage, "bucket", lambda: bucket)

    with pytest.raises(OSError, match="offline storage failure"):
        cloud_storage.upload_monitoring_evidence(
            "farmer-a", "case-1", "follow-up-1", "original", b"evidence", "image/jpeg",
        )

    assert events[:2] == ["metadata", "cache_control"]
    assert events[2][0] == "upload"
    assert bucket.blobs[0].metadata["firebaseStorageDownloadTokens"]
    assert bucket.blobs[0].cache_control == "private, max-age=3600"
