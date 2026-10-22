"""Allowlisted operational evidence; never export free-text inputs or outputs."""

from uuid import UUID

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select

from app.admission import fingerprint
from app.database import transaction
from app.models import AuditEvent, ModelCall, Request, RequestEvent, WorkItem
from app.pricing import FLASH_LITE

ROLES = {"Intake", "Coach", "Nutrition", "TechnicalManager"}
STATUSES = {
    "Queued",
    "Running",
    "Waiting",
    "AwaitingConfirmation",
    "Completed",
    "Stopped",
    "Cancelled",
    "Superseded",
    "Reserved",
    "Dispatching",
    "UsageUnknown",
    "Settled",
    "Approved",
    "Revise",
    "Rejected",
}
REASONS = {
    "BudgetRequired",
    "ModelDispatchUnavailable",
    "PolicyReview",
    "UsageReconciliation",
    "TechnicalReview",
    "ExecutorUnavailable",
    "CandidateValidation",
    "DeadlineExpired",
    "DeadlineOrAttemptLimit",
    "AccountUnavailable",
    "UserCancelled",
    "RequestEnded",
    "CorrectionLimit",
}
KINDS = {
    "Chat",
    "Intake",
    "Training",
    "Nutrition",
    "Combined",
    "Operations",
    "CycleReview",
    "FoodSubstitution",
    "Evaluation",
}
EVENTS = {
    "queued",
    "processing",
    "waiting",
    "stopped",
    "recovery",
    "recovery_queued",
    "training_candidate",
    "nutrition_candidate",
    "completed",
    "evaluation_case_completed",
    "policy_changed",
    "correction_queued",
    "expired",
    "cancelled",
    "model.response_failed",
    "task.execution_failed",
    "request.cancelled",
    "candidate.correction_queued",
    "plans.activated",
    "training.activated",
    "incident.recovery_requested",
    "incident.recovery_verified",
    "incident.closed_unresolved",
    "member.group_changed",
}
NUMERIC = {
    "profile",
    "group",
    "version",
    "group_version",
    "revision",
    "revisions",
    "maximum_revisions",
    "model_calls",
    "http_status",
    "attempts",
    "current_version",
}
REFERENCES = {
    "group_id",
    "agent_version",
    "work_id",
    "call_id",
    "plan_id",
    "training_id",
    "nutrition_id",
    "pair_id",
    "incident_id",
    "release_id",
    "change_id",
    "correction_plan_id",
    "source_plan_id",
}
HASHES = {"fingerprint", "correction_fingerprint", "source_fingerprint"}
TOKENS = {
    "total_input_tokens",
    "total_output_tokens",
    "total_thought_tokens",
    "total_cached_tokens",
    "total_tool_use_tokens",
    "total_tokens",
}


def code(value, allowed):
    return value if isinstance(value, str) and value in allowed else None


def safe_fields(values):
    result = {}
    for key, value in (values if isinstance(values, dict) else {}).items():
        if key in NUMERIC and type(value) is int and value >= 0:
            result[key] = value
        elif key in REFERENCES and isinstance(value, str):
            try:
                result[key] = str(UUID(value))
            except ValueError:
                pass
        elif (
            key in HASHES
            and isinstance(value, str)
            and len(value) == 64
            and all(c in "0123456789abcdef" for c in value)
        ):
            result[key] = value
        elif key in {"status", "decision", "role", "reason"}:
            accepted = code(value, ROLES if key == "role" else REASONS if key == "reason" else STATUSES)
            if accepted is not None:
                result[key] = accepted
    return result


def request_trace(identifier):
    with transaction() as db:
        request = db.get(Request, identifier)
        if not request:
            raise HTTPException(404, "Request not found.")
        tasks = list(
            db.scalars(
                select(WorkItem)
                .where(WorkItem.request_id == identifier)
                .order_by(WorkItem.created_at, WorkItem.id)
            )
        )
        calls = list(
            db.scalars(
                select(ModelCall)
                .where(ModelCall.request_id == identifier)
                .order_by(ModelCall.created_at, ModelCall.id)
            )
        )
        audits = list(
            db.scalars(
                select(AuditEvent)
                .where(AuditEvent.request_id == identifier)
                .order_by(AuditEvent.created_at, AuditEvent.id)
            )
        )
        progress = list(
            db.scalars(
                select(RequestEvent)
                .where(RequestEvent.request_id == identifier)
                .order_by(RequestEvent.sequence)
            )
        )
        data = jsonable_encoder(
            {
                "schema_version": "operational-trace-v1",
                "request": {
                    "id": request.id,
                    "kind": code(request.kind, KINDS),
                    "status": code(request.status, STATUSES),
                    "created_at": request.created_at,
                    "deadline": request.deadline,
                    "model_calls": request.model_calls,
                },
                "tasks": [
                    {
                        "id": row.id,
                        "role": code(row.role, ROLES),
                        "status": code(row.status, STATUSES),
                        "wait_reason": code(row.wait_reason, REASONS),
                        "attempts": row.attempts,
                        "lease_until": row.lease_until,
                        "input_versions": safe_fields(row.input_versions),
                    }
                    for row in tasks
                ],
                "calls": [
                    {
                        "id": row.id,
                        "model": row.model if row.model == FLASH_LITE.model else "Unrecognized",
                        "role": code(row.role, ROLES),
                        "status": code(row.status, STATUSES),
                        "reserved_micros": row.reserved_micros,
                        "actual_micros": row.actual_micros,
                        "price_version": row.price_version
                        if row.price_version == FLASH_LITE.version
                        else "Unverified",
                        "usage": {
                            k: v for k, v in row.usage.items() if k in TOKENS and type(v) is int and v >= 0
                        }
                        if isinstance(row.usage, dict)
                        else None,
                        "created_at": row.created_at,
                        "completed_at": row.completed_at,
                    }
                    for row in calls
                ],
                "events": [
                    {
                        "id": row.id,
                        "event_type": code(row.event_type, EVENTS) or "redacted",
                        "created_at": row.created_at,
                        "details": safe_fields(row.details),
                    }
                    for row in audits
                ],
                "progress": [
                    {
                        "sequence": row.sequence,
                        "event_type": code(row.event_type, EVENTS) or "redacted",
                        "created_at": row.created_at,
                        "details": safe_fields(row.payload),
                    }
                    for row in progress
                ],
            }
        )
    return data | {"fingerprint": fingerprint(data)}
