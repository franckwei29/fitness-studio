"""Member questions use the configured role version and only its usable plan."""

from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.activation import plan_fingerprint
from app.admission import fingerprint
from app.agent_contracts import ChatReply
from app.database import transaction
from app.main import app
from app.models import (
    AgentVersion,
    CurrentPlans,
    Member,
    MemberGroup,
    Message,
    ModelCall,
    PlanVersion,
    ProfileSnapshot,
    Request,
    WorkItem,
    now,
)
from app.settings import get_settings
from app.version_admin import base_instruction
from app.worker import run_once


@pytest.mark.parametrize(
    "role,purpose,stale",
    [("Coach", "Chat", False), ("Nutrition", "NutritionChat", False), ("Coach", "Chat", True)],
)
def test_member_questions_use_role_version_and_current_plan(monkeypatch, role, purpose, stale):
    contexts = []

    def synthetic_generate(claim, instruction, context, output_type):
        contexts.append(context)
        assert output_type is ChatReply
        with transaction() as db:
            request = db.get(Request, claim.request_id)
            call = ModelCall(
                request_id=request.id,
                user_id=request.user_id,
                role=claim.role,
                model="synthetic-test",
                status="Settled",
                reserved_micros=0,
                actual_micros=0,
                price_version="synthetic-test",
            )
            db.add(call)
            db.flush()
            return ChatReply(reply="Review the current plan before requesting changes."), call.id

    monkeypatch.setattr("app.conversation_runtime.generate", synthetic_generate)
    with TestClient(app) as client:
        auth = client.post(
            "/api/auth/register",
            json={
                "email": f"question-{uuid4()}@example.test",
                "password": "Integration-test-123!",
                "display_name": "Question Member",
            },
        ).json()
        client.headers["x-csrf-token"] = auth["csrf_token"]
        user_id = client.get("/api/auth/me").json()["user"]["id"]
        with transaction() as db:
            values = {
                "role": role,
                "model": get_settings().gemini_model,
                "prompt": base_instruction(role) + f"\nTest {uuid4()}",
            }
            version = AgentVersion(label="Conversation test", fingerprint=fingerprint(values), **values)
            db.add(version)
            db.flush()
            group = MemberGroup(
                name=f"Question-{uuid4()}",
                configuration={
                    "capabilities": {"training": True, "nutrition": True},
                    "agent_versions": {role: version.id},
                },
            )
            db.add(group)
            db.flush()
            member = Member(user_id=user_id, group_id=group.id)
            db.add(member)
            db.flush()
            db.add(
                ProfileSnapshot(
                    member_id=member.id,
                    version=1,
                    facts={
                        "goal": "Build consistency",
                        "dietary_restrictions": ["Vegetarian"],
                        "equipment": ["Wall"],
                    },
                    sources={},
                    confirmed_by=user_id,
                )
            )
            current = CurrentPlans(member_id=member.id, version=1)
            db.add(current)
            source_request = Request(
                user_id=user_id, kind="Combined", status="Completed", deadline=now() + timedelta(hours=1)
            )
            db.add(source_request)
            db.flush()
            for kind in ("Training", "Nutrition"):
                plan = PlanVersion(
                    member_id=member.id,
                    request_id=source_request.id,
                    kind=kind,
                    version=1,
                    status="Active",
                    profile_version=2 if stale else 1,
                    config_version=1,
                    agent_version_id=version.id,
                    content={"title": f"Current {kind}"},
                    rationale="Synthetic context test",
                    fingerprint="pending",
                )
                db.add(plan)
                db.flush()
                plan.fingerprint = plan_fingerprint(plan)
                if kind == "Training":
                    current.training_id = plan.id
                else:
                    current.nutrition_id = plan.id
            version_id = version.id
        session = client.post("/api/conversations").json()
        with transaction() as db:
            for prior_role in ("Intake", role):
                reply = Message(
                    session_id=session["id"], sender=prior_role, content=f"Previous {prior_role} answer"
                )
                db.add(reply)
                db.flush()
                db.add(
                    Request(
                        user_id=user_id,
                        session_id=session["id"],
                        kind="Chat",
                        status="Completed",
                        deadline=now() + timedelta(hours=1),
                        payload={"role": prior_role, "content": f"Previous {prior_role} question"},
                        result={"message_id": reply.id},
                    )
                )
        result = client.post(
            f"/api/conversations/{session['id']}/messages",
            json={"key": str(uuid4()), "purpose": purpose, "content": "Explain my current plan."},
        )
        assert result.status_code == 202
        request_id = result.json()["request_id"]
        assert run_once(request_id)
        assert len(contexts) == 1
        context = contexts[0]
        assert context["agent_version_id"] == version_id
        assert context["history"] == [
            {"speaker": "User", "text": f"Previous {role} question"},
            {"speaker": role, "text": f"Previous {role} answer"},
        ]
        assert context["current_plan"]["available"] is not stale
        if stale:
            assert "content" not in context["current_plan"]
        else:
            assert context["current_plan"]["content"]["title"] == (
                "Current Training" if role == "Coach" else "Current Nutrition"
            )
        assert ("dietary_restrictions" in context["profile"]) is (role == "Nutrition")
        assert ("equipment" in context["profile"]) is (role == "Coach")
        with transaction() as db:
            work = db.scalar(select(WorkItem).where(WorkItem.request_id == request_id))
            assert work.input_versions["agent_version"] == version_id
            assert work.status == "Completed"
            assert db.get(Request, request_id).kind == "Chat"
