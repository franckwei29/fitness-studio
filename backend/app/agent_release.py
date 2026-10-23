"""Evidence-bound canary release, exact approval, and retained-configuration rollback."""

from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.admission import fingerprint, view
from app.agent_versions import verify_version
from app.database import transaction
from app.evaluation_review import verified_evidence
from app.group_configuration import GroupConfiguration
from app.models import (
    AgentVersion,
    Approval,
    AuditEvent,
    ChangeRequest,
    EvaluationRun,
    MemberGroup,
    User,
    now,
)
from app.policy import PolicyDenied
from app.pricing import UsageUncertain
from app.security import owner
from app.settings import get_settings
from app.version_admin import base_instruction

router = APIRouter(prefix="/api/owner/releases")


def release_evidence(db, run):
    if not run or run.status != "Reviewed" or not (run.summary or {}).get("release_eligible"):
        raise PolicyDenied("A completed and reviewed live comparison is required.")
    digest, _ = verified_evidence(db, run)
    review = run.summary.get("quality_review", {})
    if not review.get("passed") or review.get("evidence_fingerprint") != digest:
        raise PolicyDenied("The reviewed evidence changed. A new review is required.")
    return digest


def check_proposal(db, change):
    candidate = change.candidate
    digest = fingerprint({"group_id": change.group_id, "baseline": change.baseline, "candidate": candidate})
    if digest != change.fingerprint:
        raise PolicyDenied("The release proposal no longer matches its approval fingerprint.")
    run = db.get(EvaluationRun, candidate["evaluation_id"])
    if release_evidence(db, run) != candidate["evidence_fingerprint"]:
        raise PolicyDenied("Release evaluation evidence changed.")
    if (
        run.candidate_id != candidate["agent_version_id"]
        or run.baseline_id != candidate["rollback_version_id"]
    ):
        raise PolicyDenied("Release versions do not match the evaluated comparison.")
    if candidate.get("source_release_id"):
        verify_canary_source(db, candidate["source_release_id"], run.id)
    return run


def verify_canary_source(db, identifier, evaluation_id):
    from app.canary_monitor import observation

    source = db.get(ChangeRequest, identifier)
    if (
        not source
        or source.kind != "AgentRelease"
        or source.status != "Observed"
        or source.candidate.get("source_release_id")
        or source.candidate.get("evaluation_id") != evaluation_id
    ):
        raise PolicyDenied(
            "Expansion requires the original successfully observed canary for this evaluation."
        )
    check_proposal(db, source)
    group = db.get(MemberGroup, source.group_id)
    if (
        not group
        or group.name != "Canary"
        or group.version != source.execution["group_version"]
        or group.configuration != source.candidate["configuration"]
    ):
        raise PolicyDenied("The observed canary configuration is no longer current.")
    evidence = observation(db, source, now())
    if (
        not evidence["window_elapsed"]
        or not evidence["minimum_runs_met"]
        or evidence["hard_rule_failures"]
        or evidence["execution_failures"]
    ):
        raise PolicyDenied("The current canary evidence does not support expansion.")
    return source


class ReleaseProposal(BaseModel):
    group_id: str
    expected_group_version: int = Field(ge=1)
    evaluation_id: str
    reason: str = Field(min_length=10, max_length=3000)
    observation_minutes: int = Field(default=30, ge=1, le=10080)
    minimum_runs: int = Field(default=3, ge=1, le=1000)
    error_threshold: int = Field(default=1, ge=1, le=100)
    source_release_id: str | None = None


@router.get("")
def releases(user: User = Depends(owner)):
    with transaction() as db:
        return [
            view(row)
            for row in db.scalars(
                select(ChangeRequest)
                .where(ChangeRequest.kind == "AgentRelease")
                .order_by(ChangeRequest.created_at.desc())
            )
        ]


@router.post("", status_code=201)
def propose_release(body: ReleaseProposal, user: User = Depends(owner)):
    with transaction() as db:
        group = db.scalar(select(MemberGroup).where(MemberGroup.id == body.group_id).with_for_update())
        if not group:
            raise HTTPException(404, "Group not found.")
        if not body.source_release_id and group.name != "Canary":
            raise HTTPException(422, "Initial release must target the Canary group.")
        if body.source_release_id and group.name == "Canary":
            raise HTTPException(422, "An expanded release must target another member group.")
        if group.version != body.expected_group_version:
            raise HTTPException(409, "The target group changed. Refresh the proposal.")
        run = db.get(EvaluationRun, body.evaluation_id)
        try:
            evidence = release_evidence(db, run)
            baseline_version = verify_version(db.get(AgentVersion, run.baseline_id), run.role)
            if body.source_release_id:
                verify_canary_source(db, body.source_release_id, run.id)
        except (PolicyDenied, UsageUncertain) as exc:
            raise HTTPException(409, str(exc)) from None
        configured = group.configuration.get("agent_versions", {}).get(run.role)
        if configured and configured != run.baseline_id:
            raise HTTPException(409, "The evaluated baseline is not the group current version.")
        if not configured and (
            baseline_version.prompt != base_instruction(run.role)
            or baseline_version.model != get_settings().gemini_model
        ):
            raise HTTPException(
                409, "The comparison must use the current default runtime version as baseline."
            )
        baseline = {"version": group.version, "configuration": group.configuration}
        configuration = GroupConfiguration.model_validate(group.configuration).model_dump()
        configuration["agent_versions"] = configuration["agent_versions"] | {run.role: run.candidate_id}
        candidate = {
            "configuration": configuration,
            "role": run.role,
            "agent_version_id": run.candidate_id,
            "rollback_version_id": run.baseline_id,
            "rollback_fingerprint": baseline_version.fingerprint,
            "evaluation_id": run.id,
            "evidence_fingerprint": evidence,
            "observation_minutes": body.observation_minutes,
            "minimum_runs": body.minimum_runs,
            "error_threshold": body.error_threshold,
            "rollback_on_hard_rule_failure": True,
            "source_release_id": body.source_release_id,
        }
        digest = fingerprint({"group_id": group.id, "baseline": baseline, "candidate": candidate})
        change = ChangeRequest(
            kind="AgentRelease",
            group_id=group.id,
            baseline=baseline,
            candidate=candidate,
            fingerprint=digest,
            status="PendingApproval",
            reason=body.reason,
            evaluation={"run_id": run.id, "evidence_fingerprint": evidence},
        )
        db.add(change)
        db.flush()
        db.add(
            Approval(
                subject_kind="AgentRelease",
                subject_id=change.id,
                subject_version=1,
                fingerprint=digest,
                scope={"group_id": group.id},
                context={"baseline": baseline, "candidate": candidate, "evaluation": run.summary},
                expires_at=now() + timedelta(hours=24),
            )
        )
        db.add(
            AuditEvent(
                actor_id=user.id,
                event_type="release.proposed",
                subject_id=change.id,
                details={"group_id": group.id, "fingerprint": digest},
            )
        )
        return view(change)


class ReleaseDecision(BaseModel):
    decision: Literal["Approved", "Revise", "Rejected"]
    fingerprint: str
    comment: str = Field(default="", max_length=3000)


@router.post("/{identifier}/review")
def review_release(identifier: str, body: ReleaseDecision, user: User = Depends(owner)):
    with transaction() as db:
        change = db.scalar(select(ChangeRequest).where(ChangeRequest.id == identifier).with_for_update())
        if not change or change.kind != "AgentRelease":
            raise HTTPException(404, "Release not found.")
        approval = db.scalar(
            select(Approval)
            .where(Approval.subject_id == identifier, Approval.subject_kind == "AgentRelease")
            .with_for_update()
        )
        if not approval or approval.fingerprint != body.fingerprint or approval.expires_at <= now():
            raise HTTPException(409, "Release approval is stale or expired.")
        if approval.decision != "Pending":
            if approval.decision == body.decision:
                return view(change)
            raise HTTPException(409, "This release already has a decision.")
        try:
            check_proposal(db, change)
        except (PolicyDenied, UsageUncertain) as exc:
            raise HTTPException(409, str(exc)) from None
        approval.decision, approval.decided_by, approval.decided_at = body.decision, user.id, now()
        approval.comment = body.comment
        change.status = body.decision
        db.add(
            AuditEvent(
                actor_id=user.id,
                event_type="release.reviewed",
                subject_id=change.id,
                details={"decision": body.decision, "fingerprint": change.fingerprint},
            )
        )
        return view(change)


@router.post("/{identifier}/execute")
def execute_release(identifier: str, user: User = Depends(owner)):
    with transaction() as db:
        change = db.scalar(select(ChangeRequest).where(ChangeRequest.id == identifier).with_for_update())
        if not change or change.kind != "AgentRelease":
            raise HTTPException(404, "Release not found.")
        if change.status in {"Canary", "Expanded"}:
            return view(change)
        approval = db.scalar(
            select(Approval).where(
                Approval.subject_id == change.id,
                Approval.subject_kind == "AgentRelease",
                Approval.fingerprint == change.fingerprint,
                Approval.decision == "Approved",
                Approval.expires_at > now(),
            )
        )
        if change.status != "Approved" or not approval:
            raise HTTPException(409, "A current release approval is required.")
        try:
            check_proposal(db, change)
        except (PolicyDenied, UsageUncertain) as exc:
            raise HTTPException(409, str(exc)) from None
        group = db.scalar(select(MemberGroup).where(MemberGroup.id == change.group_id).with_for_update())
        if (
            group.version != change.baseline["version"]
            or group.configuration != change.baseline["configuration"]
        ):
            raise HTTPException(409, "The approved group baseline changed.")
        group.configuration = GroupConfiguration.model_validate(
            change.candidate["configuration"]
        ).model_dump()
        group.version += 1
        change.status = "Expanded" if change.candidate.get("source_release_id") else "Canary"
        change.execution = {
            "group_version": group.version,
            "started_at": now().isoformat(),
            "retained_configuration": change.baseline,
            "status": "Committed",
        }
        db.add(
            AuditEvent(
                actor_id=user.id,
                event_type="release.expansion_started"
                if change.candidate.get("source_release_id")
                else "release.canary_started",
                subject_id=change.id,
                details={"group_id": group.id, "group_version": group.version},
            )
        )
        return view(change)


def rollback_configuration(db, change, actor_id, reason):
    """Caller holds the change lock; configuration and outcome commit together."""
    if change.status == "RolledBack":
        return
    if change.status not in {"Canary", "Expanded", "Observed"} or not change.execution:
        raise PolicyDenied("Only an active canary release can be rolled back.")
    digest = fingerprint(
        {"group_id": change.group_id, "baseline": change.baseline, "candidate": change.candidate}
    )
    approval = db.scalar(
        select(Approval).where(
            Approval.subject_id == change.id,
            Approval.subject_kind == "AgentRelease",
            Approval.fingerprint == digest,
            Approval.decision == "Approved",
        )
    )
    if digest != change.fingerprint or not approval:
        raise PolicyDenied("The retained rollback configuration is not covered by the release approval.")
    retained = verify_version(
        db.get(AgentVersion, change.candidate["rollback_version_id"]), change.candidate["role"]
    )
    if retained.fingerprint != change.candidate.get("rollback_fingerprint"):
        raise PolicyDenied("The retained agent version changed after release approval.")
    group = db.scalar(select(MemberGroup).where(MemberGroup.id == change.group_id).with_for_update())
    if (
        not group
        or group.version != change.execution["group_version"]
        or group.configuration != change.candidate["configuration"]
    ):
        raise PolicyDenied(
            "The group changed after this release. Review the newer configuration before rollback."
        )
    configuration = GroupConfiguration.model_validate(change.baseline["configuration"]).model_dump()
    configuration["agent_versions"] = configuration["agent_versions"] | {retained.role: retained.id}
    group.configuration = configuration
    group.version += 1
    db.flush()
    db.refresh(group)
    if group.configuration != configuration:
        raise PolicyDenied("The restored configuration could not be verified.")
    change.status = "RolledBack"
    change.execution = change.execution | {
        "rollback": {
            "group_version": group.version,
            "agent_version_id": retained.id,
            "verified": True,
            "reason": reason,
            "actor_id": actor_id,
            "at": now().isoformat(),
        }
    }
    db.add(
        AuditEvent(
            actor_id=actor_id,
            event_type="release.rolled_back",
            subject_id=change.id,
            details={
                "group_id": group.id,
                "group_version": group.version,
                "agent_version_id": retained.id,
                "reason": reason,
            },
        )
    )


class RollbackRequest(BaseModel):
    reason: str = Field(min_length=10, max_length=2000)


@router.post("/{identifier}/rollback")
def rollback_release(identifier: str, body: RollbackRequest, user: User = Depends(owner)):
    with transaction() as db:
        change = db.scalar(select(ChangeRequest).where(ChangeRequest.id == identifier).with_for_update())
        if not change or change.kind != "AgentRelease":
            raise HTTPException(404, "Release not found.")
        try:
            rollback_configuration(db, change, user.id, body.reason)
        except (PolicyDenied, UsageUncertain) as exc:
            raise HTTPException(409, str(exc)) from None
        return view(change)
