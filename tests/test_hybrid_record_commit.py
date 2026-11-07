"""Stage 2D canonical-record projection tests using a transaction-capable fake."""
from datetime import datetime, timezone

import pytest

from app.services.hybrid_jobs import HybridJobStore
from app.services.hybrid_record_commit import HybridRecordCommitError, commit_completed_job
from app.services import media_service
from app.services.monitoring_service import _baseline_image_reference


class Snap:
    def __init__(self, value): self.value=value; self.exists=value is not None
    def to_dict(self): return dict(self.value)
class Ref:
    def __init__(self, db, path): self.db,self.path=db,path
    def collection(self, name): return Collection(self.db, f"{self.path}/{name}")
class Collection:
    def __init__(self, db, name): self.db,self.name=db,name
    def document(self, ident): return Ref(self.db, f"{self.name}/{ident}")
class Tx:
    def __init__(self, db): self.db=db; self.writes=[]
    def get(self, ref):
        if isinstance(ref, Collection):
            return [Snap(value) for path,value in self.db.data.items() if path.startswith(ref.name + "/")]
        return Snap(self.db.data.get(ref.path))
    def create(self, ref, value): self.writes.append(("create", ref.path, dict(value)))
    def update(self, ref, value): self.writes.append(("update", ref.path, dict(value)))
    def commit(self):
        for kind,path,value in self.writes:
            if kind == "create":
                if path in self.db.data: raise RuntimeError("duplicate")
                self.db.data[path]=value
            else:
                if path not in self.db.data: raise RuntimeError("missing")
                self.db.data[path].update(value)
class DB:
    def __init__(self): self.data={}
    def collection(self, name): return Collection(self,name)
    def transaction(self): return Tx(self)


def _job(store):
    job,_=store.create_upload_intent(
        user_id="farmer", user_email="farmer@example.test", idempotency_key="a"*16,
        public_id="neuralcoco/hybrid-original/"+"b"*32+".jpg",
        source_sha256="c"*64, source_bytes=99, route_type="detection",
    )
    store.mark_verified(job)
    claim=store.claim("worker", 60)
    store.complete(job, "worker", claim["lease_token"], {
        "schema":"hybrid-inference-result-v1", "detections":[{"class":"Bud Rot","confidence":0.9,"bbox":[1,2,30,40]}],
        "source_dimensions":{"width":100,"height":80}, "gps":{"latitude":7.3,"longitude":125.6,"accuracy":5,"source":"dji_exif"},
        "image_metadata":{"gps_source":"dji_exif","xmp":{"DJI":"retained"}},
        "source_identity":{"sha256":"c"*64,"bytes":99},
        "thumbnail_asset": {
            "provider":"cloudinary", "asset_id":"thumb", "public_id":f"neuralcoco/detection-thumbnail/{job}",
            "resource_type":"image", "delivery_type":"authenticated", "version":1,
            "format":"jpg", "content_type":"image/jpeg", "byte_size":12,
        },
    })
    # The source is the one browser-uploaded and worker-hash-checked raw asset.
    store.db.data[f"hybrid_inference_jobs/{job}"]["cloudinary_asset"]={
        "provider":"cloudinary", "asset_id":"asset", "public_id":"neuralcoco/hybrid-original/"+"b"*32+".jpg",
        "resource_type":"raw", "delivery_type":"authenticated", "version":1,
        "format":"jpg", "content_type":"image/jpeg", "byte_size":99,
    }
    return job


def test_completed_job_projects_one_existing_schema_record_and_reuses_raw_evidence(monkeypatch):
    db=DB(); store=HybridJobStore(db, now=lambda:datetime(2026,1,1,tzinfo=timezone.utc)); job=_job(store)
    monkeypatch.setattr("app.services.hybrid_record_commit._farm_assignment", lambda *a: (7,False))
    first=commit_completed_job(db, job, store=store); second=commit_completed_job(db, job, store=store)
    record_id=f"hybrid_{job}"; record=db.data[f"users/farmer/detections/{record_id}"]
    assert first == {"record_id":record_id,"created":True} and second == {"record_id":record_id,"created":False}
    assert record["user_id"] == "farmer" and record["hybrid_job_id"] == job
    assert record["farm_area_id"] == 7 and record["gps_data"]["source"] == "dji_exif"
    assert record["image_assets"]["original"]["resource_type"] == "raw"
    assert record["image_assets"]["thumbnail"]["resource_type"] == "image"
    assert record["detections"][0]["bbox_normalized"] == [0.01,0.025,0.3,0.5]
    assert db.data[f"hybrid_inference_jobs/{job}"]["record_commit_status"] == "committed"


def test_invalid_or_incomplete_result_never_creates_record():
    db=DB(); store=HybridJobStore(db, now=lambda:datetime(2026,1,1,tzinfo=timezone.utc)); job=_job(store)
    db.data[f"hybrid_inference_jobs/{job}"]["result_reference"]["source_identity"]["sha256"]="bad"
    with pytest.raises(HybridRecordCommitError): commit_completed_job(db, job, store=store)
    assert not any(path.startswith("users/farmer/detections/") for path in db.data)


def test_private_media_presentation_hides_raw_reference_and_reads_through_adapter(monkeypatch):
    record={"id":"hybrid_x", "user_id":"farmer", "image_assets":{"original":{
        "provider":"cloudinary", "asset_id":"asset", "public_id":"neuralcoco/hybrid-original/"+"b"*32+".jpg",
        "resource_type":"raw", "delivery_type":"authenticated", "version":1,
        "format":"jpg", "content_type":"image/jpeg",
    }}}
    presented=media_service.present_detection_record(record)
    assert presented["image_url"] == "/media/detections/hybrid_x/original"
    assert "image_assets" not in presented
    monkeypatch.setattr(media_service, "download_asset", lambda reference: (b"raw-bytes", "image/jpeg"))
    assert media_service.read_detection_image(record,"original") == (b"raw-bytes","image/jpeg")


def test_interrupted_completed_job_preserves_evidence_for_monitoring_snapshot(monkeypatch):
    db=DB(); store=HybridJobStore(db, now=lambda:datetime(2026,1,1,tzinfo=timezone.utc)); job=_job(store)
    monkeypatch.setattr("app.services.hybrid_record_commit._farm_assignment", lambda *a: (None,False))
    # The record commit starts after worker completion and projects only the
    # existing source reference; it does not invoke Cloudinary uploads.
    outcome=commit_completed_job(db, job, store=store)
    assert outcome["created"]
    record=db.data[f"users/farmer/detections/hybrid_{job}"]
    snapshot=_baseline_image_reference(record, record["id"])
    assert snapshot["image_assets"] == record["image_assets"]
    assert snapshot["image_assets"] is not record["image_assets"]
