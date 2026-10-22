"""Proposal publication uses a synthetic model result, real MySQL, and Redis."""

from datetime import timedelta
from uuid import uuid4

from sqlalchemy import func, select

from app.agent_contracts import IntakeFacts, IntakeProposal
from app.database import transaction
from app.intake_runtime import run_intake
from app.models import Assessment, ChatSession, Member, Message, ModelCall, Request, User, WorkItem, now
from app.work_queue import claim_next


def test_proposal_does_not_admit_member_and_replay_does_not_duplicate(monkeypatch):
    with transaction() as db:
        user = User(
            email=f"intake-runtime-{uuid4()}@example.test", display_name="Intake Test", password_hash="Unused"
        )
        db.add(user)
        db.flush()
        session = ChatSession(user_id=user.id)
        db.add(session)
        db.flush()
        request = Request(
            user_id=user.id,
            session_id=session.id,
            kind="Chat",
            deadline=now() + timedelta(minutes=10),
            payload={"content": "我今年28岁"},
        )
        db.add(request)
        db.flush()
        item = WorkItem(
            request_id=request.id, role="Intake", subject_kind="Conversation", subject_id=session.id
        )
        db.add(item)
        db.flush()
        request_id, user_id, session_id = request.id, user.id, session.id
    calls = []

    def synthetic_generate(claim, instruction, context, output_type):
        calls.append(claim.work_id)
        with transaction() as db:
            call = ModelCall(
                request_id=request_id,
                user_id=user_id,
                role="Intake",
                model="synthetic-test",
                status="Settled",
                reserved_micros=0,
                actual_micros=0,
                price_version="synthetic-test",
            )
            db.add(call)
            db.flush()
            call_id = call.id
        return IntakeProposal(
            reply="What is your main fitness goal?",
            proposed_facts=IntakeFacts(age=28),
            missing_fields=["goal"],
            contradictions=[],
        ), call_id

    monkeypatch.setattr("app.intake_runtime.generate", synthetic_generate)
    claim = claim_next(request_id=request_id)
    result = run_intake(claim)
    assert result["outcome"]["status"] == "Completed"
    assert run_intake(claim)["outcome"] == result["outcome"]
    assert len(calls) == 1
    with transaction() as db:
        assessment = db.scalar(select(Assessment).where(Assessment.user_id == user_id))
        assert assessment.proposed_facts == {"age": 28}
        assert assessment.confirmed_facts == {}
        assert db.scalar(select(Member).where(Member.user_id == user_id)) is None
        assert (
            db.scalar(select(func.count()).select_from(Message).where(Message.session_id == session_id)) == 1
        )
        assert db.get(Request, request_id).status == "Completed"
