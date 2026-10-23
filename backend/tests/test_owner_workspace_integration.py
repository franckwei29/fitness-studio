"""Authenticated owner discovery, local cards, and durable action receipts."""

import json
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.bootstrap import bootstrap
from app.database import transaction
from app.main import app
from app.models import MemberGroup, ModelCall, Request, WorkItem, now
from app.rules import EXCLUSIONS
from app.settings import get_settings


@pytest.fixture
def workspace_clients():
    bootstrap()
    with TestClient(app) as owner, TestClient(app) as member:
        settings = get_settings()
        result = owner.post(
            "/api/auth/login",
            json={"email": settings.owner_email, "password": settings.owner_password.get_secret_value()},
        )
        assert result.status_code == 200
        owner.headers["x-csrf-token"] = result.json()["csrf_token"]
        result = member.post(
            "/api/auth/register",
            json={
                "email": f"workspace-{uuid4()}@example.test",
                "password": "Integration-test-123!",
                "display_name": f"WorkspacePerson{uuid4().hex}",
            },
        )
        assert result.status_code == 201
        member.headers["x-csrf-token"] = result.json()["csrf_token"]
        yield owner, member


def pending_application(member):
    assessment = member.post("/api/assessments").json()
    facts = dict(
        age=28,
        goal="Private application evidence",
        experience="Beginner",
        available_days=3,
        session_minutes=45,
        equipment=["Dumbbells"],
        dietary_restrictions=[],
        **{key: False for key in EXCLUSIONS},
    ) | {"resolved_past_injury": True}
    result = member.post(
        f"/api/assessments/{assessment['id']}/confirm",
        json={"expected_version": assessment["version"], "facts": facts},
    )
    assert result.status_code == 200
    assert result.json()["status"] == "PendingOwnerReview"
    return result.json()


def approval_for(owner, assessment):
    return next(
        row for row in owner.get("/api/owner/approvals").json()
        if row["subject_id"] == assessment["id"]
    )


def model_count():
    with transaction() as db:
        return db.scalar(select(func.count()).select_from(ModelCall))


def submit_owner(owner, conversation, content):
    body = {"key": str(uuid4()), "purpose": "Operations", "content": content}
    response = owner.post(f"/api/conversations/{conversation['id']}/messages", json=body)
    assert response.status_code == 202, response.text
    return body, response.json()


def test_owner_workspace_and_receipts_require_owner_access(workspace_clients):
    owner, member = workspace_clients
    conversation = owner.post("/api/conversations").json()
    target = {"page": "members", "id": str(uuid4())}
    assert member.get("/api/owner/workspace?area=attention").status_code == 403
    assert member.post(
        f"/api/owner/conversations/{conversation['id']}/receipts",
        json={"key": str(uuid4()), "target": target},
    ).status_code == 403
    with TestClient(app) as anonymous:
        assert anonymous.get("/api/owner/workspace?area=attention").status_code == 401


@pytest.mark.parametrize("content", ["What needs my attention?", "现在有什么需要我处理？"])
def test_bilingual_attention_is_local_and_persists_in_one_conversation(workspace_clients, content):
    owner, member = workspace_clients
    assessment = pending_application(member)
    approval = approval_for(owner, assessment)
    conversation = owner.post("/api/conversations").json()
    before_calls = model_count()
    body, result = submit_owner(owner, conversation, content)
    assert owner.post(
        f"/api/conversations/{conversation['id']}/messages", json=body
    ).json() == result
    history = owner.get(f"/api/conversations/{conversation['id']}").json()
    assert len(history["messages"]) == 2
    assert history["messages"][0]["content"] == content
    cards = history["messages"][1]["cards"]
    assert any(card["type"] == "OwnerWorkspace" and card["area"] == "attention" for card in cards)
    assert "Private application evidence" not in json.dumps(history)
    with transaction() as db:
        request = db.get(Request, result["request_id"])
        assert request.status == "Completed"
        assert db.scalar(select(func.count()).select_from(WorkItem).where(
            WorkItem.request_id == request.id
        )) == 0
    projection = owner.get("/api/owner/workspace", params={"area": "approvals", "target_id": approval["id"]})
    assert projection.status_code == 200, projection.text
    assert approval["id"] in projection.text
    assert "Private application evidence" not in projection.text
    assert model_count() == before_calls


def test_receipt_cannot_claim_an_unperformed_approval_and_replays_once(workspace_clients):
    owner, member = workspace_clients
    assessment = pending_application(member)
    approval = approval_for(owner, assessment)
    conversation = owner.post("/api/conversations").json()
    path = f"/api/owner/conversations/{conversation['id']}/receipts"
    payload = {"key": str(uuid4()), "target": {"page": "approvals", "id": approval["id"]}}
    response = owner.post(path, json=payload)
    assert response.status_code in {404, 409}, response.text
    assert owner.get(f"/api/conversations/{conversation['id']}").json()["messages"] == []
    decided = owner.post(
        f"/api/owner/approvals/{approval['id']}/decisions",
        json={"decision": "Approved", "fingerprint": approval["fingerprint"], "comment": "Reviewed current application."},
    )
    assert decided.status_code == 200
    response = owner.post(path, json=payload)
    assert response.status_code in {200, 201}, response.text
    first = response.json()
    assert owner.post(path, json=payload).json() == first
    assert owner.post(path, json=payload | {"key": str(uuid4())}).status_code in {200, 201}
    history = owner.get(f"/api/conversations/{conversation['id']}").json()
    receipts = [card for row in history["messages"] for card in row["cards"] if card["type"] == "OwnerReceipt"]
    assert len(receipts) == 1
    assert member.get("/api/me/profile").json()["member"]["status"] == "Accepted"


def test_budget_stale_write_preserves_saved_value_and_receipt(workspace_clients):
    owner, _ = workspace_clients
    conversation = owner.post("/api/conversations").json()
    with transaction() as db:
        group = MemberGroup(name=f"Workspace-budget-{uuid4()}", configuration={})
        db.add(group)
        db.flush()
        scope = f"group:{group.id}"
    initial = owner.post("/api/owner/budgets", json={"scope_key": scope, "limit_micros": 1000000, "reason": "Set test allowance"})
    assert initial.status_code == 200
    old = initial.json()
    body = {"scope_key": scope, "limit_micros": 2000000, "expected_fingerprint": old["fingerprint"], "reason": "Confirmed test adjustment"}
    assert owner.post("/api/owner/budgets", json=body).status_code == 200
    assert owner.post("/api/owner/budgets", json=body | {"limit_micros": 3000000}).status_code == 409
    saved = next(row for row in owner.get("/api/owner/budgets").json() if row["scope_key"] == scope)
    assert saved["limit_micros"] == 2000000
    payload = {"key": str(uuid4()), "target": {"page": "budgets", "id": saved["id"]}}
    path = f"/api/owner/conversations/{conversation['id']}/receipts"
    receipt = owner.post(path, json=payload)
    assert receipt.status_code in {200, 201}, receipt.text
    assert owner.post(path, json=payload).json() == receipt.json()


@pytest.mark.parametrize("decision,assessment_status", [("Revise", "Collecting"), ("Rejected", "Rejected")])
def test_revision_and_rejection_have_truthful_receipts(workspace_clients, decision, assessment_status):
    owner, member = workspace_clients
    assessment = pending_application(member)
    approval = approval_for(owner, assessment)
    conversation = owner.post("/api/conversations").json()
    response = owner.post(
        f"/api/owner/approvals/{approval['id']}/decisions",
        json={"decision": decision, "fingerprint": approval["fingerprint"], "comment": "Current evidence reviewed."},
    )
    assert response.status_code == 200, response.text
    receipt = owner.post(
        f"/api/owner/conversations/{conversation['id']}/receipts",
        json={"key": str(uuid4()), "target": {"page": "approvals", "id": approval["id"]}},
    )
    assert receipt.status_code in {200, 201}, receipt.text
    assert member.get("/api/me/profile").json()["assessment"]["status"] == assessment_status
    history = owner.get(f"/api/conversations/{conversation['id']}").json()
    assert len(history["messages"]) == 1
    assert history["messages"][0]["cards"][0]["type"] == "OwnerReceipt"
    assert "Approved" not in json.dumps(history["messages"][0])


def test_superseded_application_cannot_be_decided_or_receive_success_receipt(workspace_clients):
    owner, member = workspace_clients
    assessment = pending_application(member)
    approval = approval_for(owner, assessment)
    conversation = owner.post("/api/conversations").json()
    response = member.post(
        f"/api/assessments/{assessment['id']}/confirm",
        json={"expected_version": assessment["version"], "facts": assessment["confirmed_facts"] | {"available_days": 4}},
    )
    assert response.status_code == 200
    response = owner.post(
        f"/api/owner/approvals/{approval['id']}/decisions",
        json={"decision": "Approved", "fingerprint": approval["fingerprint"]},
    )
    assert response.status_code == 409
    receipt = owner.post(
        f"/api/owner/conversations/{conversation['id']}/receipts",
        json={"key": str(uuid4()), "target": {"page": "approvals", "id": approval["id"]}},
    )
    assert receipt.status_code in {404, 409}
    assert owner.get(f"/api/conversations/{conversation['id']}").json()["messages"] == []
    assert member.get("/api/me/profile").json()["member"] is None


def test_member_confirmation_is_informational_and_never_an_owner_approval(workspace_clients):
    owner, member = workspace_clients
    assessment = pending_application(member)
    approval = approval_for(owner, assessment)
    user_id = member.get("/api/auth/me").json()["user"]["id"]
    with transaction() as db:
        waiting = Request(
            user_id=user_id,
            kind="Combined",
            status="AwaitingConfirmation",
            deadline=now() + timedelta(hours=1),
            payload={"role": "Coach"},
        )
        db.add(waiting)
        db.flush()
        request_id = waiting.id
    name = member.get("/api/auth/me").json()["user"]["display_name"]
    response = owner.get("/api/owner/workspace", params={"area": "attention", "query": name, "limit": 50})
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    application = next(item for item in items if approval["id"] in json.dumps(item))
    confirmation = next(item for item in items if request_id in json.dumps(item))
    assert application != confirmation
    text = json.dumps(confirmation).lower()
    assert "member" in text
    assert "waiting" in text
    assert confirmation["responsibility"] == "waiting_for_member"
    assert "action" not in confirmation
    assert confirmation["target"] == {"page": "dashboard", "id": request_id, "section": "trace"}
    assert '"approve"' not in text
    assert '"reject"' not in text


def test_natural_language_approval_request_only_prepares_the_exact_review(workspace_clients):
    owner, member = workspace_clients
    assessment = pending_application(member)
    approval = approval_for(owner, assessment)
    conversation = owner.post("/api/conversations").json()
    _, result = submit_owner(owner, conversation, f"Approve application {approval['id']} please.")
    assert result["status"] == "Completed"
    current = approval_for(owner, assessment)
    assert current["decision"] == "Pending"
    assert member.get("/api/me/profile").json()["member"] is None
    history = owner.get(f"/api/conversations/{conversation['id']}").json()
    assert history["messages"][-1]["cards"] == [{"type": "OwnerWorkspace", "area": "approvals", "target_id": approval["id"]}]


def test_receipt_key_cannot_be_repurposed_for_another_record(workspace_clients):
    owner, member = workspace_clients
    assessment = pending_application(member)
    approval = approval_for(owner, assessment)
    conversation = owner.post("/api/conversations").json()
    response = owner.post(
        f"/api/owner/approvals/{approval['id']}/decisions",
        json={"decision": "Approved", "fingerprint": approval["fingerprint"]},
    )
    assert response.status_code == 200
    path = f"/api/owner/conversations/{conversation['id']}/receipts"
    payload = {"key": str(uuid4()), "target": {"page": "approvals", "id": approval["id"]}}
    assert owner.post(path, json=payload).status_code == 200
    assert owner.post(path, json=payload | {"target": {"page": "approvals", "id": str(uuid4())}}).status_code == 409
    history = owner.get(f"/api/conversations/{conversation['id']}").json()
    assert len(history["messages"]) == 1


def test_budget_language_prepares_bounded_amount_without_changing_allowance(workspace_clients):
    owner, _ = workspace_clients
    before = next(row for row in owner.get("/api/owner/budgets").json() if row["scope_key"] == "global")
    conversation = owner.post("/api/conversations").json()
    before_calls = model_count()
    submit_owner(owner, conversation, "Set the global budget to USD 2.50")
    history = owner.get(f"/api/conversations/{conversation['id']}").json()
    card = history["messages"][-1]["cards"][0]
    assert card["area"] == "budgets"
    assert card["proposal"]["amount_usd"] == "2.50"
    after = next(row for row in owner.get("/api/owner/budgets").json() if row["scope_key"] == "global")
    assert after == before
    assert model_count() == before_calls


def test_group_language_resolves_member_and_destination_without_reassignment(workspace_clients):
    owner, member = workspace_clients
    assessment = pending_application(member)
    approval = approval_for(owner, assessment)
    assert owner.post(
        f"/api/owner/approvals/{approval['id']}/decisions",
        json={"decision": "Approved", "fingerprint": approval["fingerprint"]},
    ).status_code == 200
    account = member.get("/api/auth/me").json()["user"]
    initial = member.get("/api/me/profile").json()["member"]
    with transaction() as db:
        group = MemberGroup(name=f"Canary service {uuid4().hex}", configuration={})
        db.add(group)
        db.flush()
        group_id, group_name = group.id, group.name
    conversation = owner.post("/api/conversations").json()
    before_calls = model_count()
    submit_owner(owner, conversation, f"Move {account['display_name']} to {group_name} group")
    history = owner.get(f"/api/conversations/{conversation['id']}").json()
    card = history["messages"][-1]["cards"][0]
    assert card["area"] == "members"
    assert card["query"] == account["display_name"]
    assert card["proposal"] == {"group_id": group_id}
    assert member.get("/api/me/profile").json()["member"] == initial
    assert model_count() == before_calls


def test_audit_for_another_fingerprint_cannot_become_this_review_receipt(workspace_clients):
    from app.models import AuditEvent

    owner, member = workspace_clients
    assessment = pending_application(member)
    approval = approval_for(owner, assessment)
    conversation = owner.post("/api/conversations").json()
    actor = owner.get("/api/auth/me").json()["user"]["id"]
    with transaction() as db:
        db.add(AuditEvent(actor_id=actor, event_type="training.reviewed", subject_id=assessment["id"], details={"decision": "Approved", "fingerprint": "different-evidence"}))
    result = owner.post(
        f"/api/owner/conversations/{conversation['id']}/receipts",
        json={"key": str(uuid4()), "target": {"page": "approvals", "id": approval["id"]}},
    )
    assert result.status_code == 409
    assert owner.get(f"/api/conversations/{conversation['id']}").json()["messages"] == []


def test_invalid_comparison_receipt_does_not_fall_back_to_candidate_save(workspace_clients):
    from app.models import AuditEvent

    owner, _ = workspace_clients
    candidate = owner.get("/api/owner/agent-versions").json()["versions"][0]
    conversation = owner.post("/api/conversations").json()
    actor = owner.get("/api/auth/me").json()["user"]["id"]
    with transaction() as db:
        db.add(AuditEvent(actor_id=actor, event_type="agent_version.candidate_saved", subject_id=candidate["id"], details={}))
    result = owner.post(
        f"/api/owner/conversations/{conversation['id']}/receipts",
        json={"key": str(uuid4()), "target": {"page": "versions", "id": candidate["id"], "section": f"evaluation:{uuid4()}"}},
    )
    assert result.status_code == 404
    assert owner.get(f"/api/conversations/{conversation['id']}").json()["messages"] == []
