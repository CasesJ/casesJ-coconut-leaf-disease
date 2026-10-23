"""Immutable reference registration and independent, audited identity decisions."""
from datetime import datetime, timezone
from uuid import uuid4

from app.domain.tree_identity import identity_evidence
from app.services import monitoring_service as store


class IdentityConflict(ValueError):
    pass


class IdentityNotFound(ValueError):
    pass


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
