"""Bounded revisions retain the original request, budgets, deadline, and failures."""

from types import SimpleNamespace

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from app.activation import plan_fingerprint
from app.database import transaction
from app.models import AuditEvent, PlanVersion, Request, User, ValidationResult, WorkItem, now
from app.policy import PolicyDenied, execution_context
from app.security import principal
from app.work_queue import event

MAX_CORRECTIONS = 2
router = APIRouter(prefix="/api/me/requests")


def correction_context(db, item, request, authorized):
    identifier = item.input_versions.get("correction_plan_id")
    if not identifier:
        return {}
    plan = db.get(PlanVersion, identifier)
    if (
        not plan
        or plan.request_id != request.id
        or plan.member_id != authorized["member_id"]
        or plan.status != "Invalid"
        or plan.kind != ("Training" if item.role == "Coach" else "Nutrition")
        or plan.fingerprint != plan_fingerprint(plan)
        or plan.fingerprint != item.input_versions.get("correction_fingerprint")
        or plan.profile_version != authorized["versions"]["profile"]
        or plan.config_version != authorized["versions"]["group"]
    ):
        raise PolicyDenied("The failed candidate or its input context changed before correction.")
    validation = db.scalar(
        select(ValidationResult).where(
            ValidationResult.subject_fingerprint == plan.fingerprint, ValidationResult.passed.is_(False)
        )
    )
    if not validation or not validation.violations:
        raise PolicyDenied("Recorded validation failures are required for correction.")
    return {
        "correction": {
            "plan_id": plan.id,
            "previous_candidate": plan.content,
            "violations": validation.violations,
            "instruction": "Correct every recorded violation. Use only exact IDs from the supplied catalogues, never invent or approximate identifiers. Preserve the confirmed requirements and return a complete corrected candidate for validation.",
        }
    }


def queue_correction(db, item, request, plan):
    """Caller holds the source task and request locks; the transaction publishes both."""
    item.lease_until = None
    item.wait_reason = "CandidateValidation"
    if (
        request.revisions >= MAX_CORRECTIONS
        or request.model_calls >= request.max_model_calls
        or request.deadline <= now()
    ):
        item.status = "Stopped"
        return {"status": "Stopped", "stop_reason": "CorrectionLimit", "revisions": request.revisions}
    request.revisions += 1
    item.status = "Superseded"
    corrected = WorkItem(
        request_id=request.id,
        role=item.role,
        subject_kind=item.subject_kind,
        subject_id=item.subject_id,
        input_versions=item.input_versions
        | {"correction_plan_id": plan.id, "correction_fingerprint": plan.fingerprint},
    )
    db.add(corrected)
    db.flush()
    details = {
        "status": "Queued",
        "correction_work_id": corrected.id,
        "correction_source_id": plan.id,
        "revisions": request.revisions,
        "maximum_revisions": MAX_CORRECTIONS,
    }
    db.add(
        AuditEvent(
            actor_id="system",
            request_id=request.id,
            event_type="candidate.correction_queued",
            subject_id=corrected.id,
            details=details | {"previous_work_id": item.id, "role": item.role},
        )
    )
    event(db, request, "correction_queued", **details)
    return details


class CorrectionRequest(BaseModel):
    plan_id: str


@router.post("/{identifier}/correct", status_code=202)
def correct_waiting_candidate(identifier: str, body: CorrectionRequest, user: User = Depends(principal)):
    with transaction() as db:
        request = db.get(Request, identifier)
        if not request or request.user_id != user.id:
            raise HTTPException(404, "Request not found.")
        if (request.result or {}).get("correction_source_id") == body.plan_id:
            return request.result
        item = db.scalar(
            select(WorkItem)
            .where(
                WorkItem.request_id == identifier,
                WorkItem.status == "Waiting",
                WorkItem.wait_reason == "CandidateValidation",
            )
            .with_for_update()
        )
        request = db.scalar(
            select(Request)
            .where(Request.id == identifier)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if (request.result or {}).get("correction_source_id") == body.plan_id:
            return request.result
        plan = db.get(PlanVersion, body.plan_id)
        if (
            not item
            or request.status != "Waiting"
            or not plan
            or plan.request_id != request.id
            or plan.status != "Invalid"
            or body.plan_id
            not in [(request.result or {}).get("training_id"), (request.result or {}).get("nutrition_id")]
        ):
            raise HTTPException(409, "Only the current failed candidate can be corrected.")
        try:
            authorized = execution_context(db, item, request)
            proposed = SimpleNamespace(
                role=item.role,
                input_versions=item.input_versions
                | {"correction_plan_id": plan.id, "correction_fingerprint": plan.fingerprint},
            )
            correction_context(db, proposed, request, authorized)
        except PolicyDenied as exc:
            raise HTTPException(409, str(exc)) from None
        result = queue_correction(db, item, request, plan)
        request.status = result["status"]
        request.result = (request.result or {}) | result
        return request.result
