"""Immutable agent candidates; saving a candidate never changes a live group."""

from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select

from app.admission import fingerprint, view
from app.agent_versions import version_fingerprint
from app.database import transaction
from app.evaluation_records import prepare_evaluation, verify_evaluation_snapshot
from app.evaluation_review import verified_evidence
from app.evaluation_suite import SUITE_VERSION, evaluation_cases
from app.model_catalog import catalog, model_info, validate_options
from app.models import (
    AgentVersion,
    AuditEvent,
    ChangeRequest,
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
    provider: Literal["openai", "gemini"] | None = None
    max_output_tokens: int = Field(default=2048, ge=64, le=4096, strict=True)
    reasoning_effort: Literal["none", "low", "medium", "high"] | None = None
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
            "default_model": get_settings().default_model,
            "default_provider": get_settings().model_provider,
            "models": catalog(get_settings()),
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


class CurrentModelComparison(BaseModel):
    model_config = ConfigDict(extra="forbid")
    group_id: str
    role: Literal["Intake", "Coach", "Nutrition", "TechnicalManager"] = "Coach"
    model: str


@router.post("/compare-current", status_code=201)
def compare_current(body: CurrentModelComparison, user: User = Depends(owner)):
    """Materialize the effective version even before the first member request."""
    from types import SimpleNamespace

    from app.agent_versions import resolve_version

    with transaction() as db:
        db.scalar(select(User).where(User.id == user.id).with_for_update())
        group = db.scalar(select(MemberGroup).where(MemberGroup.id == body.group_id).with_for_update())
        if not group:
            raise HTTPException(404, "Group not found.")
        current = resolve_version(
            db,
            SimpleNamespace(input_versions={}, role=body.role),
            {"group_id": group.id},
            base_instruction(body.role),
        )
        if current.model == body.model:
            raise HTTPException(422, "Choose a different model to compare with the current model.")
        tokens = current.max_output_tokens or 2048
        try:
            info = model_info(body.model)
            validate_options(body.model, info["provider"], tokens, None)
            price_for(body.model)
        except (ValueError, UsageUncertain) as exc:
            raise HTTPException(422, str(exc)) from None
        digest = version_fingerprint(body.role, body.model, current.prompt, tokens, None)
        candidate = db.scalar(select(AgentVersion).where(AgentVersion.fingerprint == digest))
        if not candidate:
            candidate = AgentVersion(
                role=body.role,
                label=f"{body.role} · {body.model}"[:80],
                model=body.model,
                provider=info["provider"],
                prompt=current.prompt,
                max_output_tokens=tokens,
                reasoning_effort=None,
                fingerprint=digest,
            )
            db.add(candidate)
            db.flush()
        run = prepare_evaluation(
            db, user.id, current.id, candidate.id, SUITE_VERSION, evaluation_cases(body.role), "Live"
        )
        run.summary = {"target_group_id": group.id, "target_group_version": group.version}
        db.add(
            AuditEvent(
                actor_id=user.id,
                event_type="evaluation.prepared",
                subject_id=run.id,
                details={
                    "group_id": group.id,
                    "baseline_id": current.id,
                    "candidate_id": candidate.id,
                    "evidence_mode": "Live",
                },
            )
        )
        return view(run) | {"planned_calls": len(run.cases) * 2}


@router.get("/{identifier}")
def version_detail(identifier: str, user: User = Depends(owner)):
    with transaction() as db:
        row = db.get(AgentVersion, identifier)
        if not row:
            raise HTTPException(404, "Agent version not found.")
        groups = db.scalars(select(MemberGroup))
        return view(row) | {
            "configured_groups": [
                {"id": group.id, "name": group.name}
                for group in groups
                if group.configuration.get("agent_versions", {}).get(row.role) == row.id
            ]
        }


@router.post("", status_code=201)
def create_candidate(body: Candidate, user: User = Depends(owner)):
    try:
        price_for(body.model)
        validate_options(body.model, body.provider, body.max_output_tokens, body.reasoning_effort)
    except (UsageUncertain, ValueError):
        raise HTTPException(
            422, "This model has no supported pricing and accounting configuration."
        ) from None
    prompt = base_instruction(body.role) + "\n\nCandidate-specific instructions:\n" + body.instructions
    digest = version_fingerprint(body.role, body.model, prompt, body.max_output_tokens, body.reasoning_effort)
    with transaction() as db:
        # Serialize candidate creation across owner sessions without a new lock service.
        db.scalar(select(User).where(User.id == user.id).with_for_update())
        existing = db.scalar(select(AgentVersion).where(AgentVersion.fingerprint == digest))
        if existing:
            return view(existing)
        row = AgentVersion(
            role=body.role,
            label=body.label,
            model=body.model,
            prompt=prompt,
            fingerprint=digest,
            provider=model_info(body.model)["provider"],
            max_output_tokens=body.max_output_tokens,
            reasoning_effort=body.reasoning_effort,
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


def decision_fingerprint(run):
    return fingerprint(
        {
            "id": run.id,
            "status": run.status,
            "baseline": run.baseline_fingerprint,
            "candidate": run.candidate_fingerprint,
            "suite": run.suite_fingerprint,
        }
    )


def evaluation_release(db, run):
    return db.scalar(
        select(ChangeRequest)
        .where(
            ChangeRequest.kind == "AgentRelease",
            ChangeRequest.candidate["evaluation_id"].as_string() == run.id,
        )
        .order_by(ChangeRequest.created_at.desc())
        .limit(1)
    )


class KeepCurrentDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fingerprint: str


@router.post("/{identifier}/evaluations/{run_id}/keep-current")
def keep_current(identifier: str, run_id: str, body: KeepCurrentDecision, user: User = Depends(owner)):
    with transaction() as db:
        run = db.scalar(select(EvaluationRun).where(EvaluationRun.id == run_id).with_for_update())
        if not run or run.candidate_id != identifier:
            raise HTTPException(404, "Evaluation not found.")
        if run.status not in {
            "Completed",
            "AwaitingReview",
            "Reviewed",
            "Rejected",
            "FailedChecks",
            "Incomplete",
        }:
            raise HTTPException(409, "Wait until this comparison has finished.")
        if decision_fingerprint(run) != body.fingerprint:
            raise HTTPException(409, "The comparison changed. Refresh its results before deciding.")
        if evaluation_release(db, run):
            raise HTTPException(
                409, "This comparison already has a release decision. Open its release record."
            )
        if (run.summary or {}).get("owner_decision", {}).get("choice") != "KeepCurrent":
            run.summary = (run.summary or {}) | {
                "owner_decision": {
                    "choice": "KeepCurrent",
                    "actor_id": user.id,
                    "decided_at": now().isoformat(),
                }
            }
            db.add(
                AuditEvent(
                    actor_id=user.id,
                    event_type="evaluation.current_model_kept",
                    subject_id=run.id,
                    details={"fingerprint": body.fingerprint, "configuration_unchanged": True},
                )
            )
        return view(run)


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
        models = []
        for version_id in (run.baseline_id, run.candidate_id):
            version = db.get(AgentVersion, version_id)
            matching = [row for row in results if row.agent_version_id == version_id]
            version_requests = {
                task.request_id for task in tasks if task.input_versions.get("agent_version") == version_id
            }
            model_calls = [call for call in calls if call.request_id in version_requests]
            settled = [
                call for call in model_calls if call.status == "Settled" and call.actual_micros is not None
            ]
            complete = len(matching) == len(run.cases) and len(settled) == len(run.cases)
            times = [row.latency_ms for row in matching if row.latency_ms is not None]
            models.append(
                {
                    "version_id": version_id,
                    "model": version.model,
                    "provider": version.provider,
                    "passed": sum(row.passed for row in matching),
                    "expected": len(run.cases),
                    "recorded": len(matching),
                    "average_latency_ms": sum(times) / len(times) if times else None,
                    "average_cost_micros": sum(call.actual_micros for call in settled) / len(settled)
                    if complete and settled
                    else None,
                    "cost_complete": complete,
                    "max_output_tokens": version.max_output_tokens,
                    "reasoning_effort": version.reasoning_effort,
                }
            )
        linked_release = evaluation_release(db, run)
        return {
            "decision_fingerprint": decision_fingerprint(run),
            "release": {"id": linked_release.id, "status": linked_release.status} if linked_release else None,
            "models": models,
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
        run.summary = (run.summary or {}) | {"request_ids": request_ids, "release_eligible": False}
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
