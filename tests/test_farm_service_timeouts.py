"""Bounded optional farm-centre persistence behavior."""
from types import SimpleNamespace

from app.services import farm_service as farm


class Reference:
    def __init__(self, current=None, committed=True, latest=None):
        self.current = current
        self.committed = committed
        self.latest = latest
        self.get_calls = 0
        self.set_calls = 0

    def get(self, etag=False):
        self.get_calls += 1
        return (self.current, "etag-1") if etag else self.current

    def set_if_unchanged(self, etag, value):
        self.set_calls += 1
        assert etag == "etag-1"
        return self.committed, self.latest, "etag-2"


def setup_function():
    farm._CACHE.clear()


def test_existing_valid_center_is_cached_without_a_write(monkeypatch):
    reference = Reference({"latitude": 7.3, "longitude": 125.6, "source": "exif"})
    monkeypatch.setattr(farm, "_bounded_reference", lambda path: reference)

    first = farm.set_initial_farm_center("farmer-a", 8.0, 126.0, "dji_exif", "op-1")
    second = farm.get_farm_center("farmer-a", "op-2")

    assert first == second
    assert first["latitude"] == 7.3
    assert reference.get_calls == 1
    assert reference.set_calls == 0


def test_missing_center_uses_one_etag_read_and_one_conditional_write(monkeypatch):
    reference = Reference(None, committed=True)
    monkeypatch.setattr(farm, "_bounded_reference", lambda path: reference)

    saved = farm.set_initial_farm_center("farmer-a", 7.3, 125.6, "dji_exif", "op-1")

    assert saved["latitude"] == 7.3
    assert reference.get_calls == 1
    assert reference.set_calls == 1


def test_conflict_never_replaces_valid_remote_center(monkeypatch):
    remote = {"latitude": 7.1, "longitude": 125.4, "source": "exif"}
    reference = Reference(None, committed=False, latest=remote)
    monkeypatch.setattr(farm, "_bounded_reference", lambda path: reference)

    saved = farm.set_initial_farm_center("farmer-a", 8.0, 126.0, "dji_exif", "op-1")

    assert saved == remote
    assert reference.set_calls == 1


def test_optional_timeout_returns_none_and_does_not_cache(monkeypatch, caplog):
    reference = Reference()
    reference.get = lambda **kwargs: (_ for _ in ()).throw(TimeoutError("read timed out"))
    monkeypatch.setattr(farm, "_bounded_reference", lambda path: reference)

    assert farm.set_initial_farm_center("farmer-a", 7.3, 125.6, "exif", "safe-op") is None
    assert farm._CACHE == {}
    assert "operation_id=safe-op" in caplog.text
    assert "farmer-a" not in caplog.text


def test_bounded_reference_disables_retries_and_sets_short_timeout(monkeypatch):
    mounted = []
    session = SimpleNamespace(mount=lambda scheme, adapter: mounted.append((scheme, adapter)))
    default_client = SimpleNamespace(
        credential=object(), base_url="https://database.invalid", params={}
    )
    created = {}

    class Client:
        def __init__(self, **kwargs):
            created.update(kwargs)
            self.session = session

    class FirebaseDb:
        _Client = Client
        Reference = lambda **kwargs: kwargs
        reference = lambda path: SimpleNamespace(_client=default_client)

    import firebase_admin
    monkeypatch.setattr(firebase_admin, "db", FirebaseDb)
    monkeypatch.setattr(farm.config, "FARM_CENTER_TIMEOUT_SECONDS", 3)

    result = farm._bounded_reference("users/farmer-a/farm_center")

    assert created["timeout"] == 3
    assert result["path"] == "users/farmer-a/farm_center"
    assert [scheme for scheme, _ in mounted] == ["http://", "https://"]
    assert all(adapter.max_retries.total == 0 for _, adapter in mounted)
