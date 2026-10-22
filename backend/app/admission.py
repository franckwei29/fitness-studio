"""Confirmed admission, exact approval, and versioned member records."""

import hashlib
import json
from dataclasses import asdict
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.database import transaction
from app.models import (
    Approval,
    Assessment,
    AuditEvent,
    CurrentPlans,
    Member,
    MemberGroup,
    ProfileSnapshot,
    User,
    now,
)
from app.rules import RULE_VERSION, assess
from app.security import owner, principal

router = APIRouter(prefix="/api")


def fingerprint(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def view(row):
    return {c.name: getattr(row, c.name) for c in row.__table__.columns}


def owned_assessment(db, identifier, user):
    row = db.scalar(select(Assessment).where(Assessment.id == identifier).with_for_update())
    if not row or row.user_id != user.id:
        raise HTTPException(404, "Assessment not found.")
    return row


def approval_basis(row):
    return fingerprint(
        {
            "assessment_id": row.id,
            "version": row.version,
            "facts": row.confirmed_facts,
            "rule_version": RULE_VERSION,
        }
    )


def complete_admission(db, row, actor_id):
    result = assess(row.confirmed_facts)
    if result.outcome not in {"Eligible", "PendingOwnerReview"}:
        raise HTTPException(409, "Current admission requirements are not satisfied.")
    if result.outcome == "PendingOwnerReview":
        approval = db.scalar(
            select(Approval).where(
                Approval.subject_id == row.id,
                Approval.fingerprint == approval_basis(row),
                Approval.decision == "Approved",
                Approval.expires_at > now(),
            )
        )
        if not approval:
            raise HTTPException(409, "A current approval is required.")
    member = db.scalar(select(Member).where(Member.user_id == row.user_id).with_for_update())
    if member is None:
        group = db.scalar(select(MemberGroup).where(MemberGroup.name == "Default"))
        if not group:
            raise HTTPException(503, "Service configuration is not ready.")
        member = Member(user_id=row.user_id, group_id=group.id, profile_version=1)
        db.add(member)
        db.flush()
        db.add(CurrentPlans(member_id=member.id))
    else:
        member.profile_version += 1
    member.status = "Accepted"
    db.add(
        ProfileSnapshot(
            member_id=member.id,
            version=member.profile_version,
            facts=row.confirmed_facts,
            sources={"assessment_id": row.id},
            confirmed_by=row.user_id,
        )
    )
    row.member_id = member.id
    row.status = "Accepted"
    db.add(
        AuditEvent(
            actor_id=actor_id,
            event_type="admission.accepted",
            subject_id=row.id,
            details={
                "member_id": member.id,
                "profile_version": member.profile_version,
                "rule_version": RULE_VERSION,
            },
        )
    )
    return member


class Confirmation(BaseModel):
    expected_version: int = Field(ge=1)
    facts: dict


@router.post("/assessments", status_code=201)
def create_assessment(user: User = Depends(principal)):
    with transaction() as db:
        db.scalar(select(User).where(User.id == user.id).with_for_update())
        pending = db.scalar(
            select(Assessment).where(
                Assessment.user_id == user.id,
                Assessment.status.in_(["Collecting", "NeedsInformation", "PendingOwnerReview"]),
            )
        )
        if pending:
            return view(pending)
        member = db.scalar(select(Member).where(Member.user_id == user.id))
        row = Assessment(user_id=user.id, member_id=member.id if member else None)
        db.add(row)
        db.flush()
        return view(row)


@router.get("/assessments/{identifier}")
def get_assessment(identifier: str, user: User = Depends(principal)):
    with transaction() as db:
        return view(owned_assessment(db, identifier, user))


@router.post("/assessments/{identifier}/confirm")
def confirm(identifier: str, body: Confirmation, user: User = Depends(principal)):
    with transaction() as db:
        db.scalar(select(User).where(User.id == user.id).with_for_update())
        row = owned_assessment(db, identifier, user)
        if row.status == "Accepted" and row.confirmed_facts == body.facts:
            return view(row)
        if row.status not in {"Collecting", "NeedsInformation", "PendingOwnerReview"}:
            raise HTTPException(409, "This assessment is closed. Start a new assessment.")
        if row.version != body.expected_version:
            raise HTTPException(409, "Assessment changed. Review the current version.")
        row.confirmed_facts = body.facts
        row.version += 1
        result = assess(body.facts)
        row.status = result.outcome
        row.decision = asdict(result)
        for previous in db.scalars(
            select(Approval).where(Approval.subject_id == row.id, Approval.decision == "Pending")
        ):
            previous.decision = "Superseded"
        if row.member_id:
            member = db.get(Member, row.member_id)
            member.status = "Suspended"
        if result.outcome == "Eligible":
            complete_admission(db, row, user.id)
        elif result.outcome == "PendingOwnerReview":
            db.add(
                Approval(
                    subject_kind="IntakeAssessment",
                    subject_id=row.id,
                    subject_version=row.version,
                    fingerprint=approval_basis(row),
                    scope={"user_id": user.id},
                    context={"facts": body.facts, "decision": asdict(result)},
                    expires_at=now() + timedelta(hours=24),
                )
            )
        db.add(
            AuditEvent(
                actor_id=user.id,
                event_type="assessment.confirmed",
                subject_id=row.id,
                details={"version": row.version, "outcome": row.status},
            )
        )
        return view(row)


@router.get("/me/profile")
def profile(user: User = Depends(principal)):
    with transaction() as db:
        member = db.scalar(select(Member).where(Member.user_id == user.id))
        if not member:
            return {"member": None, "snapshot": None}
        snapshot = db.scalar(
            select(ProfileSnapshot).where(
                ProfileSnapshot.member_id == member.id, ProfileSnapshot.version == member.profile_version
            )
        )
        return {"member": view(member), "snapshot": view(snapshot)}


@router.get("/me/profile/history")
def profile_history(user: User = Depends(principal)):
    with transaction() as db:
        member = db.scalar(select(Member).where(Member.user_id == user.id))
        if not member:
            return {"current_version": None, "versions": []}
        rows = db.scalars(
            select(ProfileSnapshot)
            .where(ProfileSnapshot.member_id == member.id)
            .order_by(ProfileSnapshot.version)
        )
        previous = {}
        versions = []
        for row in rows:
            changes = [
                {
                    "field": key,
                    "before_present": key in previous,
                    "after_present": key in row.facts,
                    "before": previous.get(key),
                    "after": row.facts.get(key),
                }
                for key in sorted(set(previous) | set(row.facts))
                if (key in previous) != (key in row.facts) or previous.get(key) != row.facts.get(key)
            ]
            versions.append(
                {
                    "version": row.version,
                    "created_at": row.created_at,
                    "facts": row.facts,
                    "changes": changes,
                    "source_assessment_id": row.sources.get("assessment_id"),
                }
            )
            previous = row.facts
        return {"current_version": member.profile_version, "versions": list(reversed(versions))}


@router.get("/owner/approvals")
def approvals(user: User = Depends(owner)):
    with transaction() as db:
        rows = list(db.scalars(select(Approval).order_by(Approval.created_at.desc())))
        for row in rows:
            if row.decision == "Pending" and row.expires_at <= now():
                row.decision = "Expired"
        return [view(row) for row in rows]


class Decision(BaseModel):
    decision: str
    fingerprint: str
    comment: str = ""


@router.post("/owner/approvals/{identifier}/decisions")
def decide(identifier: str, body: Decision, user: User = Depends(owner)):
    if body.decision not in {"Approved", "Revise", "Rejected"}:
        raise HTTPException(422, "Choose Approved, Revise, or Rejected.")
    with transaction() as db:
        row = db.scalar(select(Approval).where(Approval.id == identifier).with_for_update())
        if not row:
            raise HTTPException(404, "Approval not found.")
        if row.fingerprint != body.fingerprint or row.expires_at <= now():
            raise HTTPException(409, "This approval is stale or expired.")
        if row.decision != "Pending":
            if row.decision == body.decision:
                return view(row)
            raise HTTPException(409, "A decision has already been recorded.")
        if row.subject_kind in {"TrainingPlan", "NutritionPlan"}:
            from app.activation import plan_fingerprint
            from app.models import MemberGroup, PlanVersion

            plan = db.get(PlanVersion, row.subject_id)
            kind = "Training" if row.subject_kind == "TrainingPlan" else "Nutrition"
            if not plan or plan.kind != kind or plan.status != "Validated":
                raise HTTPException(409, f"{kind} candidate is unavailable.")
            member = db.get(Member, plan.member_id)
            group = db.get(MemberGroup, member.group_id)
            if (
                plan_fingerprint(plan) != row.fingerprint
                or member.status != "Accepted"
                or plan.profile_version != member.profile_version
                or plan.config_version != group.version
            ):
                raise HTTPException(409, f"{kind} candidate context has changed.")
            from app.plan_review import apply_plan_decision

            apply_plan_decision(db, row, [plan], body.decision, body.comment)
            row.decision, row.decided_by, row.decided_at, row.comment = (
                body.decision,
                user.id,
                now().replace(microsecond=0),
                body.comment,
            )
            if body.decision in {"Revise", "Rejected"}:
                plan.status = body.decision
            db.add(
                AuditEvent(
                    actor_id=user.id,
                    event_type=f"{kind.lower()}.reviewed",
                    subject_id=plan.id,
                    details={"decision": body.decision, "fingerprint": row.fingerprint},
                )
            )
            return view(row)
        if row.subject_kind == "PlanPair":
            from app.activation import pair_fingerprint
            from app.models import MemberGroup, PlanPair, PlanVersion

            pair = db.get(PlanPair, row.subject_id)
            if not pair or pair.status != "Candidate":
                raise HTTPException(409, "The candidate pair is no longer available.")
            training = db.get(PlanVersion, pair.training_id)
            nutrition = db.get(PlanVersion, pair.nutrition_id)
            member = db.get(Member, pair.member_id)
            group = db.get(MemberGroup, member.group_id)
            if (
                pair_fingerprint(training, nutrition) != row.fingerprint
                or member.status != "Accepted"
                or any(
                    plan.profile_version != member.profile_version or plan.config_version != group.version
                    for plan in (training, nutrition)
                )
            ):
                raise HTTPException(409, "The plan pair or its context has changed.")
            from app.plan_review import apply_plan_decision

            apply_plan_decision(db, row, [training, nutrition], body.decision, body.comment)
            row.decision, row.decided_by, row.decided_at, row.comment = (
                body.decision,
                user.id,
                now().replace(microsecond=0),
                body.comment,
            )
            if body.decision in {"Revise", "Rejected"}:
                pair.status = body.decision
            db.add(
                AuditEvent(
                    actor_id=user.id,
                    event_type="plan_pair.reviewed",
                    subject_id=pair.id,
                    details={"decision": body.decision, "fingerprint": row.fingerprint},
                )
            )
            return view(row)
        if row.subject_kind != "IntakeAssessment":
            raise HTTPException(409, "This approval requires its service-specific handler.")
        assessment = db.scalar(select(Assessment).where(Assessment.id == row.subject_id).with_for_update())
        if not assessment or approval_basis(assessment) != row.fingerprint:
            raise HTTPException(409, "The reviewed information has changed.")
        row.decision, row.decided_by, row.decided_at, row.comment = (
            body.decision,
            user.id,
            now().replace(microsecond=0),
            body.comment,
        )
        db.flush()
        if body.decision == "Approved":
            complete_admission(db, assessment, user.id)
        elif body.decision == "Revise":
            assessment.status = "Collecting"
        else:
            assessment.status = "Rejected"
        db.add(
            AuditEvent(
                actor_id=user.id,
                event_type="approval.decided",
                subject_id=row.id,
                details={"decision": body.decision, "fingerprint": row.fingerprint},
            )
        )
        return view(row)
