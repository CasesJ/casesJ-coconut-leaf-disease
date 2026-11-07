"""Offline security tests for Stage 2B direct-upload coordination."""
import hashlib
from datetime import datetime, timezone

import pytest

from app.infrastructure import cloudinary_storage
from app.services import hybrid_uploads
from app.services.hybrid_jobs import HybridJobStore, IdempotencyConflict


class Snap:
    def __init__(self, value): self.value=value; self.exists=value is not None
    def to_dict(self): return dict(self.value)
class Ref:
    def __init__(self, db, path): self.db,self.path=db,path
class Collection:
    def __init__(self, db, name): self.db,self.name=db,name
    def document(self, ident): return Ref(self.db, f"{self.name}/{ident}")
class Tx:
    def __init__(self, db): self.db=db; self.writes=[]
    def get(self, ref): return Snap(self.db.data.get(ref.path))
    def create(self, ref, value): self.writes.append(("create",ref.path,dict(value)))
    def update(self, ref, value): self.writes.append(("update",ref.path,dict(value)))
    def commit(self):
        for kind,path,value in self.writes:
            if kind == "create":
                if path in self.db.data: raise RuntimeError("duplicate")
                self.db.data[path]=value
            else: self.db.data[path].update(value)
class DB:
    def __init__(self): self.data={}
    def collection(self, name): return Collection(self,name)
    def transaction(self): return Tx(self)
def args():
    return {"idempotency_key":"a"*16, "source_sha256":"c"*64}


def test_signed_upload_authorization_binds_exact_asset_without_secret(monkeypatch):
    seen = {}
    monkeypatch.setattr(cloudinary_storage, "_configure", lambda: ("test-cloud", "public-key", "secret-value"))
    monkeypatch.setattr("cloudinary.utils.api_sign_request", lambda params, secret: seen.update(params=params, secret=secret) or "signature")
    result = cloudinary_storage.create_hybrid_upload_signature(
        public_id="neuralcoco/hybrid-original/" + "a" * 32 + ".jpg",
        job_id="b" * 32, user_id="farmer-1", timestamp=123,
    )
    assert result["signature"] == "signature" and "secret" not in result
    assert seen["secret"] == "secret-value"
    assert seen["params"]["type"] == "authenticated"
    assert "resource_type" not in seen["params"]
    assert seen["params"]["context"] == "hybrid_job_id=" + "b" * 32 + "|hybrid_user_id=farmer-1"


def test_generated_public_id_passes_real_signature_and_source_validation(monkeypatch):
    from app.services.hybrid_source import _PUBLIC_ID
    store=HybridJobStore(DB(), now=lambda: datetime.now(timezone.utc))
    monkeypatch.setattr(hybrid_uploads, "_store", lambda: store)
    monkeypatch.setattr(cloudinary_storage, "_configure", lambda: ("test-cloud", "public-key", "secret-value"))
    monkeypatch.setattr("cloudinary.utils.api_sign_request", lambda params, secret: "signature")
    authorization=hybrid_uploads.create_upload_authorization(
        user_id="farmer-1", idempotency_key="a"*16,
        source_sha256="c"*64, source_bytes=123,
    )
    public_id=authorization["upload_parameters"]["public_id"]
    assert public_id == hybrid_uploads._public_id("farmer-1", "a"*16)
    assert len(public_id.rsplit("/", 1)[-1].removesuffix(".jpg")) == 64
    assert _PUBLIC_ID.fullmatch(public_id)
    assert authorization["upload_parameters"]["signature"] == "signature"
    assert authorization["upload_parameters"]["type"] == "authenticated"
    assert store.get_job(authorization["job_id"])["public_id"] == public_id


def test_admin_verification_rejects_wrong_owner_or_asset(monkeypatch):
    public_id="neuralcoco/hybrid-original/" + "a" * 32 + ".jpg"
    monkeypatch.setattr(cloudinary_storage, "_configure", lambda: ("test", "key", "secret"))
    monkeypatch.setattr("cloudinary.api.resource", lambda *a, **k: {
        "public_id": public_id, "asset_id":"asset", "resource_type":"raw", "type":"authenticated",
        "bytes":12, "format":"jpg", "version":7,
        "context":{"custom":{"hybrid_job_id":"b"*32, "hybrid_user_id":"farmer"}},
    })
    ok=cloudinary_storage.verify_hybrid_uploaded_asset(public_id=public_id, job_id="b"*32, user_id="farmer", expected_bytes=12, asset_id="asset")
    assert ok["delivery_type"] == "authenticated" and ok["resource_type"] == "raw"
    with pytest.raises(cloudinary_storage.CloudinaryStorageError):
        cloudinary_storage.verify_hybrid_uploaded_asset(public_id=public_id, job_id="b"*32, user_id="other", expected_bytes=12, asset_id="asset")


def test_authenticated_raw_asset_without_admin_format_is_verified_as_jpeg(monkeypatch):
    public_id = "neuralcoco/hybrid-original/" + "a" * 64 + ".jpg"
    resource = {
        "public_id": public_id, "asset_id": "asset-1", "resource_type": "raw",
        "type": "authenticated", "bytes": 123, "format": None, "version": 7,
        "context": {"custom": {"hybrid_job_id": "b" * 32, "hybrid_user_id": "farmer"}},
    }
    monkeypatch.setattr(cloudinary_storage, "_configure", lambda: ("test", "key", "secret"))
    monkeypatch.setattr("cloudinary.api.resource", lambda *a, **k: dict(resource))
    reference = cloudinary_storage.verify_hybrid_uploaded_asset(
        public_id=public_id, job_id="b" * 32, user_id="farmer",
        expected_bytes=123, asset_id="asset-1",
    )
    assert reference["format"] == "jpg"
    assert reference["content_type"] == "image/jpeg"
    assert reference["resource_type"] == "raw"
    assert reference["delivery_type"] == "authenticated"
    assert reference["asset_id"] == "asset-1"


@pytest.mark.parametrize("field,value", [
    ("public_id", "neuralcoco/hybrid-original/" + "c" * 64 + ".jpg"),
    ("asset_id", "different-asset"),
    ("resource_type", "image"),
    ("type", "upload"),
    ("bytes", 122),
    ("format", "png"),
    ("format", "webp"),
    ("format", ""),
    ("version", None),
    ("context", {"custom": {"hybrid_job_id": "b" * 32, "hybrid_user_id": "other"}}),
])
@pytest.mark.parametrize("format_present", [False, True])
def test_missing_admin_format_does_not_bypass_asset_checks(monkeypatch, field, value, format_present):
    public_id = "neuralcoco/hybrid-original/" + "a" * 64 + ".jpg"
    resource = {
        "public_id": public_id, "asset_id": "asset-1", "resource_type": "raw",
        "type": "authenticated", "bytes": 123, "format": None, "version": 7,
        "context": {"custom": {"hybrid_job_id": "b" * 32, "hybrid_user_id": "farmer"}},
    }
    if not format_present:
        resource.pop("format")
    resource[field] = value
    monkeypatch.setattr(cloudinary_storage, "_configure", lambda: ("test", "key", "secret"))
    monkeypatch.setattr("cloudinary.api.resource", lambda *a, **k: dict(resource))
    with pytest.raises(cloudinary_storage.CloudinaryStorageError, match="verification failed"):
        cloudinary_storage.verify_hybrid_uploaded_asset(
            public_id=public_id, job_id="b" * 32, user_id="farmer",
            expected_bytes=123, asset_id="asset-1",
        )


def test_missing_admin_format_field_is_verified_as_jpeg(monkeypatch):
    public_id = "neuralcoco/hybrid-original/" + "a" * 64 + ".jpg"
    resource = {
        "public_id": public_id, "asset_id": "asset-1", "resource_type": "raw",
        "type": "authenticated", "bytes": 123, "version": 7,
        "context": {"custom": {"hybrid_job_id": "b" * 32, "hybrid_user_id": "farmer"}},
    }
    monkeypatch.setattr(cloudinary_storage, "_configure", lambda: ("test", "key", "secret"))
    monkeypatch.setattr("cloudinary.api.resource", lambda *a, **k: dict(resource))
    reference = cloudinary_storage.verify_hybrid_uploaded_asset(
        public_id=public_id, job_id="b" * 32, user_id="farmer",
        expected_bytes=123, asset_id="asset-1",
    )
    assert reference["format"] == "jpg"
    assert reference["content_type"] == "image/jpeg"
    assert reference["asset_id"] == "asset-1"
    assert reference["version"] == 7


def test_authorization_is_idempotent_and_confirmation_is_owner_bound(monkeypatch):
    store=HybridJobStore(DB(), now=lambda: datetime(2026,1,1,tzinfo=timezone.utc))
    monkeypatch.setattr(hybrid_uploads, "_store", lambda: store)
    monkeypatch.setattr(hybrid_uploads.config, "HYBRID_UPLOAD_TTL_SECONDS", 300)
    monkeypatch.setattr(hybrid_uploads.config, "HYBRID_UPLOAD_DAILY_LIMIT", 10)
    monkeypatch.setattr(hybrid_uploads, "create_hybrid_upload_signature", lambda **kw: {"cloud_name":"test", "signature":"sig", **kw})
    source=args()
    first=hybrid_uploads.create_upload_authorization(user_id="u", idempotency_key=source["idempotency_key"], source_sha256=source["source_sha256"], source_bytes=10)
    assert first["upload_url"] == "https://api.cloudinary.com/v1_1/test/raw/upload"
    retry=hybrid_uploads.create_upload_authorization(user_id="u", idempotency_key=source["idempotency_key"], source_sha256=source["source_sha256"], source_bytes=10)
    assert first["job_id"] == retry["job_id"] and not retry["created"]
    monkeypatch.setattr(hybrid_uploads, "verify_hybrid_uploaded_asset", lambda **kw: {"asset_id":"asset", "public_id":kw["public_id"], "byte_size":10})
    with pytest.raises(Exception): hybrid_uploads.confirm_upload(user_id="other", job_id=first["job_id"], asset_id="asset")
    confirmed=hybrid_uploads.confirm_upload(user_id="u", job_id=first["job_id"], asset_id="asset")
    assert confirmed["status"] == "pending"
    with pytest.raises(IdempotencyConflict):
        hybrid_uploads.confirm_upload(user_id="u", job_id=first["job_id"], asset_id="different")


def test_hybrid_http_routes_are_absent_from_default_render_local_app():
    from app.main import create_app

    paths = {route.path for route in create_app().routes}
    assert not any(path.startswith("/hybrid/") for path in paths)


def test_owner_status_projection_hides_source_and_worker_secrets(monkeypatch):
    store=HybridJobStore(DB(), now=lambda: datetime(2026,1,1,tzinfo=timezone.utc))
    monkeypatch.setattr(hybrid_uploads, "_store", lambda: store)
    job,_=store.create_upload_intent(
        user_id="u", idempotency_key="z"*16,
        public_id="neuralcoco/hybrid-original/"+"a"*32+".jpg", source_sha256="c"*64,
        source_bytes=12, route_type="detection",
    )
    status=hybrid_uploads.upload_status(user_id="u", job_id=job)
    assert status["status"] == "upload_intent"
    assert "cloudinary_asset" not in status and "source_sha256" not in status and "lease_owner" not in status


def test_hybrid_thumbnail_retry_adopts_exact_existing_asset(monkeypatch):
    from cloudinary import api, uploader
    job_id="b"*32
    content=b"\xff\xd8tiny-jpeg\xff\xd9"
    digest=hashlib.sha256(content).hexdigest()
    monkeypatch.setattr(cloudinary_storage, "_configure", lambda: ("test", "key", "secret"))
    resource={"public_id":f"neuralcoco/detection-thumbnail/{job_id}", "asset_id":"same-asset",
              "resource_type":"image", "type":"authenticated", "format":"jpg", "version":4,
              "bytes":len(content), "context":{"custom":{"hybrid_job_id":job_id,"thumbnail_sha256":digest}}}
    monkeypatch.setattr(api, "resource", lambda *a, **kw: resource)
    monkeypatch.setattr(uploader, "upload", lambda *a, **kw: pytest.fail("existing derivative must not upload again"))
    first=cloudinary_storage.upload_hybrid_thumbnail(content,job_id)
    second=cloudinary_storage.upload_hybrid_thumbnail(content,job_id)
    assert first == second and first["asset_id"] == "same-asset"


def test_hybrid_thumbnail_conflict_never_overwrites(monkeypatch):
    from cloudinary import api, uploader
    monkeypatch.setattr(cloudinary_storage, "_configure", lambda: ("test", "key", "secret"))
    monkeypatch.setattr(api, "resource", lambda *a, **kw: {
        "public_id":"neuralcoco/detection-thumbnail/"+"b"*32,
        "asset_id":"other", "resource_type":"image", "type":"authenticated",
        "format":"jpg", "version":1, "bytes":12,
        "context":{"custom":{"hybrid_job_id":"b"*32,"thumbnail_sha256":"0"*64}},
    })
    monkeypatch.setattr(uploader, "upload", lambda *a, **kw: pytest.fail("conflict must not upload"))
    with pytest.raises(cloudinary_storage.CloudinaryStorageError, match="identity conflict"):
        cloudinary_storage.upload_hybrid_thumbnail(b"different", "b"*32)


def test_hybrid_thumbnail_ambiguous_upload_adopts_exact_asset(monkeypatch):
    from cloudinary import api, uploader
    job_id="b"*32
    content=b"\xff\xd8synthetic\xff\xd9"
    digest=hashlib.sha256(content).hexdigest()
    stored=[]
    class Missing(Exception):
        http_code=404
    def inspect(*_args, **_kwargs):
        if not stored:
            raise Missing()
        return stored[0]
    def upload(*_args, **kwargs):
        assert kwargs["overwrite"] is False
        assert kwargs["public_id"] == f"neuralcoco/detection-thumbnail/{job_id}"
        stored.append({
            "public_id":kwargs["public_id"], "asset_id":"stable", "resource_type":"image",
            "type":"authenticated", "format":"jpg", "version":1, "bytes":len(content),
            "context":{"custom":{"hybrid_job_id":job_id,"thumbnail_sha256":digest}},
        })
        raise TimeoutError("response lost after provider accepted upload")
    monkeypatch.setattr(cloudinary_storage, "_configure", lambda: ("test", "key", "secret"))
    monkeypatch.setattr(api, "resource", inspect)
    monkeypatch.setattr(uploader, "upload", upload)
    result=cloudinary_storage.upload_hybrid_thumbnail(content,job_id)
    assert result["asset_id"] == "stable" and len(stored) == 1


def test_hybrid_raw_stream_yields_bounded_chunks_without_cache(monkeypatch):
    class Response:
        status_code=200
        def raise_for_status(self): pass
        def iter_content(self, chunk_size):
            assert chunk_size == 64*1024
            yield b"original-"
            yield b"bytes"
        def close(self): self.closed=True
    response=Response()
    monkeypatch.setattr(cloudinary_storage, "_configure", lambda: ("test", "key", "secret"))
    monkeypatch.setattr("cloudinary.utils.private_download_url", lambda *a, **k:
                        "https://api.cloudinary.com/v1_1/test/raw/download")
    monkeypatch.setattr(cloudinary_storage.requests, "get", lambda *a, **k: response)
    reference={"provider":"cloudinary", "public_id":"neuralcoco/hybrid-original/"+"a"*32+".jpg",
               "resource_type":"raw", "delivery_type":"authenticated", "format":"jpg",
               "content_type":"image/jpeg"}
    chunks=cloudinary_storage.stream_asset(reference)
    assert b"".join(chunks) == b"original-bytes"
    assert response.closed
