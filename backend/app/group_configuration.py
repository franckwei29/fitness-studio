"""Version-bound group configuration proposals, review, and execution."""

from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select

from app.admission import fingerprint, view
from app.database import transaction
from app.models import Approval, AuditEvent, ChangeRequest, MemberGroup, User, now
from app.policy import PROFILE_FIELDS
from app.security import owner

router = APIRouter(prefix="/api/owner")


class Capabilities(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    training: bool
    nutrition: bool
    substitutions: bool
    reassessment_reminders: bool
    continuity_reminders: bool
    weekly_summary: bool

    @model_validator(mode="after")
    def prerequisites(self):
        if self.substitutions and not self.nutrition:
            raise ValueError("Food substitution requires nutrition.")
        return self


class GroupConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    capabilities: Capabilities
    field_permissions: dict[str, list[str]] = Field(default_factory=dict)
    review_required: bool = False
    agent_versions: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def field_boundaries(self):
        for role, fields in self.field_permissions.items():
            if role not in PROFILE_FIELDS or not set(fields) <= PROFILE_FIELDS[role]:
                raise ValueError("Group fields must remain within fixed role permissions.")
        return self


class Proposal(BaseModel):
    expected_version: int = Field(ge=1)
    configuration: GroupConfiguration
    reason: str = Field(min_length=1, max_length=3000)


@router.get("/groups")
def groups(user: User = Depends(owner)):
    with transaction() as db:
        return [view(row) for row in db.scalars(select(MemberGroup).order_by(MemberGroup.name))]


@router.get("/group-policy")
def group_policy(user: User = Depends(owner)):
    """Expose the same fixed boundaries used to validate configuration changes."""
    return {role: sorted(fields) for role, fields in PROFILE_FIELDS.items()}


@router.get("/changes")
def changes(user: User = Depends(owner)):
    with transaction() as db:
        return [
            view(row) for row in db.scalars(select(ChangeRequest).order_by(ChangeRequest.created_at.desc()))
        ]


@router.post("/groups/{identifier}/changes", status_code=201)
def propose(identifier: str, body: Proposal, user: User = Depends(owner)):
    with transaction() as db:
        group = db.scalar(select(MemberGroup).where(MemberGroup.id == identifier).with_for_update())
        if not group:
            raise HTTPException(404, "Group not found.")
        if group.version != body.expected_version:
            raise HTTPException(409, "The group configuration has changed.")
        candidate = body.configuration.model_dump()
        if candidate["agent_versions"] != group.configuration.get("agent_versions", {}):
            raise HTTPException(422, "Agent version changes require the model evaluation workflow.")
        baseline = {"version": group.version, "configuration": group.configuration}
        digest = fingerprint({"group_id": group.id, "baseline": baseline, "candidate": candidate})
        row = ChangeRequest(
            kind="GroupConfiguration",
            group_id=group.id,
            baseline=baseline,
            candidate=candidate,
            fingerprint=digest,
            status="PendingApproval",
            reason=body.reason,
            evaluation={
                "passed": True,
                "checks": ["Schema", "RoleBoundaries", "FeaturePrerequisites"],
                "model_evaluation_required": False,
            },
        )
        db.add(row)
        db.flush()
        db.add(
            Approval(
                subject_kind="GroupConfiguration",
                subject_id=row.id,
                subject_version=1,
                fingerprint=digest,
                scope={"group_id": group.id},
                context={"baseline": baseline, "candidate": candidate, "evaluation": row.evaluation},
                expires_at=now() + timedelta(hours=24),
            )
        )
        db.add(
            AuditEvent(
                actor_id=user.id,
                event_type="configuration.proposed",
                subject_id=row.id,
                details={"group_id": group.id, "fingerprint": digest},
            )
        )
        return view(row)


class Review(BaseModel):
    decision: Literal["Approved", "Revise", "Rejected"]
    fingerprint: str
    comment: str = Field(default="", max_length=3000)


@router.post("/changes/{identifier}/review")
def review(identifier: str, body: Review, user: User = Depends(owner)):
    with transaction() as db:
        change = db.scalar(select(ChangeRequest).where(ChangeRequest.id == identifier).with_for_update())
        if not change or change.kind != "GroupConfiguration":
            raise HTTPException(404, "Configuration change not found.")
        approval = db.scalar(
            select(Approval)
            .where(Approval.subject_id == identifier, Approval.fingerprint == body.fingerprint)
            .with_for_update()
        )
        if not approval or change.fingerprint != body.fingerprint or approval.expires_at <= now():
            raise HTTPException(409, "The reviewed candidate is stale or expired.")
        if approval.decision != "Pending":
            if approval.decision == body.decision:
                return view(change)
            raise HTTPException(409, "This review already has a decision.")
        approval.decision, approval.decided_by, approval.decided_at = body.decision, user.id, now()
        approval.comment = body.comment
        change.status = body.decision
        db.add(
            AuditEvent(
                actor_id=user.id,
                event_type="configuration.reviewed",
                subject_id=change.id,
                details={"decision": body.decision, "fingerprint": change.fingerprint},
            )
        )
        return view(change)


@router.post("/changes/{identifier}/execute")
def execute(identifier: str, user: User = Depends(owner)):
    with transaction() as db:
        change = db.scalar(select(ChangeRequest).where(ChangeRequest.id == identifier).with_for_update())
        if not change or change.kind != "GroupConfiguration":
            raise HTTPException(404, "Configuration change not found.")
        if change.status == "Executed":
            return view(change)
        approval = db.scalar(
            select(Approval).where(
                Approval.subject_id == change.id,
                Approval.fingerprint == change.fingerprint,
                Approval.decision == "Approved",
                Approval.expires_at > now(),
            )
        )
        if change.status != "Approved" or not approval:
            raise HTTPException(409, "A current approval is required.")
        from app.rollback_recovery import complete_recovery_change, verify_recovery_change

        recovery_release = verify_recovery_change(db, change, approval)
        group = db.scalar(select(MemberGroup).where(MemberGroup.id == change.group_id).with_for_update())
        if (
            group.version != change.baseline["version"]
            or group.configuration != change.baseline["configuration"]
        ):
            raise HTTPException(409, "The approved baseline has changed. Submit a new candidate.")
        candidate = GroupConfiguration.model_validate(change.candidate).model_dump()
        if not recovery_release and candidate["agent_versions"] != group.configuration.get(
            "agent_versions", {}
        ):
            raise HTTPException(409, "Agent changes require a release or reviewed rollback recovery.")
        digest = fingerprint({"group_id": group.id, "baseline": change.baseline, "candidate": candidate})
        if digest != change.fingerprint:
            raise HTTPException(409, "The approved candidate has changed.")
        group.configuration = candidate
        group.version += 1
        change.status = "Executed"
        change.execution = {
            "group_version": group.version,
            "retained_configuration": change.baseline,
            "status": "Committed",
        }
        if recovery_release:
            complete_recovery_change(db, recovery_release, change, group, user.id)
        db.add(
            AuditEvent(
                actor_id=user.id,
                event_type="configuration.executed",
                subject_id=change.id,
                details={"group_id": group.id, "version": group.version},
            )
        )
        return view(change)
