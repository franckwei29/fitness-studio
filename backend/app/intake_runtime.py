"""Intake graph handlers with atomic proposal publication and replay protection."""

from sqlalchemy import select

from app.agent_contracts import INTAKE_INSTRUCTION, IntakeProposal
from app.agent_graph import graph_config, persistent_graph
from app.agent_versions import resolve_version
from app.database import transaction
from app.model_gateway import generate
from app.models import Assessment, Member, Message, ModelCall, Request, User, WorkItem
from app.policy import execution_context
from app.rules import assess
from app.work_queue import event, lock_claim


def run_intake(claim):
    def prepare(state):
        with transaction() as db:
            item, request = lock_claim(db, claim)
            authorized = execution_context(db, item, request)
            if item.role != "Intake" or not request.session_id:
                raise ValueError("This executor requires an intake conversation.")
            version = resolve_version(db, item, authorized, INTAKE_INSTRUCTION)
            assessment = db.scalar(
                select(Assessment)
                .where(
                    Assessment.user_id == request.user_id,
                    Assessment.status.in_(["Collecting", "NeedsInformation", "PendingOwnerReview"]),
                )
                .order_by(Assessment.created_at.desc())
                .limit(1)
            )
            messages = list(
                db.scalars(
                    select(Message)
                    .where(Message.session_id == request.session_id)
                    .order_by(Message.created_at.desc(), Message.id.desc())
                    .limit(12)
                )
            )
            context = {
                "agent_version_id": version.id,
                "confirmed_profile": authorized["profile"],
                "proposed_facts": assessment.proposed_facts if assessment else {},
                "messages": [
                    {"speaker": message.sender, "text": message.content} for message in reversed(messages)
                ],
                "current_message": request.payload["content"],
            }
            return {"context": context, "input_versions": authorized["versions"]}

    def infer(state):
        proposal, call_id = generate(claim, INTAKE_INSTRUCTION, state["context"], IntakeProposal)
        return {"candidate": {"proposal": proposal.model_dump(), "call_id": call_id}}

    def validate(state):
        IntakeProposal.model_validate(state["candidate"]["proposal"])
        return {"validation": {"passed": True}, "requires_confirmation": False}

    def commit(state):
        proposal = IntakeProposal.model_validate(state["candidate"]["proposal"])
        with transaction() as db:
            # Publication and task completion share one transaction. If a crash
            # happens before checkpointing, replay returns the committed result.
            current = db.scalar(select(WorkItem).where(WorkItem.id == claim.work_id).with_for_update())
            if current and current.status == "Completed":
                request = db.get(Request, current.request_id)
                return {"outcome": request.result}
            item, request = lock_claim(db, claim)
            execution_context(db, item, request)
            call = db.get(ModelCall, state["candidate"]["call_id"])
            if not call or call.request_id != request.id or call.status != "Settled":
                raise ValueError("A settled model result is required before publication.")
            db.scalar(select(User).where(User.id == request.user_id).with_for_update())
            assessment = db.scalar(
                select(Assessment)
                .where(
                    Assessment.user_id == request.user_id,
                    Assessment.status.in_(["Collecting", "NeedsInformation", "PendingOwnerReview"]),
                )
                .order_by(Assessment.created_at.desc())
                .limit(1)
                .with_for_update()
            )
            if not assessment:
                member = db.scalar(select(Member).where(Member.user_id == request.user_id))
                assessment = Assessment(user_id=request.user_id, member_id=member.id if member else None)
                db.add(assessment)
                db.flush()
            facts = proposal.proposed_facts.model_dump(exclude_none=True)
            assessment.proposed_facts = assessment.proposed_facts | facts
            if proposal.contradictions:
                assessment.proposed_facts = assessment.proposed_facts | {"conflicting_information": True}
            cards = [
                {
                    "type": "IntakeReview",
                    "assessment_id": assessment.id,
                    "version": assessment.version,
                    "facts": assessment.proposed_facts,
                    "missing_fields": assess(assessment.proposed_facts).missing,
                    "contradictions": proposal.contradictions,
                }
            ]
            message = Message(
                session_id=request.session_id, sender="Intake", content=proposal.reply, cards=cards
            )
            db.add(message)
            db.flush()
            result = {
                "message_id": message.id,
                "assessment_id": assessment.id,
                "call_id": call.id,
                "status": "Completed",
            }
            request.result = result
            item.status = request.status = "Completed"
            item.lease_until = None
            event(db, request, "completed", **result)
            return {"outcome": result}

    with persistent_graph(prepare=prepare, generate=infer, validate=validate, commit=commit) as graph:
        config = graph_config(claim.work_id)
        snapshot = graph.get_state(config)
        if snapshot.values:
            if snapshot.next:
                return graph.invoke(None, config)
            return snapshot.values
        return graph.invoke(
            {"request_id": claim.request_id, "work_id": claim.work_id, "role": claim.role}, config
        )
