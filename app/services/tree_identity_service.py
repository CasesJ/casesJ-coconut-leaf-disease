"""Immutable reference registration and independent, audited identity decisions."""
from datetime import datetime, timezone
from uuid import uuid4

from app.domain.tree_identity import identity_evidence
from app.services import monitoring_service as store


class IdentityConflict(ValueError):
    pass


class IdentityNotFound(ValueError):
    pass


def list_trees(farmer_id: str) -> list[dict]:
    """Only explicit registrations and existing case identities; never infer from GPS."""
    parent = store._firestore().collection("users").document(farmer_id)
    trees = {doc.id: doc.to_dict() for doc in parent.collection("trees").stream()}
    for case in store.list_cases(farmer_id):
        if case.get("tree_id"):
            trees.setdefault(case["tree_id"], {"id": case["tree_id"], "farmer_id": farmer_id})
    return list(trees.values())


def commit_care_identity(farmer_id, baseline_id, selected_id, write_case):
    """Atomically bind a detection and create its care records without replacing an ID.

    Short random IDs supply readable identifiers; create() additionally
    reserves the ID globally so a collision fails rather than merging trees.
    """
    db = store._firestore()
    parent = db.collection("users").document(farmer_id)
    detection = parent.collection("detections").document(baseline_id)
    allowed = {tree["id"] for tree in list_trees(farmer_id)} if selected_id else set()
    historical_ids = {case["tree_id"] for case in store.list_cases(farmer_id)
                      if case.get("baseline_detection_id") == baseline_id and case.get("tree_id")}
    if len(historical_ids) > 1:
        raise ValueError("Historical care records have conflicting tree identities; expert review is required")
    candidate = "TREE-" + uuid4().hex[:10].upper()

    def commit(transaction):
        snap = detection.get(transaction=transaction)
        data = snap.to_dict() if snap.exists else None
        if not data or data.get("user_id", farmer_id) != farmer_id:
            raise ValueError("Baseline detection not found for farmer")
        detection_tree_id = data.get("tree_id")
        detection_tree = (parent.collection("trees").document(detection_tree_id)
                          if detection_tree_id else None)
        detection_tree_registered = bool(
            detection_tree and detection_tree.get(transaction=transaction).exists
        )
        # Some older detections contain a label parsed from the upload (for
        # example ``O1``) even though no tree was ever registered.  Such a
        # label is not a permanent identity.  Generate and reserve a real ID
        # when the first care record is created.
        existing = (detection_tree_id if detection_tree_registered else None) or next(iter(historical_ids), None)
        if historical_ids and data.get("tree_id") and data["tree_id"] not in historical_ids:
            raise ValueError("Detection and historical care record tree identities conflict")
        if existing and selected_id != existing:
            raise ValueError("Select the tree already linked to this detection")
        if selected_id and selected_id != existing and selected_id not in allowed:
            raise ValueError("Selected tree is not registered to this farmer")
        tree_id = existing or selected_id or candidate
        tree = parent.collection("trees").document(tree_id)
        registered = tree.get(transaction=transaction)
        if not registered.exists:
            registration = {"id": tree_id, "farmer_id": farmer_id,
                            "created_at": datetime.now(timezone.utc).isoformat()}
            if tree_id == candidate:
                transaction.create(db.collection("tree_id_reservations").document(tree_id), registration)
            transaction.create(tree, registration)
        if data.get("tree_id") != tree_id:
            transaction.update(detection, {"tree_id": tree_id})
        return write_case(transaction, tree_id)

    result = _transaction(commit)
    from app.services.detection_service import invalidate_expert_records_cache
    invalidate_expert_records_cache()
    return result


def _transaction(operation):
    from firebase_admin import firestore
    return firestore.transactional(operation)(store._firestore().transaction())


def owned_reference(reference, case: dict, follow_up_id=None) -> bool:
    return (isinstance(reference, dict) and bool(reference.get("id"))
            and reference.get("farmer_id") == case.get("farmer_id")
            and reference.get("case_id") == case.get("id")
            and reference.get("tree_id") == case.get("tree_id")
            and reference.get("follow_up_id") == follow_up_id)


def register_baseline(farmer_id: str, case_id: str, reference: dict) -> dict:
    """Write once. A baseline can never silently replace historical evidence."""
    parent = store._case_ref(farmer_id, case_id)

    def commit(transaction):
        snap = parent.get(transaction=transaction)
        case = snap.to_dict() if snap.exists else None
        if not case or case.get("id") != case_id or case.get("farmer_id") != farmer_id:
            raise IdentityNotFound("Monitoring case not found")
        if not owned_reference(reference, case):
            raise ValueError("Tree reference does not belong to this case")
        if case.get("tree_reference"):
            raise IdentityConflict("A baseline whole-tree reference is already registered")
        transaction.update(parent, {"tree_reference": reference})
        return reference

    return _transaction(commit)


def review_identity(farmer_id: str, case_id: str, follow_up_id: str, payload, expert_id: str) -> dict:
    parent = store._case_ref(farmer_id, case_id)
    ref = parent.collection("follow_ups").document(follow_up_id)
    event_id = uuid4().hex

    def commit(transaction):
        case_snap, snap = parent.get(transaction=transaction), ref.get(transaction=transaction)
        case = case_snap.to_dict() if case_snap.exists else None
        follow_up = snap.to_dict() if snap.exists else None
        if (not case or case.get("id") != case_id or case.get("farmer_id") != farmer_id
                or not follow_up or follow_up.get("id") != follow_up_id
                or follow_up.get("case_id") != case_id or follow_up.get("farmer_id") != farmer_id
                or follow_up.get("tree_id") != case.get("tree_id")):
            raise IdentityNotFound("Follow-up not found in this monitoring case")
        current = follow_up.get("identity_review") or {}
        if current.get("revision", 0) != payload.expected_revision:
            raise IdentityConflict("Identity review changed. Reload the case before saving.")
        baseline, photo = case.get("tree_reference"), follow_up.get("tree_reference")
        baseline_id = baseline.get("id") if baseline else None
        if baseline_id != payload.baseline_reference_id:
            raise IdentityConflict("Baseline reference changed. Reload and inspect it before saving.")
        if (baseline and not owned_reference(baseline, case)) or (photo and not owned_reference(photo, case, follow_up_id)):
            raise ValueError("Whole-tree evidence is not linked to this case")
        if payload.status == "confirmed" and not (baseline and photo):
            raise ValueError("Both baseline and follow-up whole-tree photos are required to confirm identity")
        review = {"id": event_id, "status": payload.status,
                  "explanation": (payload.explanation or "").strip() or None,
                  "reviewed_by_expert_uid": expert_id, "reviewed_at": datetime.now(timezone.utc).isoformat(),
                  "revision": current.get("revision", 0) + 1,
                  "baseline_reference_id": baseline_id,
                  "follow_up_reference_id": photo.get("id") if photo else None,
                  "gps_evidence": identity_evidence(case, follow_up)}
        transaction.update(ref, {"identity_review": review})
        transaction.create(ref.collection("identity_review_events").document(event_id), {
            **review, "farmer_id": farmer_id, "case_id": case_id, "tree_id": case["tree_id"],
            "follow_up_id": follow_up_id})
        # No treatment/outcome, disease, scheduling, or parent summary fields are written.
        return review

    return _transaction(commit)


def list_identity_reviews(farmer_id: str, case_id: str, follow_up_id: str) -> list[dict]:
    case = store.get_case(farmer_id, case_id)
    follow_up = store.get_follow_up(farmer_id, case_id, follow_up_id)
    if not case or not follow_up or follow_up.get("tree_id") != case.get("tree_id"):
        raise IdentityNotFound("Follow-up not found")
    events = store._case_ref(farmer_id, case_id).collection("follow_ups").document(follow_up_id).collection("identity_review_events").stream()
    return sorted([data for doc in events if (data := doc.to_dict())
                   and data.get("farmer_id") == farmer_id and data.get("case_id") == case_id
                   and data.get("follow_up_id") == follow_up_id], key=lambda item: item.get("revision", 0))
