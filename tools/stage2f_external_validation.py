"""One-shot approved external Stage 2F validation.

Creates exactly one disposable raw Cloudinary asset under the Stage 2F test
prefix. All Firestore writes are fail-closed to the localhost demo emulator.
It never deletes Cloudinary assets or reads production Firebase.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import tempfile
import time
from pathlib import Path
from uuid import uuid4

import firebase_admin
import piexif
import requests
from firebase_admin import credentials, firestore
from google.auth.credentials import AnonymousCredentials
from PIL import Image, ImageCms

from app.core import config
from app.infrastructure import cloudinary_storage
from app.services import hybrid_source, media_service
from app.services.hybrid_jobs import HybridJobStore, IDEMPOTENCY_COLLECTION, JOB_COLLECTION, QUOTA_COLLECTION, WORKER_NONCE_COLLECTION
from app.services.hybrid_record_commit import commit_completed_job

PROJECT = "demo-coconutai-hybrid-tests"
HOST = "127.0.0.1:8080"
PREFIX = "neuralcoco/hybrid-stage2f-test"
RUN_ROOT = Path("tmp") / "stage2f-external-runs"


class EmulatorCredential(credentials.Base):
    def get_credential(self):
        return AnonymousCredentials()


class PrefixedStore(HybridJobStore):
    def __init__(self, db, prefix):
        super().__init__(db); self.prefix = prefix
    def _collection(self): return self.db.collection(f"{JOB_COLLECTION}_{self.prefix}")
    def _key_ref(self, user_id, key):
        return self.db.collection(f"{IDEMPOTENCY_COLLECTION}_{self.prefix}").document(super()._key_ref(user_id, key).id)
    def _quota_ref(self, user_id, now):
        return self.db.collection(f"{QUOTA_COLLECTION}_{self.prefix}").document(super()._quota_ref(user_id, now).id)
    def _nonce_ref(self, nonce):
        return self.db.collection(f"{WORKER_NONCE_COLLECTION}_{self.prefix}").document(super()._nonce_ref(nonce).id)


def sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def fixture(path: Path) -> bytes:
    """Synthetic image only; no DJI/farmer asset is opened or copied."""
    exif = piexif.dump({
        "0th": {piexif.ImageIFD.Make: b"CoconutAI-Stage2F", piexif.ImageIFD.Model: b"Disposable"},
        "Exif": {piexif.ExifIFD.DateTimeOriginal: b"2026:11:07 00:00:00"},
        "GPS": {piexif.GPSIFD.GPSLatitudeRef:b"N", piexif.GPSIFD.GPSLatitude:((7,1),(18,1),(0,1)),
                piexif.GPSIFD.GPSLongitudeRef:b"E", piexif.GPSIFD.GPSLongitude:((125,1),(36,1),(0,1))},
        "1st": {}, "thumbnail": None,
    })
    xmp = b"http://ns.adobe.com/xap/1.0/\x00<x:xmpmeta><stage2f>synthetic</stage2f></x:xmpmeta>"
    icc = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    Image.new("RGB", (97, 73), (45, 121, 77)).save(path, "JPEG", quality=91, exif=exif, xmp=xmp, icc_profile=icc)
    return path.read_bytes()


def metadata(value: bytes) -> dict:
    with Image.open(__import__("io").BytesIO(value)) as image:
        exif = image.getexif()
        return {"dimensions": [image.width, image.height], "exif_make": exif.get(piexif.ImageIFD.Make) == "CoconutAI-Stage2F",
                "gps_present": bool(exif.get_ifd(piexif.ImageIFD.GPSTag)), "xmp_marker": b"stage2f" in value,
                "icc_present": bool(image.info.get("icc_profile"))}


def require_emulator():
    if os.environ.get("FIRESTORE_EMULATOR_HOST") != HOST or os.environ.get("GOOGLE_CLOUD_PROJECT") != PROJECT:
        raise RuntimeError("Refusing Stage 2F: exact localhost demo emulator environment is required")
    with socket.create_connection(("127.0.0.1", 8080), timeout=1): pass
    if firebase_admin._apps:
        raise RuntimeError("Refusing Stage 2F: Firebase must start with an isolated demo app")
    firebase_admin.initialize_app(EmulatorCredential(), options={"projectId": PROJECT})
    return firestore.client()


def main() -> None:
    if os.environ.get("INFERENCE_BACKEND") != "openvino":
        raise RuntimeError("Set INFERENCE_BACKEND=openvino for the approved Stage 2F worker test")
    db = require_emulator()
    resume_run = os.environ.get("STAGE2F_RESUME_RUN", "").strip()
    run_id = uuid4().hex
    run = Path(resume_run) if resume_run else RUN_ROOT / run_id
    if resume_run:
        if not run.is_dir() or not (run / "pre-upload.json").is_file():
            raise RuntimeError("Refusing Stage 2F resume: persisted run directory is invalid")
        pre_existing = json.loads((run / "pre-upload.json").read_text())
        public_id = str(pre_existing.get("public_id") or "")
        if not re.fullmatch(r"neuralcoco/hybrid-stage2f-test/[a-f0-9]{32}\.jpg", public_id):
            raise RuntimeError("Refusing Stage 2F resume: public ID is outside the test prefix")
        run_id = run.name
    else:
        run.mkdir(parents=True, exist_ok=False)
    original_path, download_path = run / "original.jpg", run / "worker-downloaded.jpg"
    original = original_path.read_bytes() if resume_run else fixture(original_path)
    public_id = public_id if resume_run else f"{PREFIX}/{uuid4().hex}.jpg"
    expected_context = f"hybrid_stage2f_run={run_id}|purpose=isolated-validation"
    pre = {"sha256": sha(original), "bytes": len(original), "metadata": metadata(original), "public_id": public_id}
    if not resume_run:
        (run / "pre-upload.json").write_text(json.dumps(pre, sort_keys=True))
    cloudinary_storage._configure()
    from cloudinary import api, uploader, utils
    params = {"public_id":public_id, "timestamp":int(time.time()), "type":"authenticated", "context":expected_context,
              "overwrite":"false", "unique_filename":"false", "use_filename":"false", "allowed_formats":"jpg,jpeg"}
    signature = utils.api_sign_request(params, config.CLOUDINARY_API_SECRET)
    # Cloudinary selects authenticated delivery from the signed ``type`` form
    # parameter; raw uploads themselves use the standard resource endpoint.
    upload_url = f"https://api.cloudinary.com/v1_1/{config.CLOUDINARY_CLOUD_NAME}/raw/upload"
    cors_started = time.monotonic()
    cors = requests.options(upload_url, headers={"Origin":"http://localhost:8000", "Access-Control-Request-Method":"POST", "Access-Control-Request-Headers":"content-type"}, timeout=(5,20), allow_redirects=False)
    cors_ms = (time.monotonic()-cors_started)*1000
    upload_ms = None
    if resume_run:
        upload = None
    else:
        upload_started = time.monotonic()
        with original_path.open("rb") as handle:
            response = requests.post(upload_url, data={**params, "api_key":config.CLOUDINARY_API_KEY, "signature":signature}, files={"file":("disposable.jpg",handle,"image/jpeg")}, timeout=(5,90))
        response.raise_for_status(); upload = response.json(); upload_ms=(time.monotonic()-upload_started)*1000
        if upload.get("public_id") != public_id or upload.get("resource_type") != "raw" or upload.get("type") != "authenticated":
            raise RuntimeError("Cloudinary direct upload returned an unexpected asset")
    admin_started=time.monotonic(); resource=api.resource(public_id,resource_type="raw",type="authenticated",context=True); admin_ms=(time.monotonic()-admin_started)*1000
    if (upload is not None and resource.get("asset_id") != upload.get("asset_id")) or int(resource.get("bytes") or -1) != len(original):
        raise RuntimeError("Cloudinary Admin verification failed")
    # Temporarily extend only this process's test matcher, leaving production
    # source/media validation unchanged on disk.
    test_pattern=re.compile(r"^neuralcoco/hybrid-stage2f-test/[a-f0-9]{32}\.jpg$")
    hybrid_source._PUBLIC_ID=test_pattern
    cloudinary_storage._HYBRID_SOURCE_PUBLIC_ID_PATTERN=test_pattern
    asset={"provider":"cloudinary","asset_id":str(resource["asset_id"]),"public_id":public_id,"resource_type":"raw","delivery_type":"authenticated",
           "version":int(resource["version"]),"format":str(resource.get("format") or "jpg"),"content_type":"image/jpeg","byte_size":len(original)}
    worker_download_started=time.monotonic()
    source_path=hybrid_source.download_verified_source(asset,sha(original),len(original),run)
    worker_download_ms=(time.monotonic()-worker_download_started)*1000
    download_path.write_bytes(source_path.read_bytes())
    if source_path != download_path: source_path.unlink(missing_ok=True)
    if download_path.read_bytes()!=original: raise RuntimeError("Worker raw source comparison failed")
    inference_started=time.monotonic()
    import hybrid_worker
    job={"id":run_id,"public_id":public_id,"source_sha256":sha(original),"source_bytes":len(original),"cloudinary_asset":asset}
    result=hybrid_worker._run_existing_pipeline(job,download_path)
    inference_ms=(time.monotonic()-inference_started)*1000
    store=PrefixedStore(db,run_id)
    job_id,_=store.create_upload_intent(user_id="stage2f-farmer",user_email="stage2f@example.test",idempotency_key="s"*16,public_id=public_id,source_sha256=sha(original),source_bytes=len(original),route_type="detection")
    store.mark_verified(job_id); store.claim("stage2f-worker",60)
    # Bind the exact verified raw asset returned by this one direct upload.
    store._job_ref(job_id).update({"cloudinary_asset":asset})
    store.complete(job_id,"stage2f-worker",result)
    import app.services.hybrid_record_commit as commit_module
    original_assignment=commit_module._farm_assignment; commit_module._farm_assignment=lambda *args:(None,False)
    try: commit_started=time.monotonic(); outcome=commit_completed_job(db,job_id,store=store); commit_ms=(time.monotonic()-commit_started)*1000
    finally: commit_module._farm_assignment=original_assignment
    record_ref=db.collection("users").document("stage2f-farmer").collection("detections").document(outcome["record_id"])
    record=record_ref.get()
    if not record.exists: raise RuntimeError("Canonical emulator record was not committed")
    display_started=time.monotonic(); display_bytes,content_type=media_service.read_detection_image(record.to_dict(),"original"); display_ms=(time.monotonic()-display_started)*1000
    if display_bytes!=original or content_type!="image/jpeg": raise RuntimeError("Private media display did not return the original raw source")
    source_cleanup=not source_path.exists()
    report={"run_directory":str(run),"public_id":public_id,"cors":{"status":cors.status_code,"allow_origin":cors.headers.get("Access-Control-Allow-Origin"),"ms":round(cors_ms,1)},
            "source_sha256":sha(original),"downloaded_sha256":sha(download_path.read_bytes()),"sha256_matches":sha(download_path.read_bytes())==sha(original),"bytes":len(original),"metadata":metadata(original),
            "admin_verified":True,"authenticated_raw":True,"record_id":outcome["record_id"],"record_created":outcome["created"],"record_count":1,"media_matches_original":display_bytes==original,
            "timing_ms":{"direct_upload":round(upload_ms,1) if upload_ms is not None else None,"admin_inspection":round(admin_ms,1),"worker_download":round(worker_download_ms,1),"worker_inference_total":round(inference_ms,1),"record_commit":round(commit_ms,1),"private_media_display":round(display_ms,1)},
            "source_temp_cleaned":source_cleanup,"test_asset_retained":True,"detections":len(result.get("detections") or [])}
    (run/"report.json").write_text(json.dumps(report,sort_keys=True)); print(json.dumps(report,sort_keys=True))


if __name__=="__main__": main()
