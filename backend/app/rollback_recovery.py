"""Fresh approvals recover a blocked release without overwriting newer settings."""

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.admission import fingerprint, view
from app.agent_versions import verify_version
from app.database import transaction
from app.group_configuration import GroupConfiguration
from app.models import (
    AgentVersion,
    Approval,
    AuditEvent,
    ChangeRequest,
    MemberGroup,
    ModelCall,
    Request,
    User,
    WorkItem,
    now,
)
from app.policy import PolicyDenied
from app.pricing import UsageUncertain, price_for
from app.security import owner

router = APIRouter(prefix="/api/owner/releases")


def retained_version(db, release):
    if not release or release.kind != "AgentRelease" or release.status != "RollbackBlocked":
        raise HTTPException(409, "A blocked release rollback is required.")
    digest = fingerprint(
        {"group_id": release.group_id, "baseline": release.baseline, "candidate": release.candidate}
    )
    approved = db.scalar(
        select(Approval.id).where(
            Approval.subject_kind == "AgentRelease",
            Approval.subject_id == release.id,
            Approval.fingerprint == digest,
            Approval.decision == "Approved",
        )
    )
    if digest != release.fingerprint or not approved:
        raise HTTPException(409, "The original release approval no longer matches the retained version.")
    try:
        version = verify_version(
            db.get(AgentVersion, release.candidate["rollback_version_id"]), release.candidate["role"]
        )
    except (PolicyDenied, UsageUncertain) as exc:
        raise HTTPException(409, str(exc)) from None
    if version.fingerprint != release.candidate["rollback_fingerprint"]:
        raise HTTPException(409, "The retained version changed after release approval.")
    return version


def restored_configuration(group_configuration, release, version):
    configuration = GroupConfiguration.model_validate(group_configuration).model_dump()
    if configuration["agent_versions"].get(version.role) != release.candidate["agent_version_id"]:
        raise HTTPException(
            409, "The affected role already uses a different version. Review its current release."
        )
    configuration["agent_versions"][version.role] = version.id
    return configuration


class RecoveryProposal(BaseModel):
    expected_group_version: int = Field(ge=1)
    reason: str = Field(min_length=10, max_length=3000)


@router.post("/{identifier}/recovery-proposals", status_code=201)
def propose_recovery(identifier: str, body: RecoveryProposal, user: User = Depends(owner)):
    if len(body.reason.strip()) < 10:
        raise HTTPException(422, "Explain why the retained version should be restored.")
    with transaction() as db:
        release = db.scalar(select(ChangeRequest).where(ChangeRequest.id == identifier).with_for_update())
        version = retained_version(db, release)
        group = db.scalar(select(MemberGroup).where(MemberGroup.id == release.group_id).with_for_update())
        if not group or group.version != body.expected_group_version:
            raise HTTPException(409, "The group changed. Refresh before proposing recovery.")
        candidate = restored_configuration(group.configuration, release, version)
        baseline = {"version": group.version, "configuration": group.configuration}
        digest = fingerprint({"group_id": group.id, "baseline": baseline, "candidate": candidate})
        evidence = {
            "passed": True,
            "rollback_release_id": release.id,
            "release_fingerprint": release.fingerprint,
            "retained_fingerprint": version.fingerprint,
            "checks": ["OriginalReleaseApproval", "RetainedVersionIntegrity", "PreserveCurrentSettings"],
            "service_recovery_verified": False,
        }
        change = ChangeRequest(
            kind="GroupConfiguration",
            group_id=group.id,
            baseline=baseline,
            candidate=candidate,
            fingerprint=digest,
            status="PendingApproval",
            reason=body.reason.strip(),
            evaluation=evidence,
        )
        db.add(change)
        db.flush()
        db.add(
            Approval(
                subject_kind="GroupConfiguration",
                subject_id=change.id,
                subject_version=1,
                fingerprint=digest,
                scope={"group_id": group.id},
                context={"baseline": baseline, "candidate": candidate, "evaluation": evidence},
                expires_at=now() + timedelta(hours=24),
            )
        )
        db.add(
            AuditEvent(
                actor_id=user.id,
                event_type="release.recovery_proposed",
                subject_id=release.id,
                details={
                    "change_id": change.id,
                    "group_version": group.version,
                    "retained_version_id": version.id,
                },
            )
        )
        return view(change)


def verify_recovery_change(db, change, approval):
    """Validate the linked source before taking the group lock."""
    evidence = change.evaluation or {}
    approved_evidence = approval.context.get("evaluation") or {}
    if not evidence.get("rollback_release_id") and not approved_evidence.get("rollback_release_id"):
        return None
    if approved_evidence != evidence:
        raise HTTPException(409, "The recovery evidence changed after review.")
    release = db.scalar(
        select(ChangeRequest).where(ChangeRequest.id == evidence["rollback_release_id"]).with_for_update()
    )
    version = retained_version(db, release)
    if (
        release.group_id != change.group_id
        or release.fingerprint != evidence["release_fingerprint"]
        or version.fingerprint != evidence["retained_fingerprint"]
    ):
        raise HTTPException(409, "The source release or retained version changed.")
    if change.candidate != restored_configuration(change.baseline["configuration"], release, version):
        raise HTTPException(409, "Recovery may only restore the affected role's retained version.")
    return release


def complete_recovery_change(db, release, change, group, actor_id):
    db.flush()
    db.refresh(group)
    if group.configuration != change.candidate:
        raise HTTPException(409, "The restored configuration could not be verified.")
    release.status = "RolledBack"
    release.execution = (release.execution or {}) | {
        "service_health": "ConfigurationRestored",
        "previous_rollback_blocked_reason": (release.execution or {}).get("rollback_blocked_reason"),
        "rollback_blocked_reason": None,
        "rollback": {
            "group_version": group.version,
            "agent_version_id": release.candidate["rollback_version_id"],
            "verified": True,
            "reason": change.reason,
            "actor_id": actor_id,
            "at": now().isoformat(),
            "recovery_change_id": change.id,
        },
    }
    db.add(
        AuditEvent(
            actor_id=actor_id,
            event_type="release.recovery_configuration_restored",
            subject_id=release.id,
            details={
                "change_id": change.id,
                "group_version": group.version,
                "service_recovery_verified": False,
            },
        )
    )


def verify_release_service(db, incident, actor_id):
    """Restoring a pointer is insufficient; require a subsequent successful scoped run."""
    from datetime import datetime

    release = db.get(ChangeRequest, incident.evidence["release_id"])
    rollback = (release.execution or {}).get("rollback") if release else None
    group = db.get(MemberGroup, release.group_id) if release else None
    if (
        not release
        or release.status != "RolledBack"
        or not rollback
        or not rollback.get("verified")
        or not group
    ):
        raise HTTPException(409, "The retained configuration has not been restored and verified.")
    role = release.candidate["role"]
    version_id = rollback["agent_version_id"]
    if group.configuration.get("agent_versions", {}).get(role) != version_id:
        raise HTTPException(409, "The role no longer uses the restored version.")
    version = db.get(AgentVersion, version_id)
    try:
        verify_version(version, role)
        price = price_for(version.model)
    except (PolicyDenied, UsageUncertain) as exc:
        raise HTTPException(409, str(exc)) from None
    if version.fingerprint != release.candidate["rollback_fingerprint"]:
        raise HTTPException(409, "The restored version no longer matches its approved fingerprint.")
    tasks = db.scalars(
        select(WorkItem)
        .where(
            WorkItem.role == role,
            WorkItem.status == "Completed",
            WorkItem.created_at >= datetime.fromisoformat(rollback["at"]),
        )
        .order_by(WorkItem.created_at.desc())
    )
    for task in tasks:
        if any(
            task.input_versions.get(key) != value
            for key, value in {
                "group_id": group.id,
                "group": group.version,
                "agent_version": version_id,
            }.items()
        ):
            continue
        request = db.get(Request, task.request_id)
        if not request or request.status != "Completed":
            continue
        call = db.scalar(
            select(ModelCall).where(
                ModelCall.request_id == request.id,
                ModelCall.role == role,
                ModelCall.status == "Settled",
                ModelCall.model == version.model,
                ModelCall.price_version == price.version,
                ModelCall.actual_micros.is_not(None),
                ModelCall.request_fingerprint.is_not(None),
            )
        )
        if not call or call.output is None:
            continue
        incident.status, incident.outcome = "Closed", "Recovered"
        incident.recovery = {
            "verified_at": now().isoformat(),
            "release_id": release.id,
            "request_id": request.id,
            "task_ids": [task.id],
            "call_id": call.id,
            "group_version": group.version,
            "agent_version_id": version_id,
            "service_restored": True,
        }
        db.add(
            AuditEvent(
                actor_id=actor_id,
                request_id=request.id,
                event_type="incident.release_recovery_verified",
                subject_id=incident.id,
                details=incident.recovery,
            )
        )
        return view(incident)
    raise HTTPException(
        409,
        "The configuration is restored, but no successful subsequent request proves this role has recovered.",
    )
