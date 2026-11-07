"""Idempotently project completed hybrid jobs into canonical detection records."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

from app.core.config import UPLOAD_RECORD_CONFIDENCE_THRESHOLD
from app.domain.detection import _serialize_detection_payload
from app.services.detection_service import _annotated_detections, _record_ref, invalidate_expert_records_cache
from app.services.farm_boundary_service import list_boundaries, match_area
from app.services.farm_service import set_initial_farm_center
from app.services.hybrid_jobs import COMPLETED, HybridJobError, HybridJobStore, _snapshot_dict


class HybridRecordCommitError(RuntimeError):
    pass


def _thumbnail_asset(result: dict, job_id: str) -> dict | None:
    """Accept only the bounded worker-created authenticated image derivative."""
    asset = result.get("thumbnail_asset")
    if not isinstance(asset, dict):
        return None
    required = {"provider", "asset_id", "public_id", "resource_type", "delivery_type",
                "version", "format", "content_type", "byte_size"}
    if (not required.issubset(asset) or asset.get("provider") != "cloudinary"
            or asset.get("resource_type") != "image" or asset.get("delivery_type") != "authenticated"
            or asset.get("content_type") != "image/jpeg"
            or asset.get("public_id") != f"neuralcoco/detection-thumbnail/{job_id}"
            or asset.get("format") not in {"jpg", "jpeg"}
            or not isinstance(asset.get("byte_size"), int)
            or not 0 < asset["byte_size"] <= 1_000_000):
        raise HybridRecordCommitError("Completed hybrid job has an invalid thumbnail asset")
    return {key: deepcopy(asset[key]) for key in required if key in asset}


def _result_record_inputs(job: dict) -> tuple[list[dict], dict, dict, dict, dict | None]:
    result = job.get("result_reference")
    asset = job.get("cloudinary_asset")
    if (not isinstance(result, dict) or result.get("schema") != "hybrid-inference-result-v1"
            or not isinstance(asset, dict) or asset.get("public_id") != job.get("public_id")
            or not isinstance(result.get("detections"), list)
            or not isinstance(result.get("source_dimensions"), dict)
            or not isinstance(result.get("source_identity"), dict)
            or result["source_identity"].get("sha256") != job.get("source_sha256")
            or result["source_identity"].get("bytes") != job.get("source_bytes")):
        raise HybridRecordCommitError("Completed hybrid job has an invalid result")
    try:
        width, height = int(result["source_dimensions"]["width"]), int(result["source_dimensions"]["height"])
    except (KeyError, TypeError, ValueError):
        raise HybridRecordCommitError("Completed hybrid job has invalid image dimensions") from None
    if width < 1 or height < 1:
        raise HybridRecordCommitError("Completed hybrid job has invalid image dimensions")
    detections = [dict(item) for item in result["detections"] if isinstance(item, dict)
                  and float(item.get("confidence", -1)) >= UPLOAD_RECORD_CONFIDENCE_THRESHOLD]
    gps = result.get("gps") if isinstance(result.get("gps"), dict) else {}
    gps_data = {
        "latitude": gps.get("latitude"), "longitude": gps.get("longitude"),
        "accuracy": gps.get("accuracy"), "source": gps.get("source", "unknown"),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    # The standard private media path reads this authenticated raw reference;
    # no signed URL, context, or credential enters the record document.
    original_asset = {
        key: deepcopy(asset[key]) for key in (
            "provider", "asset_id", "public_id", "resource_type", "delivery_type",
            "version", "format", "content_type", "byte_size",
        ) if key in asset
    }
    original_asset.update({
        "sha256": job["source_sha256"], "source_sha256": job["source_sha256"],
        "source_byte_size": job["source_bytes"], "storage_representation": "authenticated_raw_source",
    })
    thumbnail = _thumbnail_asset(result, job["id"])
    # Older completed jobs may predate the worker derivative.  They can still
    # commit safely: presentation omits the thumbnail URL and streams the
    # original, while Render's thumbnail/annotation decode fallbacks are shut.
    return detections, gps_data, {"width": width, "height": height}, original_asset, thumbnail


def _farm_assignment(user_id: str, gps_data: dict, operation_id: str) -> tuple[int | None, bool]:
    lat, lng = gps_data.get("latitude"), gps_data.get("longitude")
    if gps_data.get("source") not in {"exif", "dji_exif"} or lat is None or lng is None:
        return None, False
    try:
        set_initial_farm_center(user_id, lat, lng, str(gps_data["source"]), operation_id=operation_id)
        return match_area(list_boundaries(user_id), lat, lng)
    except Exception:
        # The detection remains valid when optional boundary enrichment is
        # unavailable, matching the established Render-local upload behavior.
        return None, False


def commit_completed_job(db, job_id: str, *, store: HybridJobStore | None = None) -> dict:
    """Create at most one canonical record and atomically mark its job committed."""
    store = store or HybridJobStore(db)
    job = store.get_job(job_id)
    if not job or job.get("status") != COMPLETED:
        raise HybridRecordCommitError("Hybrid job is not ready for record commit")
    if job.get("record_commit_status") == "committed" and job.get("record_id"):
        return {"record_id": job["record_id"], "created": False}
    detections, gps_data, dimensions, original_asset, thumbnail_asset = _result_record_inputs(job)
    if not detections:
        def no_record(txn):
            current = _snapshot_dict(txn.get(store._job_ref(job_id)))
            if not current or current.get("status") != COMPLETED:
                raise HybridRecordCommitError("Hybrid job changed before record commit")
            txn.update(store._job_ref(job_id), {"record_commit_status": "no_record", "updated_at": store.now()})
            return {"record_id": None, "created": False}
        return store._run(no_record)

    record_id = f"hybrid_{job_id}"
    record_ref = _record_ref(db, job["user_id"], record_id)
    if record_ref is None:
        raise HybridRecordCommitError("Hybrid job has invalid ownership")
    farm_area_id, farm_area_conflict = _farm_assignment(job["user_id"], gps_data, f"hybrid-{job_id}")
    record = _serialize_detection_payload(
        record_id=record_id, user_id=job["user_id"], email=str(job.get("user_email") or ""),
        detections=_annotated_detections(detections, (dimensions["width"], dimensions["height"])),
        gps_data=gps_data, filename=str(job["public_id"]).rsplit("/", 1)[-1], source="upload",
        image_metadata=deepcopy(job["result_reference"].get("image_metadata") or {}),
    )
    image_assets = {"original": original_asset}
    if thumbnail_asset is not None:
        image_assets["thumbnail"] = thumbnail_asset
    record.update({
        "image_assets": image_assets, "image_dimensions": dimensions,
        "annotation_format": "xyxy-normalized-v1", "upload_idempotency_key": job["idempotency_key"],
        "hybrid_job_id": job_id,
    })
    if farm_area_id is not None:
        record["farm_area_id"] = int(farm_area_id)
    if farm_area_conflict:
        record["farm_area_conflict"] = True

    def transaction_commit(txn):
        current = _snapshot_dict(txn.get(store._job_ref(job_id)))
        if not current or current.get("status") != COMPLETED:
            raise HybridRecordCommitError("Hybrid job changed before record commit")
        existing = _snapshot_dict(txn.get(record_ref))
        if existing:
            if existing.get("hybrid_job_id") != job_id or existing.get("user_id") != job["user_id"]:
                raise HybridRecordCommitError("Hybrid record identity conflict")
            txn.update(store._job_ref(job_id), {
                "record_id": record_id, "record_commit_status": "committed",
                "record_committed_at": store.now(), "updated_at": store.now(),
            })
            return {"record_id": record_id, "created": False}
        txn.create(record_ref, record)
        txn.update(store._job_ref(job_id), {
            "record_id": record_id, "record_commit_status": "committed",
            "record_committed_at": store.now(), "updated_at": store.now(),
        })
        return {"record_id": record_id, "created": True}

    outcome = store._run(transaction_commit)
    if outcome["created"]:
        invalidate_expert_records_cache()
    return outcome


def reconcile_completed_jobs(db, limit: int = 10, *, store: HybridJobStore | None = None) -> list[dict]:
    """Retry interrupted commits; never scans or changes non-completed jobs."""
    store = store or HybridJobStore(db)
    collection = store._collection()
    if not hasattr(collection, "where"):
        return []
    from google.cloud.firestore_v1.base_query import FieldFilter
    # Query the pending marker directly.  A status-only first page can be
    # filled by old committed jobs forever and starve interrupted commits.
    snapshots = collection.where(
        filter=FieldFilter("record_commit_status", "==", "pending")
    ).limit(max(1, min(limit, 25))).stream()
    outcomes = []
    for snapshot in snapshots:
        value = snapshot.to_dict() or {}
        if value.get("status") != COMPLETED:
            continue
        outcomes.append(commit_completed_job(db, snapshot.id, store=store))
    return outcomes
