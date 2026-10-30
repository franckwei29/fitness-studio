"""Durable, schema-constrained intent routing with explicit business boundaries."""

from typing import Literal

from pydantic import Field
from sqlalchemy import select

from app.agent_contracts import COMMON_INSTRUCTION, StrictModel
from app.agent_graph import graph_config, persistent_graph
from app.agent_versions import resolve_version
from app.database import transaction
from app.member_actions import ActionDetails, pending_actions, proposal
from app.member_experience import plan_summary
from app.model_gateway import generate
from app.models import (
    Assessment,
    ChatSession,
    CurrentPlans,
    Member,
    MemberGroup,
    Message,
    ModelCall,
    Request,
    WorkItem,
)
from app.policy import execution_context
from app.work_queue import event, lock_claim

AUTO_INSTRUCTION = (
    COMMON_INSTRUCTION
    + """
This is a unified fitness conversation. Follow the supplied IntentReply schema.
Classify the user's intended ACTION, not just their topic. Keep the current host
for reading both plans and basic explanations across training and nutrition.
Answer: general questions, explanations and hypothetical discussion. Never invent
personal advice beyond supplied usable plans. ViewPlans: requests to see current
plans; the application renders both plans. Clarify: ask one concise question when
an action or its target is ambiguous. 'Could this be better?' is discussion, not
permission to change. Training/Nutrition/Combined: only explicit requests to
create or revise drafts; NOT viewing, explaining, or asking hypothetical questions.
Intake: explicit profile changes or supplying details during an intake conversation;
never treat a hypothetical condition as a real profile change. Consult: explicit
request to talk to a named role. consultation_role selects that role. For deeper
cross-domain questions, Answer and offer_consultation rather than switching without
consent. Do not repeat a specialist invitation after the user declined it in this topic. There is no task-type menu: never tell the user to select a request type.
Return reply in the user's language, but interface labels and normalized values
remain English. Do not claim a draft is already created or active. The app decides
which actions can run. Existing plan cards are not calendar appointments. No diagnosis,
medical prescription, new personal nutrient target, or bypass of admission or policy.
Do not change an existing plan for mere feedback; offer its feedback control instead.
Feedback: record training or meal feedback in an inline preview. Substitution: replace
specific foods in the CURRENT active meal plan. CycleReview: confirm a completed training
period and request the next cycle. ViewAllowance: remaining usage and limits. ViewUpdates:
notifications and review outcomes. For Feedback, Substitution and CycleReview populate
action_details ONLY with facts explicitly supplied by the member. Use supplied current
plan food indices (zero-based) in selections; only when the meal and food are unambiguous.
Never guess discomfort, completion, difficulty, dates or quantities. Carry explicit answers
from pending_member_actions when the member is answering its missing fields. Preserve the
original replacement target in preference, including a requested banana etc. Multiple
selected foods belong in one preview. Clarify when uncertain. Do not send the member to a
page or claim saving: the app shows an inline preview, asks only for missing details and
requires confirmation. Questions about possibility are Answer, not a change request.
ViewActivity: read progress or review status. ReviewDrafts: requests to activate or
accept a plan; show the exact drafts for explicit confirmation; never activate from model text.
Cancel: stop or withdraw a pending update; show the user's pending updates for explicit
cancellation. If a new restriction or injury affects a requested plan change, use
Intake first so those facts are reviewed before any new planning.
If the user wants to keep meals unchanged, set keep_meals true. If this message answers
an earlier clarification, put the resolved original goal and the new answer in task_goal
without widening scope; otherwise leave task_goal null.
When draft_context is present, use its exact current draft for explanations. Explicit
requests to adjust that draft remain Training or Nutrition within the SAME update;
ordinary questions about it remain Answer. Never claim changes were saved from an Answer.
An ambiguous OK is Clarify; final activation must use the displayed confirmation control.
"""
)


class IntentReply(StrictModel):
    intent: Literal[
        "Answer",
        "ViewPlans",
        "ViewActivity",
        "ReviewDrafts",
        "Feedback",
        "Substitution",
        "CycleReview",
        "ViewAllowance",
        "ViewUpdates",
        "Cancel",
        "Clarify",
        "Intake",
        "Training",
        "Nutrition",
        "Combined",
        "Consult",
    ]
    action_details: ActionDetails | None = None
    reply: str = Field(min_length=1, max_length=5000)
    consultation_role: Literal["Intake", "Coach", "Nutrition"] = "Intake"
    offer_consultation: Literal["None", "Coach", "Nutrition"] = "None"
    keep_meals: bool = False
    task_goal: str | None = Field(default=None, max_length=12000)


def publish(db, request, item, text, cards=None):
    message = Message(
        session_id=request.session_id,
        sender=request.payload.get("speaker", item.role),
        content=text,
        cards=cards or [],
    )
    db.add(message)
    db.flush()
    request.result = {"message_id": message.id, "status": "Completed"}
    request.status = item.status = "Completed"
    item.lease_until = None
    event(db, request, "completed", message_id=message.id)
    return message


def apply_intent(db, request, item, decision):
    session = db.scalar(select(ChatSession).where(ChatSession.id == request.session_id).with_for_update())
    member = db.scalar(select(Member).where(Member.user_id == request.user_id).with_for_update())
    cards = []
    text = decision.reply
    if decision.intent == "Consult":
        # A slow inference must not undo a newer, explicitly chosen host.
        newer_choice = db.scalar(
            select(Request.id)
            .where(
                Request.session_id == session.id,
                Request.created_at > request.created_at,
                Request.result["host_changed"].as_boolean().is_(True),
            )
            .limit(1)
        )
        if newer_choice:
            publish(
                db, request, item, "Your latest specialist choice is already in place. We can continue there."
            )
            return
        role = decision.consultation_role
        if member and member.status == "Accepted" and role in {"Coach", "Nutrition"}:
            group = db.get(MemberGroup, member.group_id)
            capability = "training" if role == "Coach" else "nutrition"
            if not group.configuration.get("capabilities", {}).get(capability):
                publish(
                    db,
                    request,
                    item,
                    "That specialist is currently unavailable. You can still view your saved plans.",
                )
                return
        if role != session.active_role:
            session.active_role = role
            cards.append({"type": "Handoff", "role": role})
        request.payload = request.payload | {"speaker": role}
        text = {
            "Intake": "I can help with your details and getting started. We can continue from your question above.",
            "Coach": "I can help you explore your training. We can continue from your question above.",
            "Nutrition": "I can help you explore your meals. We can continue from your question above.",
        }[role]
    if decision.intent == "ViewPlans":
        cards.append({"type": "PlanSummary"})
    if decision.intent in {"ViewActivity", "Cancel"}:
        cards.append({"type": "Activity"})
        if decision.intent == "Cancel":
            text = "Choose the update below and confirm cancellation. Your current plans stay unchanged."
    if decision.intent in {"Feedback", "Substitution", "CycleReview"}:
        cards.append(proposal(db, request, decision.intent, decision.action_details))
        role = "Nutrition" if decision.intent == "Substitution" or (decision.action_details and decision.action_details.plan_kind == "Nutrition") else "Coach"
        if session.active_role != role:
            session.active_role = role
            cards.insert(0, {"type": "Handoff", "role": role})
        request.payload = request.payload | {"speaker": role}
    if decision.intent in {"ReviewDrafts", "ViewAllowance", "ViewUpdates"}:
        cards.append({"type": decision.intent})
    recent = list(
        db.scalars(
            select(Request)
            .where(
                Request.session_id == session.id,
                Request.id != request.id,
            )
            .order_by(Request.created_at.desc())
            .limit(5)
        )
    )
    declined = any(r.payload.get("action") == "KeepChat" for r in recent)
    if decision.offer_consultation != "None" and not declined:
        cards.append({"type": "Consultation", "role": decision.offer_consultation})
    planning = decision.intent in {"Training", "Nutrition", "Combined"}
    if planning:
        if decision.task_goal:
            request.payload = request.payload | {
                "original_content": request.payload["content"],
                "content": decision.task_goal,
            }
        if not member or member.status != "Accepted":
            publish(
                db,
                request,
                item,
                "I can help with that. First, confirm your profile and complete the studio review.",
                [{"type": "IntakeReview"}],
            )
            request.result = request.result | {
                "needs_input": True,
                "pending_goal": decision.intent,
                "pending_reason": "Admission",
                "prompt": "Complete your profile to continue this plan request.",
            }
            request.status = "AwaitingConfirmation"
            return
        # Lock the member to serialize conflicting plan proposals across conversations.
        conflict = db.scalar(
            select(Request)
            .where(
                Request.user_id == request.user_id,
                Request.id != request.id,
                Request.kind.in_(["Training", "Nutrition", "Combined", "CycleReview", "FoodSubstitution"]),
                Request.status.in_(["Queued", "Running", "Waiting", "AwaitingConfirmation"]),
            )
            .limit(1)
        )
        if conflict:
            from app.workflows import FlowAction, find, perform

            active_flow = find(db, conflict)
            if (
                active_flow
                and conflict.session_id == request.session_id
                and active_flow.stage in {"TrainingDiscussion", "MealDiscussion", "FinalReview"}
            ):
                domain = "training" if decision.intent in {"Training", "Combined"} else "meals"
                body = FlowAction(
                    action="revise_" + domain,
                    expected_revision=active_flow.revision,
                    operation_key=request.id,
                    feedback=request.payload["content"],
                )
                from fastapi import HTTPException

                from app.models import User

                try:
                    with db.begin_nested():
                        perform(db, active_flow, conflict, body, db.get(User, request.user_id))
                except HTTPException as exc:
                    publish(
                        db, request, item, str(exc.detail), [{"type": "Activity", "request_id": conflict.id}]
                    )
                    return
                publish(
                    db,
                    request,
                    item,
                    "Your feedback is part of the current draft. The specialist will revise it here.",
                )
                return
            publish(
                db,
                request,
                item,
                "Another plan update is still pending. Keep it, or cancel it in Activity before starting a replacement.",
                [{"type": "Activity", "request_id": conflict.id}],
            )
            return
        current = db.get(CurrentPlans, member.id)
        if decision.intent == "Training" and current and current.nutrition_id:
            # Generate training first; compatibility is assessed against the concrete draft.
            request.payload = request.payload | {
                "training_first": True,
                "keep_meals": decision.keep_meals or request.payload.get("keep_meals", False),
            }
            decision = decision.model_copy(update={"intent": "Combined"})
        group = db.get(MemberGroup, member.group_id)
        capabilities = {
            "Training": ["training"],
            "Nutrition": ["nutrition"],
            "Combined": ["training", "nutrition"],
        }[decision.intent]
        if any(group.configuration.get("capabilities", {}).get(c) is not True for c in capabilities):
            publish(
                db,
                request,
                item,
                "This planning service is not enabled for your membership. You can still view your current plans.",
            )
            return
        kind, role = decision.intent, "Nutrition" if decision.intent == "Nutrition" else "Coach"
        request.payload = request.payload | {
            "plan_baseline": {
                "version": current.version,
                "training_id": current.training_id,
                "nutrition_id": current.nutrition_id,
            }
            if current
            else None
        }
    elif decision.intent == "Intake":
        kind, role = "Intake", "Intake"
        session.active_role = "Intake"
    else:
        publish(db, request, item, text, cards)
        if decision.intent == "Consult":
            request.result = request.result | {"host_changed": True}
        if decision.intent == "Clarify":
            request.result = request.result | {"needs_input": True, "prompt": text}
        return
    request.kind, request.status = kind, "Queued"
    request.payload = request.payload | {"role": role, "routed": True}
    item.status, item.lease_until = "Completed", None
    db.add(
        WorkItem(
            request_id=request.id,
            role=role,
            subject_kind="Conversation",
            subject_id=session.id,
            input_versions={},
        )
    )
    if planning:
        from app.workflows import start

        start(db, request, role)
    # Informational tasks retain their current host.
    db.add(
        Message(
            session_id=session.id,
            sender=role,
            content="I’ll prepare a draft for your review. Your current plans will stay in use."
            if planning
            else "Front Desk will help prepare your details for confirmation.",
        )
    )
    event(db, request, "delegated", role=role, status="Queued")


def run_auto(claim):
    def prepare(state):
        with transaction() as db:
            item, request = lock_claim(db, claim)
            authorized = execution_context(db, item, request)
            version = resolve_version(db, item, authorized, AUTO_INSTRUCTION)
            member = db.scalar(select(Member).where(Member.user_id == request.user_id))
            assessment = db.scalar(
                select(Assessment)
                .where(Assessment.user_id == request.user_id)
                .order_by(Assessment.created_at.desc())
                .limit(1)
            )
            previous = list(
                db.scalars(
                    select(Request)
                    .where(
                        Request.session_id == request.session_id,
                        Request.id != request.id,
                        Request.payload["auto"].as_boolean().is_(True),
                    )
                    .order_by(Request.created_at.desc())
                    .limit(5)
                )
            )
            # Share only user goals and clarification prompts, never another role's raw records.
            history = [
                {
                    "user_goal": r.payload.get("content", ""),
                    "clarification": (r.result or {}).get("prompt"),
                    "declined_specialist": r.payload.get("action") == "KeepChat",
                }
                for r in reversed(previous)
            ]
            from app.workflows import discussion_context

            context = {
                "agent_version_id": version.id,
                "message": request.payload["content"],
                "history": history,
                "host": request.payload.get("speaker"),
                "admitted": bool(member and member.status == "Accepted"),
                "intake_status": assessment.status if assessment else None,
                "current_plans": plan_summary(db, request.user_id),
                "pending_member_actions": pending_actions(db, request.session_id),
                "draft_context": discussion_context(
                    db, session_id=request.session_id, user_id=request.user_id
                ),
                "pending_updates": [
                    {
                        "kind": r.kind,
                        "status": r.status,
                        "goal": r.payload.get("content"),
                        "question": (r.result or {}).get("prompt"),
                    }
                    for r in db.scalars(
                        select(Request)
                        .where(
                            Request.user_id == request.user_id,
                            Request.id != request.id,
                            Request.status.in_(["Queued", "Running", "Waiting", "AwaitingConfirmation"]),
                        )
                        .order_by(Request.created_at.desc())
                        .limit(5)
                    )
                ],
            }
            return {"context": context}

    def infer(state):
        output, call_id = generate(claim, AUTO_INSTRUCTION, state["context"], IntentReply)
        return {"candidate": {"decision": output.model_dump(), "call_id": call_id}}

    def validate(state):
        IntentReply.model_validate(state["candidate"]["decision"])
        return {"validation": {"passed": True}}

    def commit(state):
        with transaction() as db:
            existing = db.get(WorkItem, claim.work_id)
            if existing and existing.status == "Completed":
                return {"outcome": db.get(Request, claim.request_id).result}
            item, request = lock_claim(db, claim)
            execution_context(db, item, request)
            if plan_summary(db, request.user_id) != state["context"]["current_plans"]:
                from app.policy import PolicyDenied

                raise PolicyDenied("Your current plans changed while preparing this answer.")
            call = db.get(ModelCall, state["candidate"]["call_id"])
            if not call or call.status != "Settled" or call.request_id != request.id:
                raise ValueError("A settled response is required.")
            apply_intent(db, request, item, IntentReply.model_validate(state["candidate"]["decision"]))
            return {"outcome": request.result}

    with persistent_graph(prepare=prepare, generate=infer, validate=validate, commit=commit) as graph:
        config = graph_config(claim.work_id)
        snapshot = graph.get_state(config)
        if snapshot.values:
            return graph.invoke(None, config) if snapshot.next else snapshot.values
        return graph.invoke(
            {"request_id": claim.request_id, "work_id": claim.work_id, "role": claim.role}, config
        )


def submit_auto(db, session, body, user):
    from datetime import timedelta

    from fastapi import HTTPException

    from app.admission import fingerprint
    from app.models import IdempotencyRecord, now

    if user.role != "Member":
        raise HTTPException(403, "Use studio operations for owner requests.")
    role = session.active_role if session.active_role in {"Intake", "Coach", "Nutrition"} else "Intake"
    content = body.content
    deadline = now() + timedelta(hours=24)
    decision = None
    if not body.action:
        pending = db.scalars(
            select(Request).where(
                Request.session_id == session.id,
                Request.user_id == user.id,
                Request.status.in_(["Queued", "Running", "Waiting"]),
                Request.deadline > now(),
            )
        )
        for previous in pending:
            original = previous.payload.get("original_content", previous.payload.get("content", ""))
            if " ".join(original.split()) == " ".join(body.content.split()):
                result = {"request_id": previous.id, "status": previous.status, "already_pending": True}
                db.add(
                    IdempotencyRecord(
                        user_id=user.id,
                        operation=f"chat:{session.id}",
                        key=body.key,
                        fingerprint=fingerprint(
                            {
                                "content": body.content,
                                "purpose": body.purpose,
                                "operations_group_id": body.operations_group_id,
                                "action": body.action,
                                "source_request_id": body.source_request_id,
                            }
                        ),
                        result=result,
                    )
                )
                return result
    if body.action:
        direct = {
            "ViewPlans": ("ViewPlans", role),
            "ConsultCoach": ("Consult", "Coach"),
            "ConsultNutrition": ("Consult", "Nutrition"),
            "ConsultIntake": ("Consult", "Intake"),
            "Intake": ("Intake", "Intake"),
            "KeepChat": ("Answer", role),
        }
        if body.action in direct:
            intent, target = direct[body.action]
            decision = IntentReply(
                intent=intent,
                consultation_role=target,
                reply="Here are your current plans."
                if intent == "ViewPlans"
                else "We can continue here. Your plans have not changed.",
            )
            if body.action == "KeepChat" and body.source_request_id:
                source = db.scalar(
                    select(Request)
                    .where(
                        Request.id == body.source_request_id,
                        Request.user_id == user.id,
                        Request.session_id == session.id,
                    )
                    .with_for_update()
                )
                if (
                    not source
                    or source.status != "AwaitingConfirmation"
                    or (source.result or {}).get("pending_reason") != "Coordination"
                ):
                    raise HTTPException(409, "This choice is no longer available.")
                source.result = source.result | {"continued": True, "scope_declined": True}
                source.status = "Completed"
        elif body.action == "ReviseTrainingKeepMeals":
            source = db.scalar(
                select(Request)
                .where(
                    Request.id == body.source_request_id,
                    Request.user_id == user.id,
                    Request.session_id == session.id,
                )
                .with_for_update()
            )
            if (
                not source
                or source.deadline <= now()
                or source.status not in {"Completed", "AwaitingConfirmation"}
                or not source.payload.get("training_first")
                or not source.result.get("meal_changes")
                or source.result.get("continued")
            ):
                raise HTTPException(409, "This review is no longer available. Open the latest plan draft.")
            if source.payload.get("progressive"):
                raise HTTPException(409, "Use the current workflow card to revise this draft.")
            from app.models import PlanPair, PlanVersion

            pair = db.get(PlanPair, source.result.get("pair_id"))
            if not pair or pair.status != "Candidate":
                raise HTTPException(409, "This plan has already changed. Review your current routine.")
            from app.planning_context import verify_planning_baseline

            verify_planning_baseline(db, source, pair.member_id)
            training = db.get(PlanVersion, pair.training_id)
            content = (
                source.payload["content"]
                + "\nKeep my current meals unchanged. Rework the proposed training to fit them. "
                + "Explain any original training goal that cannot be met. Previous training draft: "
                + str(training.content)
            )
            deadline = source.deadline
            source.result = source.result | {"continued": True}
            source.status = "Cancelled"
            decision = IntentReply(
                intent="Training", reply="Coach will revise the draft while keeping your meals unchanged."
            )
        elif body.action in {"ContinuePlanning", "AllowCombined"}:
            source = db.scalar(
                select(Request)
                .where(
                    Request.id == body.source_request_id,
                    Request.user_id == user.id,
                    Request.session_id == session.id,
                )
                .with_for_update()
            )
            pending = (source.result or {}).get("pending_goal") if source else None
            if (
                not source
                or source.deadline <= now()
                or source.status not in {"Completed", "AwaitingConfirmation"}
                or pending not in {"Training", "Nutrition", "Combined"}
                or source.result.get("continued")
            ):
                raise HTTPException(
                    409, "This choice is no longer available. Please describe the update again."
                )
            content = source.payload["content"]
            deadline = source.deadline
            decision = IntentReply(intent=pending, reply="I’ll prepare the requested draft.")
            source.result = source.result | {"continued": True}
            source.status = "Completed"
    request = Request(
        user_id=user.id,
        session_id=session.id,
        kind="Auto",
        deadline=deadline,
        payload={
            "content": content,
            "role": role,
            "speaker": role,
            "auto": True,
            "action": body.action,
            "keep_meals": body.action == "ReviseTrainingKeepMeals",
        },
    )
    message = Message(session_id=session.id, sender="User", content=body.content)
    db.add_all([request, message])
    db.flush()
    item = WorkItem(
        request_id=request.id,
        role=role,
        subject_kind="Conversation",
        subject_id=session.id,
        input_versions={},
    )
    db.add(item)
    db.flush()
    if decision:
        apply_intent(db, request, item, decision)
    else:
        event(db, request, "queued", status="Queued", role=role)
    result = {"request_id": request.id, "message_id": message.id, "status": request.status}
    digest = fingerprint(
        {
            "content": body.content,
            "purpose": body.purpose,
            "operations_group_id": body.operations_group_id,
            "action": body.action,
            "source_request_id": body.source_request_id,
        }
    )
    db.add(
        IdempotencyRecord(
            user_id=user.id, operation=f"chat:{session.id}", key=body.key, fingerprint=digest, result=result
        )
    )
    return result


def resume_admission_goals():
    """Resume only an unexpired, uncancelled goal after confirmed admission."""
    from app.models import now

    resumed = 0
    with transaction() as db:
        ids = list(
            db.scalars(
                select(Request.id)
                .where(
                    Request.kind == "Auto",
                    Request.status == "AwaitingConfirmation",
                    Request.payload["auto"].as_boolean().is_(True),
                    Request.result["pending_reason"].as_string() == "Admission",
                    Request.deadline > now(),
                )
                .limit(50)
            )
        )
    for identifier in ids:
        with transaction() as db:
            # Match submission lock order: conversation before request.
            session_id = db.scalar(select(Request.session_id).where(Request.id == identifier))
            db.scalar(select(ChatSession).where(ChatSession.id == session_id).with_for_update())
            request = db.scalar(select(Request).where(Request.id == identifier).with_for_update())
            if (
                not request
                or request.status != "AwaitingConfirmation"
                or request.deadline <= now()
                or (request.result or {}).get("continued")
            ):
                continue
            member = db.scalar(select(Member).where(Member.user_id == request.user_id))
            if not member or member.status != "Accepted":
                continue
            item = db.scalar(
                select(WorkItem)
                .where(WorkItem.request_id == request.id)
                .order_by(WorkItem.created_at)
                .limit(1)
            )
            goal = (request.result or {}).get("pending_goal")
            if item and goal in {"Training", "Nutrition", "Combined"}:
                request.result = request.result | {"continued": True}
                apply_intent(
                    db,
                    request,
                    item,
                    IntentReply(
                        intent=goal,
                        reply="Your profile is accepted. I’ll continue your earlier plan request.",
                    ),
                )
                resumed += 1
    return resumed
