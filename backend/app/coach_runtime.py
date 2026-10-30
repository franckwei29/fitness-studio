"""Coach generation produces a versioned candidate, never an implicit activation."""

from datetime import timedelta

from sqlalchemy import func, select

from app.activation import plan_fingerprint
from app.agent_contracts import COMMON_INSTRUCTION
from app.agent_graph import graph_config, persistent_graph
from app.agent_versions import resolve_version
from app.candidate_correction import correction_context, queue_correction
from app.catalogue import exercise_catalogue
from app.cycle_requests import cycle_baseline
from app.cycle_review import CycleFeedback, validate_cycle_adjustment
from app.database import transaction
from app.model_gateway import generate
from app.models import (
    Approval,
    CurrentPlans,
    Member,
    MemberGroup,
    Message,
    ModelCall,
    PlanVersion,
    Request,
    ValidationResult,
    WorkItem,
    now,
)
from app.plan_review import owner_review_context
from app.planning_context import revision_context
from app.policy import execution_context
from app.training import TrainingProposal, validate_training
from app.work_queue import event, lock_claim

COACH_INSTRUCTION = (
    COMMON_INSTRUCTION
    + """
Your role is Coach. Propose a practical training schedule using only the supplied
exercise catalogue and confirmed equipment. Fit the confirmed available days and
session duration, including warmup, cooldown, transitions, and rests. Do not invent
exercise IDs. Use controlled movements and describe progression conservatively.
Explain limitations honestly; the catalogue supports basic home strength training.
This output contains the training component only. A separate Nutrition role handles
food-based meal planning through the application coordinator. The absence of food
entries in your exercise catalogue or meal fields in your output schema is an
intentional role boundary, not evidence that nutrition is unsupported or excluded.
Do not claim that another role has completed, failed, or skipped its work: you do
not have that execution evidence. Keep limitations specific to the training
proposal and the supplied evidence.
Return a candidate for validation, not a claim that a plan is active.
"""
)


def run_coach(claim):
    def prepare(state):
        with transaction() as db:
            item, request = lock_claim(db, claim)
            authorized = execution_context(db, item, request)
            if item.role != "Coach" or request.kind not in {"Training", "Combined", "CycleReview"}:
                raise ValueError("A training generation request is required.")
            baseline = cycle_baseline(db, request, authorized["member_id"])
            version = resolve_version(db, item, authorized, COACH_INSTRUCTION)
            available = {
                str(value).casefold().strip() for value in authorized["profile"].get("equipment", [])
            }
            exercises = [
                row.model_dump()
                for row in exercise_catalogue().values()
                if {name.casefold() for name in row.equipment} <= available
            ]
            if not exercises:
                from app.policy import PolicyDenied

                raise PolicyDenied("Confirm available equipment before requesting a supported training plan.")
            return {
                "context": {
                    "profile": authorized["profile"],
                    "exercise_catalogue": exercises,
                    "request": request.payload["content"],
                    "agent_version_id": version.id,
                    **correction_context(db, item, request, authorized),
                    **revision_context(db, request, authorized, "Coach"),
                    **owner_review_context(db, request, authorized, "Coach"),
                    **(
                        {
                            "baseline_training": baseline.content,
                            "confirmed_cycle_feedback": request.payload["cycle_feedback"],
                            "adjustment_metric": "Match the requested Progression, Maintenance, or Deload using total planned active exercise seconds (sets times seconds_per_set), while satisfying all current profile constraints.",
                        }
                        if baseline
                        else {}
                    ),
                },
                "input_versions": authorized["versions"],
            }

    def infer(state):
        output, call_id = generate(claim, COACH_INSTRUCTION, state["context"], TrainingProposal, 4096)
        return {"candidate": {"proposal": output.model_dump(), "call_id": call_id}}

    def validate(state):
        output = TrainingProposal.model_validate(state["candidate"]["proposal"])
        result = validate_training(output, state["context"]["profile"], exercise_catalogue())
        return {"validation": result, "requires_confirmation": False}

    def commit(state):
        output = TrainingProposal.model_validate(state["candidate"]["proposal"])
        with transaction() as db:
            existing = db.scalar(select(WorkItem).where(WorkItem.id == claim.work_id).with_for_update())
            if existing and existing.status in {"Completed", "Superseded"}:
                return {"outcome": db.get(Request, existing.request_id).result}
            item, request = lock_claim(db, claim)
            authorized = execution_context(db, item, request)
            owner_review_context(db, request, authorized, "Coach")
            member = db.scalar(select(Member).where(Member.id == authorized["member_id"]).with_for_update())
            call = db.get(ModelCall, state["candidate"]["call_id"])
            if not call or call.request_id != request.id or call.status != "Settled":
                raise ValueError("A settled model result is required.")
            validation = validate_training(output, authorized["profile"], exercise_catalogue())
            baseline = cycle_baseline(db, request, member.id)
            if baseline:
                cycle_check = validate_cycle_adjustment(
                    output, baseline.content, CycleFeedback.model_validate(request.payload["cycle_feedback"])
                )
                validation["violations"].extend(cycle_check["violations"])
                validation["passed"] = not validation["violations"]
                validation["basis"]["cycle_review"] = cycle_check["basis"]
            number = (
                db.scalar(
                    select(func.max(PlanVersion.version)).where(
                        PlanVersion.member_id == member.id, PlanVersion.kind == "Training"
                    )
                )
                or 0
            ) + 1
            current = db.get(CurrentPlans, member.id)
            plan = PlanVersion(
                member_id=member.id,
                request_id=request.id,
                kind="Training",
                version=number,
                status="Validated" if validation["passed"] else "Invalid",
                profile_version=member.profile_version,
                config_version=authorized["versions"]["group"],
                agent_version_id=state["context"]["agent_version_id"],
                baseline_id=current.training_id if current else None,
                content=output.model_dump(),
                rationale=output.rationale,
                fingerprint="pending",
            )
            db.add(plan)
            db.flush()
            plan.fingerprint = plan_fingerprint(plan)
            db.add(
                ValidationResult(
                    subject_fingerprint=plan.fingerprint,
                    rule_version=validation["rule_version"],
                    passed=validation["passed"],
                    violations=validation["violations"],
                    basis=validation["basis"],
                )
            )
            result = {
                "status": "Completed" if validation["passed"] else "Waiting",
                "training_id": plan.id,
                "validation": validation,
                "call_id": call.id,
            }
            if validation["passed"]:
                group = db.get(MemberGroup, member.group_id)
                coordinated = request.kind == "Combined" or (
                    request.kind == "CycleReview" and request.payload.get("coordinate_nutrition")
                )
                if (
                    not request.payload.get("progressive")
                    and not coordinated
                    and group.configuration.get("review_required")
                ):
                    db.add(
                        Approval(
                            subject_kind="TrainingPlan",
                            subject_id=plan.id,
                            subject_version=plan.version,
                            fingerprint=plan.fingerprint,
                            scope={"member_id": member.id},
                            context={"training": plan.content, "validation": validation},
                            expires_at=min(request.deadline, now() + timedelta(hours=24)),
                        )
                    )
                text = "Your training candidate is ready for review. It is not active yet."
                if request.kind == "CycleReview":
                    result["status"] = "AwaitingConfirmation"
                if coordinated and not request.payload.get("progressive"):
                    db.add(
                        WorkItem(
                            request_id=request.id,
                            role="Nutrition",
                            subject_kind="TrainingPlan",
                            subject_id=plan.id,
                            input_versions=authorized["versions"],
                        )
                    )
                    result["status"] = "Queued"
                    text += (
                        " We’ll now check your existing meals against this draft; nothing is active yet."
                        if request.payload.get("training_first")
                        else " Nutrition planning is next."
                    )
                item.status = "Completed"
            else:
                result.update(queue_correction(db, item, request, plan))
                text = (
                    "We’re checking and revising your training draft. Your current routine is unchanged."
                    if result["status"] == "Queued"
                    else "We couldn’t finish a suitable training draft. Your current routine is unchanged. Check Activity for the next step."
                )
            request.status = result["status"]
            request.result = result
            item.lease_until = None
            db.add(
                Message(
                    session_id=request.session_id,
                    sender="Coach",
                    content=text,
                    cards=[]
                    if request.payload.get("progressive")
                    else [{"type": "TrainingCandidate", "plan_id": plan.id, "validation": validation}],
                )
            )
            if validation["passed"] and request.payload.get("progressive"):
                from app.workflows import find, training_ready

                training_ready(db, find(db, request), request, plan)
                result["status"] = request.status
                request.result = result
            event(db, request, "training_candidate", **result)
            return {"outcome": result}

    with persistent_graph(prepare=prepare, generate=infer, validate=validate, commit=commit) as graph:
        config = graph_config(claim.work_id)
        snapshot = graph.get_state(config)
        if snapshot.values:
            return graph.invoke(None, config) if snapshot.next else snapshot.values
        return graph.invoke(
            {"request_id": claim.request_id, "work_id": claim.work_id, "role": claim.role}, config
        )
