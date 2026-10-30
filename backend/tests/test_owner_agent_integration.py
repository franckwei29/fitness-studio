"""Real SQL action fences and bounded synthetic-model orchestration (not live AI evidence)."""

import json
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.admission import fingerprint
from app.bootstrap import bootstrap
from app.database import transaction
from app.main import app
from app.models import ChatSession, MemberGroup, Message, ModelCall, Request, User, now
from app.owner_agent import OwnerTurn
from app.owner_tools import ToolInput
from app.settings import get_settings
from app.worker import run_once


@pytest.fixture
def clients():
    bootstrap()
    with TestClient(app) as owner, TestClient(app) as member:
        settings = get_settings()
        response = owner.post(
            "/api/auth/login",
            json={"email": settings.owner_email, "password": settings.owner_password.get_secret_value()},
        )
        owner.headers["x-csrf-token"] = response.json()["csrf_token"]
        response = member.post(
            "/api/auth/register",
            json={
                "email": f"owner-agent-{uuid4()}@example.test",
                "password": "Integration-test-123!",
                "display_name": "Agent test member",
            },
        )
        member.headers["x-csrf-token"] = response.json()["csrf_token"]
        yield owner, member


def task(owner, actions, session_id=None):
    session_id = session_id or owner.post("/api/conversations").json()["id"]
    with transaction() as db:
        session = db.get(ChatSession, session_id)
        row = Request(
            user_id=session.user_id,
            session_id=session_id,
            kind="Operations",
            status="Completed",
            deadline=now() + timedelta(hours=1),
            payload={"owner_agent": True, "content": "Prepare this change"},
        )
        db.add(row)
        db.flush()
        digest = fingerprint({"request_id": row.id, "actions": actions})
        row.result = {
            "owner_task": {
                "goal": "Test proposal",
                "status": "Ready",
                "fingerprint": digest,
                "actions": actions,
                "changes": [],
                "evidence": [],
                "remaining": [],
                "receipts": [],
            }
        }
        db.add(
            Message(
                session_id=session_id,
                sender="TechnicalManager",
                content="Review the exact proposal.",
                cards=[{"type": "OwnerTask", "request_id": row.id}],
            )
        )
        return row.id, digest, session_id


def candidate(label=None):
    return {
        "name": "candidate.save",
        "target": "",
        "arguments": json.dumps(
            {
                "role": "Coach",
                "label": label or str(uuid4()),
                "model": get_settings().gemini_model,
                "instructions": "Keep explanations concise. Preserve all validation requirements.",
            }
        ),
    }


def test_owner_action_executes_once_and_denies_member(clients):
    owner, member = clients
    identifier, digest, session = task(owner, [candidate()])
    assert member.get(f"/api/owner/tasks/{identifier}").status_code == 403
    payload = {"fingerprint": digest, "decision": "Execute"}
    assert member.post(f"/api/owner/tasks/{identifier}/decision", json=payload).status_code == 403
    first = owner.post(f"/api/owner/tasks/{identifier}/decision", json=payload)
    assert first.status_code == 200, first.text
    second = owner.post(f"/api/owner/tasks/{identifier}/decision", json=payload)
    assert second.json() == first.json()
    assert first.json()["status"] == "Executed"
    history = owner.get(f"/api/conversations/{session}").json()
    assert len(history["messages"]) == 2
    assert first.json()["receipts"][0]["target"]["page"] == "versions"


def test_action_batch_rolls_back_domain_write_when_later_step_fails(clients):
    from app.models import AgentVersion

    owner, _ = clients
    label = "RollbackBatch-" + str(uuid4())
    actions = [candidate(label), {"name": "configuration.execute", "target": str(uuid4()), "arguments": "{}"}]
    identifier, digest, _ = task(owner, actions)
    result = owner.post(
        f"/api/owner/tasks/{identifier}/decision", json={"fingerprint": digest, "decision": "Execute"}
    )
    assert result.status_code == 404, result.text
    with transaction() as db:
        assert db.scalar(select(AgentVersion).where(AgentVersion.label == label)) is None
    assert owner.get(f"/api/owner/tasks/{identifier}").json()["status"] == "Ready"


def test_stale_configuration_preview_cannot_execute(clients):
    owner, _ = clients
    from app.bootstrap import DEFAULT_CONFIGURATION

    with transaction() as db:
        record = MemberGroup(name="Agent stale test " + str(uuid4()), configuration=DEFAULT_CONFIGURATION)
        db.add(record)
        db.flush()
        group = {"id": record.id, "version": record.version, "configuration": record.configuration}
    action = {
        "name": "configuration.propose",
        "target": group["id"],
        "arguments": json.dumps(
            {
                "expected_version": group["version"],
                "configuration": group["configuration"],
                "reason": "Keep current reviewed settings for testing.",
            }
        ),
    }
    identifier, digest, _ = task(owner, [action])
    with transaction() as db:
        db.get(MemberGroup, group["id"]).version += 1
    response = owner.post(
        f"/api/owner/tasks/{identifier}/decision", json={"fingerprint": digest, "decision": "Execute"}
    )
    assert response.status_code == 409, response.text


def test_dismissed_and_superseded_previews_do_not_run(clients):
    owner, _ = clients
    identifier, digest, _ = task(owner, [candidate()])
    path = f"/api/owner/tasks/{identifier}/decision"
    assert owner.post(path, json={"fingerprint": digest, "decision": "Dismiss"}).status_code == 200
    assert owner.post(path, json={"fingerprint": digest, "decision": "Execute"}).status_code == 409
    assert owner.post(path, json={"fingerprint": "wrong", "decision": "Dismiss"}).status_code == 409


def test_owner_agent_reads_evidence_and_revises_without_writing(monkeypatch, clients):
    owner, _ = clients
    old, digest, session = task(owner, [candidate()])
    monkeypatch.setattr(get_settings(), "model_calls_enabled", True)
    contexts = []

    def synthetic(claim, instruction, context, output_type, max_output_tokens):
        contexts.append(json.loads(json.dumps(context)))
        if len(contexts) == 1:
            result = OwnerTurn(
                reply="Checking supported models.",
                goal="Shorter Coach explanations",
                reads=[ToolInput(name="versions")],
            )
        else:
            result = OwnerTurn(
                reply="Here is the revised draft. Nothing has been saved.",
                goal="Shorter Coach explanations",
                actions=[ToolInput(**candidate())],
                revises_request_id=old,
                changes=["Keep the same model and validation requirements."],
            )
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
            return result, call.id

    monkeypatch.setattr("app.owner_agent.generate", synthetic)
    response = owner.post(
        f"/api/conversations/{session}/messages",
        json={
            "key": str(uuid4()),
            "purpose": "Operations",
            "owner_agent": True,
            "content": "Shorten the introduction only. Keep the existing constraints.",
        },
    )
    assert response.status_code == 202, response.text
    assert run_once(response.json()["request_id"])
    assert contexts[0]["profile"] == {}
    assert contexts[1]["evidence"][0]["tool"]["name"] == "versions"
    assert contexts[0]["history"][0]["request_id"] == old
    current = owner.get(f"/api/owner/tasks/{response.json()['request_id']}").json()
    assert current["status"] == "Ready", current
    assert current["receipts"] == []
    assert owner.get(f"/api/owner/tasks/{old}").json()["status"] == "Superseded"


def test_read_approval_excludes_health_context(clients):
    from app.models import Approval
    from app.owner_tools import read_tool

    owner, _ = clients
    with transaction() as db:
        user = db.scalar(select(User).where(User.role == "Owner"))
        approval = Approval(
            subject_kind="Assessment",
            subject_id=str(uuid4()),
            subject_version=1,
            fingerprint="a" * 64,
            scope={},
            context={"facts": {"goal": "PRIVATE_HEALTH_SENTINEL"}},
            expires_at=now() + timedelta(hours=1),
        )
        db.add(approval)
        db.flush()
        identifier = approval.id
    result = read_tool(ToolInput(name="approval", target=identifier), user)
    assert "PRIVATE_HEALTH_SENTINEL" not in json.dumps(result)
    assert "context" not in result


def test_budget_preview_tolerates_usage_but_rejects_changed_limit(clients):
    from app.budget_admin import budget_view
    from app.models import BudgetAccount
    from app.owner_tools import budget_baseline

    owner, _ = clients
    with transaction() as db:
        row = BudgetAccount(scope_key="role:TechnicalManager", limit_micros=1234567)
        existing = db.scalar(select(BudgetAccount).where(BudgetAccount.scope_key == row.scope_key))
        if existing:
            row = existing
        else:
            db.add(row)
            db.flush()
        before = budget_view(row)
    action = ToolInput(
        name="budget.change",
        arguments=json.dumps(
            {
                "scope_key": before["scope_key"],
                "limit_micros": before["limit_micros"] + 100,
                "expected_fingerprint": before["fingerprint"],
                "reason": "Reviewed test increase",
            }
        ),
    )
    baseline = budget_baseline(action, [{"tool": {"name": "budgets"}, "result": [before]}])
    identifier, digest, _ = task(owner, [action.model_dump()])
    with transaction() as db:
        request = db.get(Request, identifier)
        request.result = {"owner_task": request.result["owner_task"] | {"baselines": [baseline]}}
        db.get(BudgetAccount, before["id"]).spent_micros += 1
    response = owner.post(
        f"/api/owner/tasks/{identifier}/decision", json={"fingerprint": digest, "decision": "Execute"}
    )
    assert response.status_code == 200, response.text
    second, digest, _ = task(owner, [action.model_dump()])
    with transaction() as db:
        request = db.get(Request, second)
        request.result = {"owner_task": request.result["owner_task"] | {"baselines": [baseline]}}
    response = owner.post(
        f"/api/owner/tasks/{second}/decision", json={"fingerprint": digest, "decision": "Execute"}
    )
    assert response.status_code == 409, response.text
