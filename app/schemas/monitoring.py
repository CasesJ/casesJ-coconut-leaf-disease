"""Request shapes for expert-created monitoring cases."""

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field


class MonitoringCaseCreate(BaseModel):
    farmer_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    tree_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    baseline_detection_id: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9_-]+$")
    next_follow_up_date: date | None = None
    follow_up_interval_days: int = Field(default=6, ge=1, le=90)


class FollowUpScheduleUpdate(BaseModel):
    next_follow_up_date: date
    follow_up_interval_days: int | None = Field(default=None, ge=1, le=90)


class FollowUpReview(BaseModel):
    confirmed_outcome: Literal["improving", "no_visible_change", "worsening"]
    note: str = Field(min_length=1, max_length=2000)
    next_follow_up_date: date | None = None


class TreeIdentityReview(BaseModel):
    model_config = {"extra": "forbid"}
    status: Literal["confirmed", "uncertain", "mismatch"]
    explanation: str | None = Field(default=None, max_length=2000)
    # Optimistic concurrency prevents one expert silently replacing another review.
    expected_revision: int = Field(default=0, ge=0)
    baseline_reference_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]+$", max_length=128)


class MonitoringCase(BaseModel):
    id: str
    farmer_id: str
    tree_id: str
    baseline_detection_id: str
    # Immutable snapshot of the image references at case creation.  Legacy
    # cases omit this field and are resolved from their detection record.
    baseline_image_reference: dict[str, Any] = Field(default_factory=dict)
    baseline_prediction_snapshot: list[dict[str, Any]] = Field(default_factory=list)
    baseline_recommendation_provenance: dict[str, Any] = Field(default_factory=dict)
    baseline_gps_snapshot: dict[str, Any] | None = None
    tree_reference: dict[str, Any] | None = None
    disease: str
    treatment_record_id: str
    # Older cases have a progression inspection date. New cases can be created
    # without one while the farmer is still deciding whether to treat.
    next_follow_up_date: date | None = None
    follow_up_interval_days: int = 6
    next_follow_up_schedule_id: str | None = None
    next_post_treatment_follow_up_date: date | None = None
    next_post_treatment_application_id: str | None = None
    treatment_status: Literal["awaiting_treatment", "treatment_scheduled", "treatment_applied"] = "awaiting_treatment"
    planned_treatment_id: str | None = None
    planned_treatment_date: date | None = None
    latest_treatment_application_id: str | None = None
    latest_treatment_application_date: date | None = None
    latest_review_status: str = "no_follow_up"
    created_at: str
    created_by_expert_uid: str


class TreatmentRecord(BaseModel):
    id: str
    case_id: str
    farmer_id: str
    baseline_detection_id: str
    disease: str
    recommendation_snapshot: dict[str, Any]
    recommendation_provenance: dict[str, Any] = Field(default_factory=dict)
    recorded_at: str
    recorded_by_expert_uid: str


class TreatmentApplicationCreate(BaseModel):
    """A farmer's account of treatment actually applied to a monitored tree.

    This intentionally contains no recommendation or model fields.  The
    expert-approved recommendation remains in ``TreatmentRecord`` while this
    request captures the farmer's separate, historical application log.
    """

    treatment_performed: str = Field(min_length=1, max_length=1000)
    application_date: date
    notes: str | None = Field(default=None, max_length=2000)
    planned_treatment_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]+$", max_length=128)


class PlannedTreatmentCreate(BaseModel):
    planned_date: date
    notes: str | None = Field(default=None, max_length=2000)


class PlannedTreatmentRecord(BaseModel):
    id: str
    case_id: str
    farmer_id: str
    planned_date: date
    notes: str | None = None
    status: Literal["scheduled", "applied", "superseded"] = "scheduled"
    created_at: str
    created_by_farmer_uid: str
    applied_application_id: str | None = None


class TreatmentApplicationRecord(BaseModel):
    """Immutable audit record stored beneath a monitoring case."""

    id: str
    case_id: str
    farmer_id: str
    treatment_performed: str
    application_date: date
    notes: str | None = None
    planned_treatment_id: str | None = None
    follow_up_interval_days: int = 6
    first_follow_up_due_date: date
    created_at: str
    recorded_by_farmer_uid: str


class FollowUpScheduleRecord(BaseModel):
    id: str
    case_id: str
    farmer_id: str
    schedule_type: Literal["disease_progression", "post_treatment"]
    due_date: date
    follow_up_interval_days: int
    treatment_application_id: str | None = None
    created_at: str
    created_by_uid: str
    created_by_role: Literal["expert", "farmer", "system"]
    status: Literal["scheduled", "inspection_recorded", "superseded"] = "scheduled"


class FollowUpRecord(BaseModel):
    id: str
    case_id: str
    farmer_id: str
    tree_id: str
    baseline_detection_id: str
    treatment_record_id: str
    treatment_application_id: str | None = None
    disease: str
    follow_up_schedule_id: str | None = None
    created_at: str
    filename: str
    predictions: list[dict[str, Any]]
    image_url: str
    annotated_image_url: str
    original_storage_path: str
    annotated_storage_path: str
    gps_data: dict[str, Any] | None = None
    review_status: str = "pending_expert_review"
    preliminary_status: str = "needs_review"
    tree_reference: dict[str, Any] | None = None
    identity_baseline_reference_id: str | None = None
    identity_review: dict[str, Any] | None = None
