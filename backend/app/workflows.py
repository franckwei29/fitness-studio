"""Member direction approvals, bounded revisions and exact-stage transitions."""

from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.admission import fingerprint
from app.database import transaction
from app.models import (
    Approval,
    AuditEvent,
    ChatSession,
    CurrentPlans,
    IdempotencyRecord,
    Member,
    MemberGroup,
    Message,
    PlanPair,
    PlanVersion,
    Request,
    User,
    Workflow,
    WorkItem,
    now,
)
from app.security import principal
from app.work_queue import event

router = APIRouter(prefix="/api/me/workflows")
TERMINAL = {"Completed", "Cancelled", "Stopped"}


def find(db, request):
    return db.scalar(select(Workflow).where(Workflow.request_id == request.id))


def start(db, request, role):
    flow = find(db, request)
    if flow:
        return flow
    member = db.scalar(select(Member).where(Member.user_id == request.user_id).with_for_update())
    conflict = db.scalar(
        select(Request.id)
        .join(Workflow, Workflow.request_id == Request.id)
        .where(
            Request.user_id == request.user_id,
            Request.id != request.id,
            Request.status.in_(["Queued", "Running", "Waiting", "AwaitingConfirmation"]),
            Request.deadline > now(),
        )
        .limit(1)
    )
    if conflict:
        raise HTTPException(
            409,
            "Another update already covers this routine. Open its conversation in Activity to continue it.",
        )
    if not request.session_id:
        session = db.scalar(
            select(ChatSession)
            .where(ChatSession.user_id == request.user_id, ChatSession.deleted_at.is_(None))
            .order_by(ChatSession.created_at.desc())
            .limit(1)
        )
        if not session:
            session = ChatSession(user_id=request.user_id, active_role=role, title="Plan discussion")
            db.add(session)
            db.flush()
        request.session_id = session.id
    current = db.get(CurrentPlans, member.id)
    flow = Workflow(
        request_id=request.id,
        user_id=request.user_id,
        stage="GeneratingTraining" if role == "Coach" else "GeneratingMeals",
        baseline={
            "version": current.version,
            "training_id": current.training_id,
            "nutrition_id": current.nutrition_id,
            "profile": member.profile_version,
            "group_id": member.group_id,
            "group": db.get(MemberGroup, member.group_id).version,
        },
    )
    db.add(flow)
    db.flush()
    request.payload = request.payload | {"progressive": True, "workflow_id": flow.id}
    set_stage(
        db,
        flow,
        request,
        flow.stage,
        role,
        "I’ll prepare a draft for us to discuss. Nothing becomes active yet.",
    )
    return flow


def set_stage(db, flow, request, stage, role, text):
    flow.stage, flow.revision = stage, flow.revision + 1
    session = db.get(ChatSession, request.session_id) if request.session_id else None
    if session:
        session.active_role = role
        db.add(
            Message(
                session_id=session.id,
                sender=role,
                content=text,
                cards=[
                    {
                        "type": "Workflow",
                        "stage": stage,
                        "workflow_id": flow.id,
                        "revision": flow.revision,
                        "request_id": request.id,
                    }
                ],
            )
        )
    event(db, request, "workflow_changed", stage=stage, revision=flow.revision, status=request.status)


def endorsement(plan):
    return {"id": plan.id, "fingerprint": plan.fingerprint, "at": now().isoformat()}


def verify_context(db, flow):
    from app.planning_context import verify_planning_baseline
    from app.policy import PolicyDenied

    request = db.get(Request, flow.request_id)
    member = db.scalar(select(Member).where(Member.user_id == flow.user_id))
    group = db.get(MemberGroup, member.group_id) if member else None
    try:
        if (
            not member
            or member.status != "Accepted"
            or not group
            or member.profile_version != flow.baseline.get("profile")
            or member.group_id != flow.baseline.get("group_id")
            or group.version != flow.baseline.get("group")
        ):
            raise PolicyDenied(
                "Your profile or studio settings changed. Refresh this draft against your latest details."
            )
        verify_planning_baseline(db, request, member.id)
        current = db.get(CurrentPlans, member.id)
        required = (
            {"training", "nutrition"}
            if request.kind == "Combined"
            else {"training"}
            if request.kind in {"Training", "CycleReview"}
            else {"nutrition"}
        )
        if any(group.configuration.get("capabilities", {}).get(cap) is not True for cap in required):
            raise PolicyDenied(
                "A service needed for this update is disabled. Ask the studio owner to review your membership services."
            )
        if current.version != flow.baseline["version"]:
            raise PolicyDenied("Your current routine changed. Review the latest plan before continuing.")
    except PolicyDenied as exc:
        raise HTTPException(409, str(exc)) from None
    return member, group


def queue(db, flow, request, role, text=""):
    if request.model_calls >= request.max_model_calls or len(flow.feedback) >= 12:
        raise HTTPException(
            409, "This update reached its processing limit. Cancel it before starting another update."
        )
    if db.scalar(
        select(WorkItem.id)
        .where(WorkItem.request_id == request.id, WorkItem.status.in_(["Queued", "Running", "Waiting"]))
        .limit(1)
    ):
        raise HTTPException(
            409, "This draft is still being prepared. Wait for its result before revising it."
        )
    subject = flow.training_id if role == "Nutrition" else request.session_id
    db.add(
        WorkItem(
            request_id=request.id,
            role=role,
            subject_kind="TrainingPlan" if role == "Nutrition" and flow.training_id else "Conversation",
            subject_id=subject or request.session_id,
            input_versions={},
        )
    )
    request.status = "Queued"
    request.result = (request.result or {}) | {"status": "Queued"}
    set_stage(
        db,
        flow,
        request,
        "GeneratingTraining"
        if role == "Coach"
        else "CheckingMeals"
        if flow.training_id
        else "GeneratingMeals",
        role,
        text or "I’ll prepare the next draft using your feedback.",
    )


def training_ready(db, flow, request, plan):
    flow.training_id = plan.id
    flow.nutrition_id = flow.pair_id = None
    flow.training_endorsement = flow.nutrition_endorsement = None
    request.status = "AwaitingConfirmation"
    set_stage(
        db,
        flow,
        request,
        "TrainingDiscussion",
        "Coach",
        "Your training draft is ready. Tell me what you would change, or approve this direction to continue. Your current routine stays in use.",
    )


def meals_ready(db, flow, request, plan, pair, meal_changes):
    flow.nutrition_id, flow.pair_id = plan.id, pair.id if pair else None
    if pair and not flow.training_id:
        # An existing, unchanged training version is a baseline, not a new draft to endorse.
        flow.training_id = pair.training_id
    flow.nutrition_endorsement = None
    request.status = "AwaitingConfirmation"
    if request.kind == "Nutrition" and (request.result or {}).get("training_impact"):
        set_stage(
            db,
            flow,
            request,
            "TrainingImpact",
            "Nutrition",
            "This meal direction may need a training adjustment. Review the specific impact before choosing which plan to keep.",
        )
    elif request.kind == "FoodSubstitution":
        flow.nutrition_endorsement = endorsement(plan)
        finalize(db, flow, request)
    elif request.payload.get("training_first") and not meal_changes:
        flow.nutrition_endorsement = endorsement(plan)
        finalize(
            db,
            flow,
            request,
            "Your meals can stay the same. Review the final summary before activating this update.",
        )
    else:
        stage = "MealImpact" if request.payload.get("training_first") else "MealDiscussion"
        text = (
            "We found a meal adjustment to discuss. Review the reason and suggestions, or keep your current meals and revisit training."
            if stage == "MealImpact"
            else "Your meal draft is ready. Discuss any changes before approving this direction."
        )
        set_stage(db, flow, request, stage, "Nutrition", text)


def finalize(
    db, flow, request, text="Review the final changes. Your current routine stays in use until you confirm."
):
    member, group = verify_context(db, flow)
    from app.catalogue import exercise_catalogue, food_catalogue

    plans = [db.get(PlanVersion, x) for x in (flow.training_id, flow.nutrition_id) if x]
    pair = db.get(PlanPair, flow.pair_id) if flow.pair_id else None
    from app.activation import pair_fingerprint

    source = db.get(PlanVersion, flow.nutrition_id or flow.training_id)
    kind, identifier, digest = (
        ("PlanPair", pair.id, pair_fingerprint(*plans))
        if pair
        else ("TrainingPlan" if source.kind == "Training" else "NutritionPlan", source.id, source.fingerprint)
    )
    if group.configuration.get("review_required"):
        pending = db.scalar(
            select(Approval)
            .where(
                Approval.subject_id == identifier,
                Approval.fingerprint == digest,
                Approval.decision.in_(["Pending", "Approved"]),
            )
            .limit(1)
        )
        if not pending:
            db.add(
                Approval(
                    subject_kind=kind,
                    subject_id=identifier,
                    subject_version=1,
                    fingerprint=digest,
                    scope={"member_id": member.id},
                    context={
                        "training": next((p.content for p in plans if p.kind == "Training"), None),
                        "nutrition": next((p.content for p in plans if p.kind == "Nutrition"), None),
                        "validation": (request.result or {}).get("validation"),
                        "catalogue_labels": {
                            p.id: p.name for p in [*food_catalogue().values(), *exercise_catalogue().values()]
                        },
                    },
                    expires_at=min(request.deadline, now() + timedelta(hours=24)),
                )
            )
        stage = (
            "FinalReview"
            if pending and pending.decision == "Approved" and pending.expires_at > now()
            else "OwnerReview"
        )
    else:
        stage = "FinalReview"
    request.status = "AwaitingConfirmation"
    set_stage(
        db,
        flow,
        request,
        stage,
        "Nutrition" if flow.nutrition_id else "Coach",
        "Your draft is submitted for studio review. You will be notified when you can confirm."
        if stage == "OwnerReview"
        else text,
    )


def view(db, flow):
    request = db.get(Request, flow.request_id)
    from app.member_status import waiting_status

    stage = (
        request.status
        if request.status in TERMINAL
        else "Expired"
        if request.deadline <= now()
        else flow.stage
    )
    actions = []
    issue = None
    if stage not in TERMINAL | {"Expired"}:
        try:
            verify_context(db, flow)
        except HTTPException as exc:
            issue = exc.detail
        if not issue:
            actions = {
                "TrainingDiscussion": ["endorse_training", "revise_training"],
                "MealDiscussion": ["endorse_meals", "revise_meals", "revise_training"],
                "MealImpact": ["review_meals", "keep_meals"],
                "TrainingImpact": ["keep_training", "keep_meal_draft"],
                "FinalReview": ["revise_training", "revise_meals"],
            }.get(stage, [])
            if request.kind == "FoodSubstitution":
                actions = [a for a in actions if a != "revise_training"]
            actions = [
                a
                for a in actions
                if not (a.endswith("training") and not flow.training_id)
                and not (a.endswith("meals") and not flow.nutrition_id)
            ]
        actions = actions + (["refresh_context"] if issue else []) + ["cancel"]
    return {
        "summaries": [
            {
                "kind": p.kind,
                "title": p.content.get("title"),
                "rationale": p.rationale,
                "items": [
                    row.get("title") or row.get("name")
                    for row in p.content.get("sessions", p.content.get("meals", []))
                ],
            }
            for identifier in (flow.training_id, flow.nutrition_id)
            if identifier and (p := db.get(PlanVersion, identifier))
        ],
        "id": flow.id,
        "request_id": request.id,
        "stage": stage,
        "revision": flow.revision,
        "training_id": flow.training_id,
        "nutrition_id": flow.nutrition_id,
        "pair_id": flow.pair_id,
        "training_endorsed": bool(flow.training_endorsement),
        "nutrition_endorsed": bool(flow.nutrition_endorsement),
        "actions": actions,
        "issue": issue,
        "goal": request.payload.get("original_content", request.payload.get("content")),
        "meal_review": (request.result or {}).get("meal_review"),
        "baseline_version": flow.baseline.get("version"),
        "conversation_id": request.session_id,
        "wait_message": (
            waiting_status(
                db.scalar(
                    select(WorkItem.wait_reason)
                    .where(WorkItem.request_id == request.id, WorkItem.status == "Waiting")
                    .limit(1)
                )
                or "PolicyReview"
            )[1]
        )
        if request.status == "Waiting"
        else None,
        "status": request.status,
    }


class FlowAction(BaseModel):
    action: Literal[
        "endorse_training",
        "endorse_meals",
        "revise_training",
        "revise_meals",
        "review_meals",
        "keep_meals",
        "keep_training",
        "keep_meal_draft",
        "cancel",
        "refresh_context",
    ]
    expected_revision: int
    operation_key: str = Field(min_length=8, max_length=100)
    target_id: str | None = None
    target_fingerprint: str | None = None
    feedback: str = Field(default="", max_length=4000)


def perform(db, flow, request, body, user):
    if flow.revision != body.expected_revision:
        raise HTTPException(409, "This draft changed. Review the latest version before deciding.")
    if request.status in TERMINAL or request.deadline <= now():
        raise HTTPException(409, "This update has ended or expired.")
    if body.action == "cancel":
        # The shared cancellation endpoint stops live work and retains billing evidence.
        raise HTTPException(409, "Use Cancel update to stop the request and its active work.")
    if body.action == "refresh_context":
        member = db.scalar(select(Member).where(Member.user_id == flow.user_id))
        if not member or member.status != "Accepted":
            raise HTTPException(409, "Complete your profile review before refreshing this draft.")
        current = db.get(CurrentPlans, member.id)
        group = db.get(MemberGroup, member.group_id)
        required = (
            {"training", "nutrition"}
            if request.kind == "Combined"
            else {"training"}
            if request.kind in {"Training", "CycleReview"}
            else {"nutrition"}
        )
        if any(group.configuration.get("capabilities", {}).get(cap) is not True for cap in required):
            raise HTTPException(
                409, "The studio must restore the required services before this draft can be refreshed."
            )
        from app.models import ModelCall

        if db.scalar(
            select(ModelCall.id)
            .where(
                ModelCall.request_id == request.id,
                ModelCall.status.in_(["Reserved", "Dispatching", "UsageUnknown"]),
            )
            .limit(1)
        ):
            raise HTTPException(
                409,
                "A model call still needs reconciliation. Ask the studio owner to review the request before refreshing it.",
            )
        for waiting in db.scalars(
            select(WorkItem).where(
                WorkItem.request_id == request.id,
                WorkItem.status == "Waiting",
                WorkItem.wait_reason == "PolicyReview",
            )
        ):
            waiting.status = "Superseded"
        flow.baseline = {
            "version": current.version,
            "training_id": current.training_id,
            "nutrition_id": current.nutrition_id,
            "profile": member.profile_version,
            "group_id": member.group_id,
            "group": group.version,
        }
        request.payload = {k: v for k, v in request.payload.items() if k != "owner_review"} | {
            "plan_baseline": {
                "version": current.version,
                "training_id": current.training_id,
                "nutrition_id": current.nutrition_id,
            }
        }
        flow.training_endorsement = flow.nutrition_endorsement = None
        role = "Coach" if request.kind in {"Training", "Combined", "CycleReview"} else "Nutrition"
        if request.kind in {"FoodSubstitution", "CycleReview"}:
            raise HTTPException(
                409,
                "Your original feedback or selected food is tied to an older routine. Cancel and start a fresh review from My plans.",
            )
        queue(
            db,
            flow,
            request,
            role,
            "I’ll refresh this draft against your confirmed details. Previous approvals no longer apply.",
        )
        db.add(
            AuditEvent(
                actor_id=user.id,
                request_id=request.id,
                subject_id=flow.id,
                event_type="workflow.refreshed",
                details={"revision": flow.revision},
            )
        )
        return
    verify_context(db, flow)
    if body.action not in view(db, flow)["actions"]:
        raise HTTPException(409, "This action is not available at the current stage.")
    if body.action.startswith("endorse_"):
        plan = db.get(
            PlanVersion, flow.training_id if body.action == "endorse_training" else flow.nutrition_id
        )
        from app.activation import plan_fingerprint

        if (
            not plan
            or plan.id != body.target_id
            or plan.fingerprint != body.target_fingerprint
            or plan.fingerprint != plan_fingerprint(plan)
            or plan.status != "Validated"
        ):
            raise HTTPException(409, "Review the latest validated draft before approving its direction.")
        if body.action == "endorse_training":
            flow.training_endorsement = endorsement(plan)
            if request.kind == "Combined" or (
                request.kind == "CycleReview" and request.payload.get("coordinate_nutrition")
            ):
                # Only an explicit direction approval can dispatch the next specialist.
                if flow.baseline.get("nutrition_id"):
                    request.payload = request.payload | {"training_first": True}
                queue(
                    db,
                    flow,
                    request,
                    "Nutrition",
                    "This training direction is recorded, not activated. Next, we’ll check or prepare your meals.",
                )
            else:
                finalize(db, flow, request)
        else:
            flow.nutrition_endorsement = endorsement(plan)
            finalize(db, flow, request)
    elif body.action == "review_meals":
        set_stage(
            db,
            flow,
            request,
            "MealDiscussion",
            "Nutrition",
            "Let’s review these meal suggestions. Tell me what you would change before approving them.",
        )
    else:
        role = "Coach" if body.action in {"revise_training", "keep_meals", "keep_meal_draft"} else "Nutrition"
        if not body.feedback.strip() and body.action not in {
            "keep_meals",
            "keep_training",
            "keep_meal_draft",
        }:
            raise HTTPException(422, "Describe the changes you want in this draft.")
        flow.feedback = flow.feedback + [
            {
                "role": role,
                "text": body.feedback
                or {
                    "keep_training": "Keep my current training unchanged and adapt these meals.",
                    "keep_meal_draft": "Keep this meal draft and adapt training to fit it.",
                }.get(body.action, "Keep my current meals unchanged and adapt training to fit them."),
            }
        ]
        if role == "Coach":
            if request.kind == "FoodSubstitution":
                raise HTTPException(
                    409,
                    "A food replacement cannot revise training. Finish or cancel it before starting a training update.",
                )
            if request.kind == "Nutrition":
                request.kind = "Combined"
                request.payload = request.payload | {
                    "training_first": True,
                    "preferred_meal_id": flow.nutrition_id,
                }
            flow.training_endorsement = flow.nutrition_endorsement = None
            if body.action == "keep_meals":
                request.payload = request.payload | {"keep_meals": True}
        else:
            flow.nutrition_endorsement = None
        queue(db, flow, request, role)
    db.add(
        AuditEvent(
            actor_id=user.id,
            request_id=request.id,
            subject_id=flow.id,
            event_type="workflow.decision",
            details={"action": body.action, "revision": flow.revision, "target_id": body.target_id},
        )
    )


@router.get("/{identifier}")
def get_flow(identifier: str, user: User = Depends(principal)):
    with transaction() as db:
        flow = db.scalar(select(Workflow).where(Workflow.id == identifier, Workflow.user_id == user.id))
        if not flow:
            raise HTTPException(404, "Update not found.")
        return view(db, flow)


@router.post("/{identifier}/actions")
def action(identifier: str, body: FlowAction, user: User = Depends(principal)):
    with transaction() as db:
        member = db.scalar(select(Member).where(Member.user_id == user.id).with_for_update())
        flow = db.scalar(
            select(Workflow).where(Workflow.id == identifier, Workflow.user_id == user.id).with_for_update()
        )
        if not member or not flow:
            raise HTTPException(404, "Update not found.")
        request = db.scalar(select(Request).where(Request.id == flow.request_id).with_for_update())
        digest = fingerprint(body.model_dump(exclude={"operation_key"}))
        previous = db.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.user_id == user.id,
                IdempotencyRecord.operation == f"workflow:{identifier}",
                IdempotencyRecord.key == body.operation_key,
            )
        )
        if previous:
            if previous.fingerprint != digest:
                raise HTTPException(409, "This action key was used for a different decision.")
            return previous.result
        perform(db, flow, request, body, user)
        result = view(db, flow)
        db.add(
            IdempotencyRecord(
                user_id=user.id,
                operation=f"workflow:{identifier}",
                key=body.operation_key,
                fingerprint=digest,
                result=result,
            )
        )
        return result


def discussion_context(db, session_id, user_id):
    flow = db.scalar(
        select(Workflow)
        .join(Request, Workflow.request_id == Request.id)
        .where(
            Workflow.user_id == user_id,
            Request.session_id == session_id,
            Request.status == "AwaitingConfirmation",
        )
        .order_by(Workflow.created_at.desc())
        .limit(1)
    )
    if not flow:
        return None
    role = db.get(ChatSession, session_id).active_role
    plan = db.get(PlanVersion, flow.training_id if role == "Coach" else flow.nutrition_id)
    # Keep role-specific content inside its established data boundary.
    return {
        "workflow_id": flow.id,
        "stage": flow.stage,
        "revision": flow.revision,
        "draft": plan.content if plan else None,
        "role": role,
    }


def execution_gate(db, item, request):
    from app.policy import PolicyDenied

    flow = find(db, request)
    if not flow:
        raise PolicyDenied("This update is missing its recorded stage.")
    allowed = {"Coach": {"GeneratingTraining"}, "Nutrition": {"GeneratingMeals", "CheckingMeals"}}
    if item.role in allowed and flow.stage not in allowed[item.role]:
        raise PolicyDenied("This update is waiting for a member decision, not automatic processing.")
    if item.role == "Nutrition" and flow.stage == "CheckingMeals":
        plan = db.get(PlanVersion, flow.training_id)
        if not plan or (
            flow.training_id != flow.baseline.get("training_id")
            and (
                flow.training_endorsement is None
                or flow.training_endorsement.get("fingerprint") != plan.fingerprint
            )
        ):
            raise PolicyDenied("Approve the latest training direction before reviewing meals.")


def activation_gate(db, request):
    from app.activation import ActivationConflict, plan_fingerprint

    flow = find(db, request)
    if not flow:
        return
    if flow.stage != "FinalReview":
        raise ActivationConflict(
            "Complete the draft discussion and required reviews before activating this update."
        )
    try:
        verify_context(db, flow)
    except HTTPException as exc:
        raise ActivationConflict(exc.detail) from None
    for identifier, approved, baseline in [
        (flow.training_id, flow.training_endorsement, flow.baseline.get("training_id")),
        (flow.nutrition_id, flow.nutrition_endorsement, flow.baseline.get("nutrition_id")),
    ]:
        if not identifier or identifier == baseline:
            continue
        plan = db.get(PlanVersion, identifier)
        if (
            not plan
            or not approved
            or approved.get("id") != plan.id
            or approved.get("fingerprint") != plan.fingerprint
            or plan.fingerprint != plan_fingerprint(plan)
        ):
            raise ActivationConflict("The latest draft direction needs your approval before activation.")


@router.post("/adopt/{request_id}")
def adopt_legacy(request_id: str, user: User = Depends(principal)):
    """Adopt only the member's explicitly selected idle draft; never change live work."""
    with transaction() as db:
        member = db.scalar(select(Member).where(Member.user_id == user.id).with_for_update())
        request = db.scalar(
            select(Request).where(Request.id == request_id, Request.user_id == user.id).with_for_update()
        )
        if not member or not request:
            raise HTTPException(404, "Draft update not found.")
        flow = find(db, request)
        if flow:
            return view(db, flow)
        if request.status in {"Cancelled", "Stopped"} or request.deadline <= now():
            raise HTTPException(
                409, "This saved draft ended or expired. Start a new update in the conversation."
            )
        if db.scalar(
            select(WorkItem.id)
            .where(WorkItem.request_id == request.id, WorkItem.status.in_(["Queued", "Running", "Waiting"]))
            .limit(1)
        ):
            raise HTTPException(
                409,
                "This legacy update has unfinished work. Wait for it to finish or cancel it in Activity before adopting its draft.",
            )
        plans = list(
            db.scalars(
                select(PlanVersion)
                .where(PlanVersion.request_id == request.id, PlanVersion.status == "Validated")
                .order_by(PlanVersion.version.desc())
            )
        )
        if not plans:
            raise HTTPException(409, "There is no validated draft to discuss.")
        training = next((p for p in plans if p.kind == "Training"), None)
        meal = next((p for p in plans if p.kind == "Nutrition"), None)
        flow = start(db, request, "Coach" if training else "Nutrition")
        request.status = "AwaitingConfirmation"
        if training:
            training_ready(db, flow, request, training)
        else:
            flow.nutrition_id = meal.id
            pairs = list(db.scalars(select(PlanPair).where(PlanPair.nutrition_id == meal.id)))
            if pairs:
                flow.pair_id = pairs[0].id
                flow.training_id = pairs[0].training_id
            set_stage(
                db,
                flow,
                request,
                "MealDiscussion",
                "Nutrition",
                "Review this saved meal draft before approving its direction.",
            )
        db.add(
            AuditEvent(
                actor_id=user.id,
                request_id=request.id,
                subject_id=flow.id,
                event_type="workflow.legacy_adopted",
                details={"preserved_work_and_approvals": True},
            )
        )
        return view(db, flow)


def profile_changed(db, member):
    for flow, request in db.execute(
        select(Workflow, Request)
        .join(Request, Workflow.request_id == Request.id)
        .where(
            Workflow.user_id == member.user_id,
            Request.status.in_(["Queued", "Running", "Waiting", "AwaitingConfirmation"]),
            Request.deadline > now(),
        )
    ):
        if flow.baseline.get("profile") == member.profile_version:
            continue
        flow.training_endorsement = flow.nutrition_endorsement = None
        set_stage(
            db,
            flow,
            request,
            "ContextReview",
            "Coach" if request.kind in {"Training", "Combined", "CycleReview"} else "Nutrition",
            "Your confirmed details changed. Your original goal is saved; refresh this draft against the new details before continuing.",
        )
