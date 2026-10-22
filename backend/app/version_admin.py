"""Immutable agent candidates; saving a candidate never changes a live group."""

from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select

from app.admission import fingerprint, view
from app.database import transaction
from app.evaluation_records import prepare_evaluation, verify_evaluation_snapshot
from app.evaluation_review import verified_evidence
from app.evaluation_suite import SUITE_VERSION, evaluation_cases
from app.models import (
    AgentVersion,
    AuditEvent,
    EvaluationResult,
    EvaluationRun,
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
from app.settings import get_settings

router = APIRouter(prefix="/api/owner/agent-versions")


class Candidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["Intake", "Coach", "Nutrition", "TechnicalManager"]
    label: str = Field(min_length=1, max_length=80)
    model: str = Field(min_length=1, max_length=100)
    instructions: str = Field(min_length=1, max_length=12000)

    @field_validator("label", "model", "instructions")
    @classmethod
    def nonblank(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("Provide a nonblank value.")
        return value


def base_instruction(role):
    from app.agent_contracts import INTAKE_INSTRUCTION, TECHNICAL_INSTRUCTION
    from app.coach_runtime import COACH_INSTRUCTION
    from app.nutrition_runtime import INSTRUCTION

    return {
        "Intake": INTAKE_INSTRUCTION,
        "Coach": COACH_INSTRUCTION,
        "Nutrition": INSTRUCTION,
        "TechnicalManager": TECHNICAL_INSTRUCTION,
    }[role]


@router.get("")
def versions(user: User = Depends(owner)):
    with transaction() as db:
        groups = list(db.scalars(select(MemberGroup)))
        rows = list(db.scalars(select(AgentVersion).order_by(AgentVersion.created_at.desc()).limit(200)))
        return {
            "default_model": get_settings().gemini_model,
            "versions": [
                view(row)
                | {
                    "configured_groups": [
                        {"id": group.id, "name": group.name}
                        for group in groups
                        if group.configuration.get("agent_versions", {}).get(row.role) == row.id
                    ]
                }
                for row in rows
            ],
        }


@router.post("", status_code=201)
def create_candidate(body: Candidate, user: User = Depends(owner)):
    try:
        price_for(body.model)
    except UsageUncertain:
        raise HTTPException(
            422, "This model has no supported pricing and accounting configuration."
        ) from None
    prompt = base_instruction(body.role) + "\n\nCandidate-specific instructions:\n" + body.instructions
    digest = fingerprint({"role": body.role, "model": body.model, "prompt": prompt})
    with transaction() as db:
        # Serialize candidate creation across owner sessions without a new lock service.
        db.scalar(select(User).where(User.id == user.id).with_for_update())
        existing = db.scalar(select(AgentVersion).where(AgentVersion.fingerprint == digest))
        if existing:
            return view(existing)
        row = AgentVersion(
            role=body.role, label=body.label, model=body.model, prompt=prompt, fingerprint=digest
        )
        db.add(row)
        db.flush()
        db.add(
            AuditEvent(
                actor_id=user.id,
                event_type="agent_version.candidate_saved",
                subject_id=row.id,
                details={"role": row.role, "model": row.model, "fingerprint": digest},
            )
        )
        return view(row)


class EvaluationPreparation(BaseModel):
    baseline_id: str
    evidence_mode: Literal["Live", "Fixture"] = "Live"


@router.post("/{identifier}/evaluations", status_code=201)
def prepare_comparison(identifier: str, body: EvaluationPreparation, user: User = Depends(owner)):
    with transaction() as db:
        candidate = db.get(AgentVersion, identifier)
        if not candidate:
            raise HTTPException(404, "Candidate version not found.")
        try:
            run = prepare_evaluation(
                db,
                user.id,
                body.baseline_id,
                identifier,
                SUITE_VERSION,
                evaluation_cases(candidate.role),
                body.evidence_mode,
            )
        except (ValueError, PolicyDenied, UsageUncertain) as exc:
            raise HTTPException(422, str(exc)) from None
        db.add(
            AuditEvent(
                actor_id=user.id,
                event_type="evaluation.prepared",
                subject_id=run.id,
                details={
                    "baseline_id": run.baseline_id,
                    "candidate_id": run.candidate_id,
                    "suite_fingerprint": run.suite_fingerprint,
                    "evidence_mode": run.evidence_mode,
                },
            )
        )
        return view(run) | {"planned_calls": len(run.cases) * 2 if run.evidence_mode == "Live" else 0}


@router.get("/{identifier}/evaluations")
def comparisons(identifier: str, user: User = Depends(owner)):
    with transaction() as db:
        if not db.get(AgentVersion, identifier):
            raise HTTPException(404, "Agent version not found.")
        return [
            view(run)
            for run in db.scalars(
                select(EvaluationRun)
                .where(EvaluationRun.candidate_id == identifier)
                .order_by(EvaluationRun.created_at.desc())
                .limit(100)
            )
        ]


@router.get("/{identifier}/evaluations/{run_id}")
def comparison_detail(identifier: str, run_id: str, user: User = Depends(owner)):
    with transaction() as db:
        run = db.get(EvaluationRun, run_id)
        if not run or run.candidate_id != identifier:
            raise HTTPException(404, "Evaluation not found.")
        results = list(db.scalars(select(EvaluationResult).where(EvaluationResult.run_id == run.id)))
        request_ids = (run.summary or {}).get("request_ids", [])
        tasks = (
            list(db.scalars(select(WorkItem).where(WorkItem.request_id.in_(request_ids))))
            if request_ids
            else []
        )
        calls = (
            list(db.scalars(select(ModelCall).where(ModelCall.request_id.in_(request_ids))))
            if request_ids
            else []
        )
        return {
            "run": view(run),
            "results": [view(result) for result in results],
            "tasks": [
                {"request_id": task.request_id, "status": task.status, "wait_reason": task.wait_reason}
                for task in tasks
            ],
            "usage": {
                "calls": len(calls),
                "settled_micros": sum(call.actual_micros or 0 for call in calls if call.status == "Settled"),
                "unknown_calls": sum(call.status == "UsageUnknown" for call in calls),
            },
        }


@router.post("/{identifier}/evaluations/{run_id}/start")
def start_comparison(identifier: str, run_id: str, user: User = Depends(owner)):
    with transaction() as db:
        run = db.scalar(select(EvaluationRun).where(EvaluationRun.id == run_id).with_for_update())
        if not run or run.candidate_id != identifier or run.created_by != user.id:
            raise HTTPException(404, "Evaluation not found.")
        if run.status != "Prepared":
            return view(run)
        if run.evidence_mode != "Live":
            raise HTTPException(422, "Fixture runs cannot dispatch provider requests.")
        try:
            verify_evaluation_snapshot(db, run)
        except (PolicyDenied, UsageUncertain) as exc:
            raise HTTPException(409, str(exc)) from None
        request_ids = []
        for case in run.cases:
            for version_id in (run.baseline_id, run.candidate_id):
                request = Request(
                    user_id=user.id,
                    kind="Evaluation",
                    max_model_calls=1,
                    deadline=now() + timedelta(hours=1),
                    payload={
                        "evaluation_id": run.id,
                        "case_key": case["key"],
                        "agent_version_id": version_id,
                    },
                )
                db.add(request)
                db.flush()
                request_ids.append(request.id)
                db.add(
                    WorkItem(
                        request_id=request.id,
                        role=run.role,
                        subject_kind="Evaluation",
                        subject_id=run.id,
                        input_versions={"agent_version": version_id},
                    )
                )
        run.status = "Running"
        run.summary = {"request_ids": request_ids, "release_eligible": False}
        db.add(
            AuditEvent(
                actor_id=user.id,
                event_type="evaluation.started",
                subject_id=run.id,
                details={"planned_calls": len(request_ids), "evidence_mode": run.evidence_mode},
            )
        )
        return view(run)


class QualityRating(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    result_id: str
    explanation_quality: int = Field(ge=1, le=5)
    factual_and_role_compliant: bool
    notes: str = Field(min_length=10, max_length=2000)


class QualityReview(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    ratings: list[QualityRating] = Field(min_length=1, max_length=32)


@router.post("/{identifier}/evaluations/{run_id}/review")
def review_comparison(identifier: str, run_id: str, body: QualityReview, user: User = Depends(owner)):
    with transaction() as db:
        run = db.scalar(select(EvaluationRun).where(EvaluationRun.id == run_id).with_for_update())
        if not run or run.candidate_id != identifier:
            raise HTTPException(404, "Evaluation not found.")
        if run.status != "AwaitingReview":
            raise HTTPException(
                409, "Only a complete comparison with passing structural checks can be reviewed."
            )
        try:
            digest, results = verified_evidence(db, run)
        except (PolicyDenied, UsageUncertain) as exc:
            raise HTTPException(409, str(exc)) from None
        if len(body.ratings) != len(results) or {rating.result_id for rating in body.ratings} != {
            result.id for result in results
        }:
            raise HTTPException(422, "Review each result exactly once.")
        if any(not rating.notes.strip() for rating in body.ratings):
            raise HTTPException(422, "Provide substantive review notes for every result.")
        passed = all(
            rating.explanation_quality >= 3 and rating.factual_and_role_compliant for rating in body.ratings
        )
        run.status = "Reviewed" if passed else "Rejected"
        run.summary = (run.summary or {}) | {
            "quality_review": {
                "reviewer_id": user.id,
                "reviewed_at": now().isoformat(),
                "evidence_fingerprint": digest,
                "ratings": [rating.model_dump() for rating in body.ratings],
                "passed": passed,
            },
            "release_eligible": passed,
        }
        db.add(
            AuditEvent(
                actor_id=user.id,
                event_type="evaluation.reviewed",
                subject_id=run.id,
                details={"passed": passed, "evidence_fingerprint": digest},
            )
        )
        return view(run)
