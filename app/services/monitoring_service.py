"""Firestore persistence for treatment cases and unbounded follow-up records."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from urllib.parse import unquote, urlsplit
from zoneinfo import ZoneInfo

from app.core.config import VERIFIED_STATUS
from app.domain.detection import (_extract_primary_disease, apply_record_defaults,
                                  normalize_verification_status)
from app.schemas.monitoring import MonitoringCase, TreatmentRecord
from app.services.detection_service import find_record_for_user
from app.domain.tree_identity import identity_evidence, normalize_gps

_PHILIPPINE_TZ = ZoneInfo("Asia/Manila")


def _audit_timestamp() -> str:
    """Use a server-generated, explicitly Philippine-local audit timestamp."""
    return datetime.now(_PHILIPPINE_TZ).isoformat()


def _timestamp_sort_key(value: object) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return datetime.min.replace(tzinfo=timezone.utc)


def _storage_path_from_url(value: object) -> str | None:
    """Extract a Firebase object key only when it is present in a stored URL."""
    url = str(value or "")
    path = urlsplit(url).path
    if "/o/" not in path:
        return None
    object_name = unquote(path.split("/o/", 1)[1]).strip("/")
    return object_name or None


def _baseline_image_reference(baseline: dict, baseline_id: str) -> dict:
    """Take an immutable image-reference snapshot for a new monitoring case."""
    original_url = baseline.get("image_url")
    annotated_url = baseline.get("annotated_image_url")
    return {
        "detection_id": baseline_id,
        "image_url": original_url,
        "annotated_image_url": annotated_url,
        "original_storage_path": baseline.get("original_storage_path") or _storage_path_from_url(original_url),
        "annotated_storage_path": baseline.get("annotated_storage_path") or _storage_path_from_url(annotated_url),
        "filename": baseline.get("filename"),
        "timestamp": baseline.get("timestamp"),
    }


def _baseline_predictions(baseline: dict) -> list[dict]:
    """Copy saved model outputs without deriving disease or severity metadata."""
    predictions = baseline.get("detections") or baseline.get("inference_results") or []
    if not isinstance(predictions, list):
        return []
    return deepcopy([prediction for prediction in predictions if isinstance(prediction, dict)])


def _recommendation_provenance(baseline: dict, snapshot: dict) -> dict:
    """Keep only recommendation provenance already present on the source record."""
    provenance = {}
    for key in ("source", "model_used", "recommendation_scope", "updated_at", "expert_override"):
        if key in snapshot and snapshot[key] is not None:
            provenance[key] = deepcopy(snapshot[key])
    if baseline.get("recommendation_source") is not None:
        provenance["record_recommendation_source"] = baseline["recommendation_source"]
    return provenance


def _firestore():
    # Import the existing Firebase Admin initializer before obtaining a client.
    import app.infrastructure.firebase.admin  # noqa: F401, PLC0415
    from firebase_admin import firestore  # noqa: PLC0415

    return firestore.client()


def _case_ref(farmer_id: str, case_id: str):
    return _firestore().collection("users").document(farmer_id).collection("monitoring_cases").document(case_id)


def create_case(farmer_id: str, tree_id: str, baseline_id: str, next_date: str | None,
                expert_id: str, interval_days: int = 6) -> dict:
    baseline = find_record_for_user(farmer_id, baseline_id)
    if not baseline or str(baseline.get("user_id") or "") != farmer_id:
        raise ValueError("Baseline detection not found for farmer")
    baseline = apply_record_defaults(dict(baseline)) or baseline
    if (str(baseline.get("source") or "upload").lower() != "upload"
            or normalize_verification_status(baseline) != VERIFIED_STATUS):
        raise ValueError("Baseline detection requires expert verification")
    disease, _ = _extract_primary_disease(baseline)
    if not disease or disease.strip().lower() == "healthy":
        raise ValueError("Baseline must identify a disease")
    snapshot = baseline.get("recommendation_snapshot")
    if not isinstance(snapshot, dict) or not (snapshot.get("recommendations") or {}).get("treatment"):
        raise ValueError("Baseline has no treatment recommendation")
    if snapshot.get("disease") and snapshot["disease"].strip().lower() != disease.strip().lower():
        raise ValueError("Treatment disease does not match baseline")
    existing_tree = str(baseline.get("tree_id") or "").strip()
    if existing_tree and existing_tree != tree_id:
        raise ValueError("Tree does not match baseline")

    # These snapshots intentionally capture the verified baseline as it was at
    # case creation. Later edits to a detection record must not change the
    # evidence, disease predictions, or recommendation provenance of an
    # existing treatment case.
    baseline_image_reference = _baseline_image_reference(baseline, baseline_id)
    baseline_prediction_snapshot = _baseline_predictions(baseline)
    recommendation_snapshot = deepcopy(snapshot)
    recommendation_provenance = _recommendation_provenance(baseline, recommendation_snapshot)

    now = _audit_timestamp()
    case_id, treatment_id = uuid4().hex, uuid4().hex
    case = MonitoringCase(
        id=case_id, farmer_id=farmer_id, tree_id=tree_id,
        baseline_detection_id=baseline_id, disease=disease,
        baseline_image_reference=baseline_image_reference,
        baseline_prediction_snapshot=baseline_prediction_snapshot,
        baseline_recommendation_provenance=recommendation_provenance,
        baseline_gps_snapshot=normalize_gps(baseline.get("gps_data")),
        treatment_record_id=treatment_id, next_follow_up_date=next_date,
        follow_up_interval_days=interval_days,
        created_at=now, created_by_expert_uid=expert_id,
    ).model_dump(mode="json")
    treatment = TreatmentRecord(
        id=treatment_id, case_id=case_id, farmer_id=farmer_id,
        baseline_detection_id=baseline_id, disease=disease,
        recommendation_snapshot=recommendation_snapshot,
        recommendation_provenance=recommendation_provenance,
        recorded_at=now,
        recorded_by_expert_uid=expert_id,
    ).model_dump(mode="json")
    db = _firestore()
    parent = db.collection("users").document(farmer_id)
    case_ref = parent.collection("monitoring_cases").document(case_id)
    batch = db.batch()
    batch.set(parent.collection("treatment_records").document(treatment_id), treatment)
    if next_date:
        schedule_id = uuid4().hex
        schedule = {
            "id": schedule_id, "case_id": case_id, "farmer_id": farmer_id,
            "schedule_type": "disease_progression", "due_date": next_date,
            "follow_up_interval_days": interval_days, "treatment_application_id": None,
            "created_at": now, "created_by_uid": expert_id,
            "created_by_role": "expert", "status": "scheduled",
        }
        case["next_follow_up_schedule_id"] = schedule_id
        batch.set(case_ref.collection("follow_up_schedules").document(schedule_id), schedule)
    batch.set(case_ref, case)
    batch.commit()
    return case


def get_case(farmer_id: str, case_id: str) -> dict | None:
    snapshot = _case_ref(farmer_id, case_id).get()
    if not snapshot.exists:
        return None
    case = snapshot.to_dict() or {}
    if case.get("farmer_id") != farmer_id or case.get("id") != case_id:
        return None
    return case


def list_cases(farmer_id: str) -> list[dict]:
    return [case for doc in _firestore().collection("users").document(farmer_id)
            .collection("monitoring_cases").stream()
            if (case := doc.to_dict()) and case.get("farmer_id") == farmer_id]


def list_expert_cases() -> list[dict]:
    return [case for doc in _firestore().collection_group("monitoring_cases").stream()
            if (case := doc.to_dict()) and case.get("farmer_id") and case.get("id")]


def get_overview(farmer_id: str, case_id: str) -> dict | None:
    case = get_case(farmer_id, case_id)
    if not case:
        return None
    baseline_id = case["baseline_detection_id"]
    image_reference = case.get("baseline_image_reference")
    if isinstance(image_reference, dict) and image_reference:
        # New cases use the case-owned evidence reference. Do not reread a
        # mutable detection record and accidentally replace the baseline.
        baseline = {
            "id": image_reference.get("detection_id") or baseline_id,
            "image_url": image_reference.get("image_url"),
            "annotated_image_url": image_reference.get("annotated_image_url"),
            "original_storage_path": image_reference.get("original_storage_path"),
            "annotated_storage_path": image_reference.get("annotated_storage_path"),
            "timestamp": image_reference.get("timestamp"),
            "filename": image_reference.get("filename"),
            "predictions": deepcopy(case.get("baseline_prediction_snapshot") or []),
            "recommendation_provenance": deepcopy(case.get("baseline_recommendation_provenance") or {}),
            "reference_source": "case_snapshot",
        }
    else:
        # Legacy cases were created before baseline snapshots existed. Keep
        # their original behavior while allowing them to coexist with new data.
        canonical = (_firestore().collection("users").document(farmer_id)
                     .collection("detections").document(baseline_id).get())
        legacy_baseline = canonical.to_dict() if canonical.exists else None
        if not legacy_baseline or str(legacy_baseline.get("user_id") or farmer_id) != farmer_id:
            legacy_baseline = find_record_for_user(farmer_id, baseline_id) or {}
        if legacy_baseline:
            legacy_baseline = apply_record_defaults(dict(legacy_baseline)) or legacy_baseline
        baseline = {
            "id": baseline_id,
            "image_url": legacy_baseline.get("image_url"),
            "annotated_image_url": legacy_baseline.get("annotated_image_url"),
            "timestamp": legacy_baseline.get("timestamp"),
            "filename": legacy_baseline.get("filename"),
            "predictions": _baseline_predictions(legacy_baseline),
            "recommendation_provenance": _recommendation_provenance(
                legacy_baseline, legacy_baseline.get("recommendation_snapshot") or {},
            ),
            "reference_source": "legacy_detection_record",
        }
    treatment_snapshot = (_firestore().collection("users").document(farmer_id)
                          .collection("treatment_records").document(case["treatment_record_id"]).get())
    treatment = treatment_snapshot.to_dict() if treatment_snapshot.exists else None
    follow_ups = deepcopy(list_follow_ups(farmer_id, case_id))
    # Optional response-only defaults preserve every historical document.
    for follow_up in follow_ups:
        follow_up.setdefault("identity_review", None)
        follow_up["identity_gps_evidence"] = identity_evidence(case, follow_up)
    planned_treatments = list_planned_treatments(farmer_id, case_id)
    follow_up_schedules = list_follow_up_schedules(farmer_id, case_id)
    return {"case": case, "baseline": baseline, "treatment": treatment,
            "treatment_applications": list_treatment_applications(farmer_id, case_id),
            "planned_treatments": planned_treatments,
            "follow_up_schedules": follow_up_schedules,
            "follow_ups": follow_ups}


def save_planned_treatment(farmer_id: str, case_id: str, planned: dict) -> None:
    """Append a farmer's planned date without recording an application."""
    if planned.get("farmer_id") != farmer_id or planned.get("case_id") != case_id:
        raise ValueError("Planned treatment does not belong to this monitoring case")
    case_ref = _case_ref(farmer_id, case_id)
    db = _firestore()
    batch = db.batch()
    case = get_case(farmer_id, case_id) or {}
    previous_id = case.get("planned_treatment_id")
    if previous_id:
        previous_ref = case_ref.collection("planned_treatments").document(previous_id)
        previous_snapshot = previous_ref.get()
        if previous_snapshot.exists:
            previous = previous_snapshot.to_dict() or {}
            if previous.get("status") == "scheduled":
                batch.update(previous_ref, {
                    "status": "superseded", "superseded_at": planned["created_at"],
                    "superseded_by_treatment_id": planned["id"],
                })
    batch.create(case_ref.collection("planned_treatments").document(planned["id"]), planned)
    batch.update(case_ref, {
        "treatment_status": "treatment_scheduled",
        "planned_treatment_id": planned["id"],
        "planned_treatment_date": planned["planned_date"],
        "planned_treatment_updated_at": planned["created_at"],
    })
    batch.commit()


def get_planned_treatment(farmer_id: str, case_id: str, planned_id: str) -> dict | None:
    snapshot = (_case_ref(farmer_id, case_id).collection("planned_treatments")
                .document(planned_id).get())
    if not snapshot.exists:
        return None
    planned = snapshot.to_dict() or {}
    if (planned.get("farmer_id") != farmer_id or planned.get("case_id") != case_id
            or planned.get("id") != planned_id):
        return None
    return planned


def list_planned_treatments(farmer_id: str, case_id: str) -> list[dict]:
    records = [doc.to_dict() or {} for doc in _case_ref(farmer_id, case_id)
               .collection("planned_treatments").stream()]
    owned_records = [record for record in records
                     if record.get("farmer_id") == farmer_id and record.get("case_id") == case_id]
    today = datetime.now(_PHILIPPINE_TZ).date().isoformat()
    for record in owned_records:
        record["is_overdue"] = (record.get("status", "scheduled") == "scheduled"
                                and str(record.get("planned_date") or "") < today)
    return sorted(owned_records, key=lambda record: (
        str(record.get("planned_date") or ""), str(record.get("created_at") or ""),
        str(record.get("id") or ""),
    ))


def save_treatment_application(farmer_id: str, case_id: str, application: dict,
                               follow_up_schedule: dict | None = None) -> None:
    """Append one farmer-entered application record without changing advice history."""
    if (application.get("farmer_id") != farmer_id
            or application.get("case_id") != case_id):
        raise ValueError("Treatment application does not belong to this monitoring case")
    # A case-owned subcollection keeps this log distinct from the immutable
    # expert recommendation record and avoids mutating its historical content.
    case_ref = _case_ref(farmer_id, case_id)
    db = _firestore()
    batch = db.batch()
    batch.create(case_ref.collection("treatment_applications").document(application["id"]), application)
    if follow_up_schedule:
        if (follow_up_schedule.get("farmer_id") != farmer_id
                or follow_up_schedule.get("case_id") != case_id
                or follow_up_schedule.get("treatment_application_id") != application.get("id")):
            raise ValueError("Post-treatment schedule does not match its application")
        batch.create(case_ref.collection("follow_up_schedules")
                     .document(follow_up_schedule["id"]), follow_up_schedule)
    case = get_case(farmer_id, case_id) or {}
    current_plan_id = case.get("planned_treatment_id")
    planned_id = application.get("planned_treatment_id")
    latest_application_date = str(case.get("latest_treatment_application_date") or "")
    if str(application.get("application_date") or "") >= latest_application_date:
        changes = {
            "latest_treatment_application_id": application["id"],
            "latest_treatment_application_date": application["application_date"],
            "next_post_treatment_follow_up_date": (follow_up_schedule or {}).get("due_date"),
            "next_post_treatment_application_id": application["id"],
            "treatment_updated_at": application["created_at"],
        }
    else:
        changes = {"treatment_updated_at": application["created_at"]}
    if not current_plan_id or current_plan_id == planned_id:
        changes["treatment_status"] = "treatment_applied"
        if current_plan_id == planned_id:
            changes["planned_treatment_id"] = None
            changes["planned_treatment_date"] = None
    batch.update(case_ref, changes)
    if planned_id:
        batch.update(case_ref.collection("planned_treatments").document(planned_id), {
            "status": "applied", "applied_application_id": application["id"],
            "applied_at": application["created_at"],
        })
    batch.commit()


def list_treatment_applications(farmer_id: str, case_id: str) -> list[dict]:
    """Return a case's application log in chronological order.

    Sorting after retrieval avoids a composite Firestore index requirement and
    keeps an empty collection fully compatible with monitoring cases created
    before treatment applications were introduced.
    """
    records = [doc.to_dict() or {} for doc in _case_ref(farmer_id, case_id)
               .collection("treatment_applications").stream()]
    owned_records = [record for record in records
                     if record.get("farmer_id") == farmer_id and record.get("case_id") == case_id]
    return sorted(owned_records, key=lambda record: (
        str(record.get("application_date") or ""),
        _timestamp_sort_key(record.get("created_at")),
        str(record.get("id") or ""),
    ))


def list_follow_up_schedules(farmer_id: str, case_id: str) -> list[dict]:
    records = [doc.to_dict() or {} for doc in _case_ref(farmer_id, case_id)
               .collection("follow_up_schedules").stream()]
    owned_records = [record for record in records
                     if record.get("farmer_id") == farmer_id and record.get("case_id") == case_id]
    return sorted(owned_records, key=lambda record: (
        str(record.get("due_date") or ""), _timestamp_sort_key(record.get("created_at")),
        str(record.get("id") or ""),
    ))


def get_follow_up_schedule(farmer_id: str, case_id: str, schedule_id: str) -> dict | None:
    snapshot = (_case_ref(farmer_id, case_id).collection("follow_up_schedules")
                .document(schedule_id).get())
    if not snapshot.exists:
        return None
    schedule = snapshot.to_dict() or {}
    if (schedule.get("farmer_id") != farmer_id or schedule.get("case_id") != case_id
            or schedule.get("id") != schedule_id):
        return None
    return schedule


def get_treatment_application(farmer_id: str, case_id: str, application_id: str) -> dict | None:
    snapshot = (_case_ref(farmer_id, case_id).collection("treatment_applications")
                .document(application_id).get())
    if not snapshot.exists:
        return None
    application = snapshot.to_dict() or {}
    if (application.get("farmer_id") != farmer_id or application.get("case_id") != case_id
            or application.get("id") != application_id):
        return None
    return application


def save_follow_up(farmer_id: str, case_id: str, follow_up: dict) -> None:
    if follow_up.get("farmer_id") != farmer_id or follow_up.get("case_id") != case_id:
        raise ValueError("Follow-up does not belong to this monitoring case")
    # Each follow-up is a separate document; the parent never grows with history.
    parent = _case_ref(farmer_id, case_id)
    db = _firestore()
    batch = db.batch()
    batch.create(parent.collection("follow_ups").document(follow_up["id"]), follow_up)
    batch.update(parent, {"latest_follow_up_id": follow_up["id"],
                          "last_inspection_at": follow_up["created_at"],
                          "latest_review_status": "pending_expert_review",
                          "latest_confirmed_outcome": None})
    schedule_id = follow_up.get("follow_up_schedule_id")
    if schedule_id:
        schedule_ref = parent.collection("follow_up_schedules").document(schedule_id)
        snapshot = schedule_ref.get()
        if snapshot.exists:
            schedule = snapshot.to_dict() or {}
            if schedule.get("farmer_id") == farmer_id and schedule.get("case_id") == case_id:
                batch.update(schedule_ref, {
                    "status": "inspection_recorded",
                    "last_inspection_id": follow_up["id"],
                    "last_inspection_at": follow_up["created_at"],
                })
    batch.commit()


def list_follow_ups(farmer_id: str, case_id: str) -> list[dict]:
    records = [doc.to_dict() or {} for doc in _case_ref(farmer_id, case_id)
               .collection("follow_ups").stream()]
    return sorted(records, key=lambda record: (
        _timestamp_sort_key(record.get("created_at")), str(record.get("id") or "")))


def get_follow_up(farmer_id: str, case_id: str, follow_up_id: str) -> dict | None:
    """Return one persisted follow-up for safe post-failure cleanup checks."""
    snapshot = _case_ref(farmer_id, case_id).collection("follow_ups").document(follow_up_id).get()
    if not snapshot.exists:
        return None
    follow_up = snapshot.to_dict() or {}
    if follow_up.get("farmer_id") != farmer_id or follow_up.get("case_id") != case_id:
        return None
    return follow_up


def update_next_follow_up(farmer_id: str, case_id: str, next_date: str,
                          expert_id: str, interval_days: int | None = None) -> dict | None:
    case = get_case(farmer_id, case_id)
    if not case:
        return None
    now = _audit_timestamp()
    interval = interval_days or int(case.get("follow_up_interval_days") or 6)
    schedule_id = uuid4().hex
    changes = {"next_follow_up_date": next_date,
               "next_follow_up_schedule_id": schedule_id,
               "schedule_updated_at": now,
               "schedule_updated_by_expert_uid": expert_id,
               "follow_up_interval_days": interval}
    case_ref = _case_ref(farmer_id, case_id)
    db = _firestore()
    batch = db.batch()
    previous_id = case.get("next_follow_up_schedule_id")
    if previous_id:
        old_ref = case_ref.collection("follow_up_schedules").document(previous_id)
        if old_ref.get().exists:
            batch.update(old_ref, {"status": "superseded", "superseded_at": now,
                                   "superseded_by_schedule_id": schedule_id})
    schedule = {
        "id": schedule_id, "case_id": case_id, "farmer_id": farmer_id,
        "schedule_type": "disease_progression", "due_date": next_date,
        "follow_up_interval_days": interval, "treatment_application_id": None,
        "created_at": now, "created_by_uid": expert_id,
        "created_by_role": "expert", "status": "scheduled",
    }
    batch.create(case_ref.collection("follow_up_schedules").document(schedule_id), schedule)
    batch.update(case_ref, changes)
    batch.commit()
    return {**case, **changes}


def review_follow_up(farmer_id: str, case_id: str, follow_up_id: str,
                     outcome: str, note: str, expert_id: str,
                     next_date: str | None = None) -> dict | None:
    case = get_case(farmer_id, case_id)
    if not case:
        return None
    ref = _case_ref(farmer_id, case_id).collection("follow_ups").document(follow_up_id)
    snap = ref.get()
    if not snap.exists:
        return None
    follow_up = snap.to_dict() or {}
    if follow_up.get("farmer_id") != farmer_id or follow_up.get("case_id") != case_id:
        return None
    now = datetime.now(_PHILIPPINE_TZ)
    interval = int(case.get("follow_up_interval_days") or 6)
    treatment_application_id = follow_up.get("treatment_application_id")
    if next_date:
        due = next_date
    elif treatment_application_id:
        inspected_at = follow_up.get("created_at")
        try:
            inspection_date = datetime.fromisoformat(str(inspected_at).replace("Z", "+00:00"))
            if inspection_date.tzinfo is None:
                inspection_date = inspection_date.replace(tzinfo=timezone.utc)
            inspection_date = inspection_date.astimezone(_PHILIPPINE_TZ).date()
        except (TypeError, ValueError):
            inspection_date = now.date()
        due = (inspection_date + timedelta(days=interval)).isoformat()
    else:
        # Preserve the legacy disease-progression behavior for old and generic
        # inspections: the expert's review starts the next interval.
        due = (now.date() + timedelta(days=interval)).isoformat()
    changes = {"review_status": "expert_confirmed", "confirmed_outcome": outcome,
               "expert_note": note, "reviewed_by_expert_uid": expert_id,
               "reviewed_at": now.isoformat()}
    db = _firestore()
    batch = db.batch()
    batch.update(ref, changes)
    case_ref = _case_ref(farmer_id, case_id)
    follow_up_schedule_id = None
    is_latest = case.get("latest_follow_up_id") == follow_up_id
    if treatment_application_id or is_latest:
        follow_up_schedule_id = uuid4().hex
        schedule_type = "post_treatment" if treatment_application_id else "disease_progression"
        schedule = {
            "id": follow_up_schedule_id, "case_id": case_id, "farmer_id": farmer_id,
            "schedule_type": schedule_type, "due_date": due,
            "follow_up_interval_days": interval,
            "treatment_application_id": treatment_application_id,
            "created_at": now.isoformat(), "created_by_uid": expert_id,
            "created_by_role": "expert", "status": "scheduled",
        }
        source_schedule_ids = {follow_up.get("follow_up_schedule_id")}
        if is_latest and not treatment_application_id:
            source_schedule_ids.add(case.get("next_follow_up_schedule_id"))
        for source_schedule_id in filter(None, source_schedule_ids):
            source_ref = case_ref.collection("follow_up_schedules").document(source_schedule_id)
            source_snapshot = source_ref.get()
            if source_snapshot.exists:
                source_schedule = source_snapshot.to_dict() or {}
                if source_schedule.get("farmer_id") == farmer_id and source_schedule.get("case_id") == case_id:
                    batch.update(source_ref, {
                        "status": "superseded", "superseded_at": now.isoformat(),
                        "superseded_by_schedule_id": follow_up_schedule_id,
                    })
        batch.create(case_ref.collection("follow_up_schedules").document(follow_up_schedule_id), schedule)
    if is_latest:
        latest_changes = {
            "latest_review_status": "expert_confirmed",
            "latest_confirmed_outcome": outcome,
        }
        if treatment_application_id and case.get("latest_treatment_application_id") == treatment_application_id:
            latest_changes.update({
                "next_post_treatment_follow_up_date": due,
                "next_post_treatment_application_id": treatment_application_id,
            })
        else:
            latest_changes.update({
                "next_follow_up_date": due,
                "next_follow_up_schedule_id": follow_up_schedule_id,
                "schedule_updated_at": now.isoformat(),
                "schedule_updated_by_expert_uid": expert_id,
            })
        batch.update(case_ref, latest_changes)
    batch.set(ref.collection("review_events").document(uuid4().hex), {
        **changes, "follow_up_id": follow_up_id, "farmer_id": farmer_id, "case_id": case_id})
    batch.commit()
    return {**follow_up, **changes, "next_follow_up_date": due,
            "follow_up_schedule_id": follow_up_schedule_id}
