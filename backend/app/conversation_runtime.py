"""Role-scoped conversational replies and read-only operational explanations."""

from sqlalchemy import func, select

from app.agent_contracts import COMMON_INSTRUCTION, TECHNICAL_INSTRUCTION, ChatReply
from app.agent_graph import graph_config, persistent_graph
from app.agent_versions import resolve_version
from app.database import transaction
from app.model_gateway import generate
from app.models import (
    BudgetAccount,
    CurrentPlans,
    Member,
    MemberGroup,
    Message,
    ModelCall,
    PlanVersion,
    Request,
    WorkItem,
)
from app.policy import PolicyDenied, execution_context
from app.work_queue import event, lock_claim


def current_plan_context(db, authorized, role):
    from app.activation import plan_fingerprint
    from app.plans import availability
    from app.policy import PolicyDenied

    member = db.get(Member, authorized["member_id"])
    group = db.get(MemberGroup, authorized["group_id"])
    current = db.get(CurrentPlans, member.id)
    identifier = (current.training_id if role == "Coach" else current.nutrition_id) if current else None
    plan = db.get(PlanVersion, identifier) if identifier else None
    if not plan:
        return {"available": False, "reason": "No current plan has been activated."}
    expected_kind = "Training" if role == "Coach" else "Nutrition"
    if (
        plan.member_id != member.id
        or plan.kind != expected_kind
        or plan.fingerprint != plan_fingerprint(plan)
    ):
        raise PolicyDenied("The current plan does not match its recorded ownership or contents.")
    state = availability(member, group, plan)
    if not state["usable"]:
        return {"available": False, "reason": state["reason"]}
    return {"available": True, "id": plan.id, "version": plan.version, "content": plan.content}


def conversation_history(db, request, role):
    """Do not forward another role's intake or plan messages across a handoff."""
    previous = list(
        db.scalars(
            select(Request)
            .where(
                Request.session_id == request.session_id,
                Request.user_id == request.user_id,
                Request.status == "Completed",
                Request.kind.in_(["Chat", "Operations"]),
                Request.payload["role"].as_string() == role,
            )
            .order_by(Request.created_at.desc(), Request.id.desc())
            .limit(5)
        )
    )
    history = []
    for earlier in reversed(previous):
        message_id = (earlier.result or {}).get("message_id")
        reply = db.get(Message, message_id) if message_id else None
        if reply and reply.session_id == request.session_id and reply.sender == role:
            history.extend(
                [
                    {"speaker": "User", "text": earlier.payload["content"]},
                    {"speaker": role, "text": reply.content},
                ]
            )
    return history


def run_conversation(claim):
    instruction = (
        TECHNICAL_INSTRUCTION
        if claim.role == "TechnicalManager"
        else COMMON_INSTRUCTION + f"\nYour role is {claim.role}. Answer general questions within your role. "
        "For a new or revised plan, explain that the user can choose a planning request. "
        "Do not invent a personalised plan in an ordinary chat reply."
    )

    def prepare(state):
        with transaction() as db:
            item, request = lock_claim(db, claim)
            authorized = execution_context(db, item, request)
            context = {
                "profile": authorized["profile"],
                "message": request.payload["content"],
                "history": conversation_history(db, request, claim.role),
            }
            if claim.role in {"Coach", "Nutrition"}:
                from app.coach_runtime import COACH_INSTRUCTION
                from app.nutrition_runtime import INSTRUCTION

                base = COACH_INSTRUCTION if claim.role == "Coach" else INSTRUCTION
                version = resolve_version(db, item, authorized, base)
                context["agent_version_id"] = version.id
                context["current_plan"] = current_plan_context(db, authorized, claim.role)
            if claim.role == "TechnicalManager":
                version = resolve_version(db, item, authorized, TECHNICAL_INSTRUCTION)
                context["agent_version_id"] = version.id
                counts = dict(db.execute(select(Request.status, func.count()).group_by(Request.status)).all())
                budgets = list(db.scalars(select(BudgetAccount).where(BudgetAccount.scope_key == "global")))
                if request.payload.get("incident_id"):
                    from app.incident_context import incident_context

                    context["incident"] = incident_context(db, request.payload["incident_id"])
                context["operations"] = {
                    "request_counts": counts,
                    "global_budget": [
                        {
                            "limit_micros": row.limit_micros,
                            "reserved_micros": row.reserved_micros,
                            "spent_micros": row.spent_micros,
                        }
                        for row in budgets
                    ],
                    "unknown_calls": db.scalar(
                        select(func.count()).select_from(ModelCall).where(ModelCall.status == "UsageUnknown")
                    ),
                }
            return {"context": context, "input_versions": authorized["versions"]}

    def infer(state):
        output, call_id = generate(claim, instruction, state["context"], ChatReply)
        return {"candidate": {"reply": output.reply, "call_id": call_id}}

    def validate(state):
        ChatReply(reply=state["candidate"]["reply"])
        return {"validation": {"passed": True}, "requires_confirmation": False}

    def commit(state):
        with transaction() as db:
            existing = db.scalar(select(WorkItem).where(WorkItem.id == claim.work_id).with_for_update())
            if existing and existing.status == "Completed":
                return {"outcome": db.get(Request, existing.request_id).result}
            item, request = lock_claim(db, claim)
            authorized = execution_context(db, item, request)
            if (
                claim.role in {"Coach", "Nutrition"}
                and current_plan_context(db, authorized, claim.role) != state["context"]["current_plan"]
            ):
                raise PolicyDenied(
                    "The current plan changed while preparing the reply. Ask again about the latest plan."
                )
            call = db.get(ModelCall, state["candidate"]["call_id"])
            if not call or call.request_id != request.id or call.status != "Settled":
                raise ValueError("A settled model result is required.")
            message = Message(
                session_id=request.session_id, sender=item.role, content=state["candidate"]["reply"]
            )
            db.add(message)
            db.flush()
            result = {"status": "Completed", "message_id": message.id, "call_id": call.id}
            request.result = result
            item.status = request.status = "Completed"
            item.lease_until = None
            event(db, request, "completed", **result)
            return {"outcome": result}

    with persistent_graph(prepare=prepare, generate=infer, validate=validate, commit=commit) as graph:
        config = graph_config(claim.work_id)
        snapshot = graph.get_state(config)
        if snapshot.values:
            return graph.invoke(None, config) if snapshot.next else snapshot.values
        return graph.invoke(
            {"request_id": claim.request_id, "work_id": claim.work_id, "role": claim.role}, config
        )
