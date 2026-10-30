"""Unified conversation boundaries against real SQL and Redis, with synthetic inference."""

from datetime import timedelta
from uuid import uuid4

import pytest
from redis import Redis
from sqlalchemy import select
from test_admission_integration import facts, submit
from test_admission_integration import member as member_fixture

from app.activation import plan_fingerprint
from app.database import transaction
from app.intent_router import IntentReply, resume_admission_goals
from app.models import (
    ChatSession,
    CurrentPlans,
    Member,
    ModelCall,
    PlanVersion,
    Request,
    WorkItem,
    now,
)
from app.rate_limit import ModelRateLimiter
from app.settings import get_settings
from app.worker import run_once

member = member_fixture


def send(client, conversation, content="Explain my current routine.", **fields):
    body = {"key": str(uuid4()), "purpose": "Auto", "content": content} | fields
    response = client.post(f"/api/conversations/{conversation}/messages", json=body)
    assert response.status_code == 202, response.text
    return response.json(), body


def synthetic(monkeypatch, decision):
    contexts = []

    def generate(claim, instruction, context, output_type):
        contexts.append(context)
        assert output_type is IntentReply
        with transaction() as db:
            request = db.get(Request, claim.request_id)
            call = ModelCall(
                request_id=request.id,
                user_id=request.user_id,
                role=claim.role,
                model="synthetic",
                status="Settled",
                reserved_micros=0,
                actual_micros=0,
                price_version="test",
            )
            db.add(call)
            db.flush()
            return decision, call.id

    monkeypatch.setattr("app.intent_router.generate", generate)
    return contexts


def current_plans(client):
    accepted = submit(client, facts())
    with transaction() as db:
        member = db.get(Member, accepted["member_id"])
        request = Request(
            user_id=member.user_id, kind="Combined", status="Completed", deadline=now() + timedelta(hours=1)
        )
        db.add(request)
        db.flush()
        current = db.get(CurrentPlans, member.id)
        for kind in ["Training", "Nutrition"]:
            plan = PlanVersion(
                member_id=member.id,
                request_id=request.id,
                kind=kind,
                version=1,
                status="Active",
                profile_version=member.profile_version,
                config_version=1,
                agent_version_id="synthetic",
                content={"title": f"Current {kind}"},
                rationale="Fixture",
                fingerprint="pending",
            )
            db.add(plan)
            db.flush()
            plan.fingerprint = plan_fingerprint(plan)
            setattr(current, kind.lower() + "_id", plan.id)
        current.version = 1
    return accepted


def test_read_both_plans_keeps_host_and_does_not_call_provider(member, monkeypatch):
    current_plans(member)
    conversation = member.post("/api/conversations").json()["id"]
    send(member, conversation, action="ConsultCoach")
    monkeypatch.setattr("app.intent_router.generate", lambda *args: pytest.fail("Read action must not infer"))
    result, body = send(member, conversation, action="ViewPlans")
    assert member.post(f"/api/conversations/{conversation}/messages", json=body).json() == result
    history = member.get(f"/api/conversations/{conversation}").json()
    assert history["conversation"]["active_role"] == "Coach"
    assert sum(c["type"] == "Handoff" for m in history["messages"] for c in m["cards"]) == 1
    summary = member.get("/api/me/plan-summary").json()
    assert {p["kind"] for p in summary["plans"]} == {"Training", "Nutrition"}
    assert all(p["availability"]["usable"] for p in summary["plans"])
    with transaction() as db:
        assert db.scalar(select(ModelCall).where(ModelCall.request_id == result["request_id"])) is None


@pytest.mark.parametrize("intent", ["Answer", "ViewPlans", "Clarify"])
def test_read_or_question_never_generates_or_switches_role(member, monkeypatch, intent):
    current_plans(member)
    contexts = synthetic(monkeypatch, IntentReply(intent=intent, reply="Here is an explanation."))
    conversation = member.post("/api/conversations").json()["id"]
    send(member, conversation, action="ConsultNutrition")
    result, _ = send(member, conversation)
    assert run_once(result["request_id"])
    history = member.get(f"/api/conversations/{conversation}").json()
    assert history["conversation"]["active_role"] == "Nutrition"
    assert history["messages"][-1]["sender"] == "Nutrition"
    assert len(contexts) == 1 and "profile" not in contexts[0]
    with transaction() as db:
        assert db.get(Request, result["request_id"]).kind == "Auto"
        assert len(list(db.scalars(select(WorkItem).where(WorkItem.request_id == result["request_id"])))) == 1


def test_explicit_generation_delegates_and_updates_host(member, monkeypatch):
    submit(member, facts())
    synthetic(monkeypatch, IntentReply(intent="Combined", reply="Prepare both plans."))
    conversation = member.post("/api/conversations").json()["id"]
    result, _ = send(member, conversation, "Create training and meals for me.")
    assert run_once(result["request_id"])
    with transaction() as db:
        request = db.get(Request, result["request_id"])
        assert request.kind == "Combined" and request.status == "Queued"
        assert db.get(ChatSession, conversation).active_role == "Coach"
        works = list(db.scalars(select(WorkItem).where(WorkItem.request_id == request.id)))
        assert {(w.role, w.status) for w in works} == {("Intake", "Completed"), ("Coach", "Queued")}
    assert member.get("/api/me/activity").json()[0]["status"] == "Waiting to start"


def test_linked_training_generates_before_requesting_meal_decisions(member, monkeypatch):
    current_plans(member)
    synthetic(monkeypatch, IntentReply(intent="Training", reply="Shorter training.", keep_meals=True))
    conversation = member.post("/api/conversations").json()["id"]
    result, _ = send(member, conversation, "Shorten training; keep my meals.")
    run_once(result["request_id"])
    with transaction() as db:
        source = db.get(Request, result["request_id"])
        assert source.kind == "Combined" and source.status == "Queued"
        assert source.payload["training_first"] and source.payload["keep_meals"]
        assert not (source.result or {}).get("needs_input")
        assert (
            db.scalar(
                select(WorkItem).where(WorkItem.request_id == source.id, WorkItem.role == "Coach")
            ).status
            == "Queued"
        )


@pytest.mark.parametrize("end", ["Accepted", "Cancelled", "Expired"])
def test_admission_goal_resumes_only_when_valid(member, monkeypatch, end):
    synthetic(monkeypatch, IntentReply(intent="Training", reply="Create training."))
    conversation = member.post("/api/conversations").json()["id"]
    result, _ = send(member, conversation, "Please create training.")
    run_once(result["request_id"])
    with transaction() as db:
        request = db.get(Request, result["request_id"])
        assert request.status == "AwaitingConfirmation"
        assert request.result["pending_reason"] == "Admission"
        if end == "Expired":
            request.deadline = now() - timedelta(seconds=1)
    if end == "Cancelled":
        assert member.post(f"/api/requests/{result['request_id']}/cancel").status_code == 200
    submit(member, facts())
    resume_admission_goals()
    with transaction() as db:
        request = db.get(Request, result["request_id"])
        assert (request.kind == "Training") == (end == "Accepted")
        if end == "Accepted":
            assert request.status == "Queued"


def test_usage_counts_rolling_account_calls_without_other_accounts(member):
    uid = member.get("/api/auth/me").json()["user"]["id"]
    r = Redis.from_url(get_settings().redis_url)
    gate = ModelRateLimiter(r)
    stamp = r.time()[0] * 1000
    key = gate.account_key(uid)
    r.zadd(key, {"fresh": stamp, "old": stamp - 86401000})
    try:
        response = member.get("/api/me/usage")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["account_used"] == 1 and body["account_limit"] == 200
        assert body["account_window_seconds"] == 86400
        assert "global_budget" not in body and "user_id" not in body
        assert r.zcard(key) == 2  # Observation does not mutate history.
    finally:
        r.delete(key)
        r.close()


def test_stale_summary_hides_unusable_contents(member):
    accepted = current_plans(member)
    with transaction() as db:
        db.get(Member, accepted["member_id"]).profile_version += 1
    body = member.get("/api/me/plan-summary").json()
    assert all(not p["availability"]["usable"] and p["content"] == {} for p in body["plans"])


def test_activity_completed_draft_is_not_active(member):
    current_plans(member)
    body = member.get("/api/me/activity").json()
    assert any(r["status"] == "In use" for r in body)
    with transaction() as db:
        uid = member.get("/api/auth/me").json()["user"]["id"]
        m = db.scalar(select(Member).where(Member.user_id == uid))
        current = db.get(CurrentPlans, m.id)
        current.training_id = current.nutrition_id = None
        plans = list(db.scalars(select(PlanVersion).where(PlanVersion.member_id == m.id)))
        for p in plans:
            p.status = "Validated"
    body = member.get("/api/me/activity").json()
    assert any(r["status"] == "Ready for review" for r in body)
    assert not any(r["status"] == "In use" for r in body)


def test_declining_coordination_consumes_choice_without_new_work(member, monkeypatch):
    current_plans(member)
    synthetic(monkeypatch, IntentReply(intent="Training", reply="Shorter training."))
    conversation = member.post("/api/conversations").json()["id"]
    result, _ = send(member, conversation)
    run_once(result["request_id"])
    # Previously issued choices remain safely consumable after the routing change.
    with transaction() as db:
        source = db.get(Request, result["request_id"])
        source.kind, source.status = "Auto", "AwaitingConfirmation"
        source.result = {"pending_reason": "Coordination", "pending_goal": "Combined"}
    send(member, conversation, action="KeepChat", source_request_id=result["request_id"])
    response = member.post(
        f"/api/conversations/{conversation}/messages",
        json={
            "content": "Review both",
            "purpose": "Auto",
            "key": str(uuid4()),
            "action": "AllowCombined",
            "source_request_id": result["request_id"],
        },
    )
    assert response.status_code == 409
    with transaction() as db:
        source = db.get(Request, result["request_id"])
        assert source.status == "Completed" and source.result["scope_declined"]
        assert source.kind == "Auto"


def test_delayed_role_inference_does_not_override_new_choice(member, monkeypatch):
    conversation = member.post("/api/conversations").json()["id"]
    result, _ = send(member, conversation, "Talk to Coach")
    send(member, conversation, action="ConsultNutrition")
    synthetic(monkeypatch, IntentReply(intent="Consult", reply="Coach", consultation_role="Coach"))
    run_once(result["request_id"])
    history = member.get(f"/api/conversations/{conversation}").json()
    assert history["conversation"]["active_role"] == "Nutrition"
    assert sum(c["type"] == "Handoff" for m in history["messages"] for c in m["cards"]) == 1


@pytest.mark.parametrize(
    "intent,card",
    [
        ("Cancel", "Activity"),
        ("ViewActivity", "Activity"),
        ("ReviewDrafts", "ReviewDrafts"),
        ("Feedback", "Feedback"),
        ("Substitution", "Substitution"),
    ],
)
def test_actions_offer_exact_existing_flow_without_silent_writes(member, monkeypatch, intent, card):
    conversation = member.post("/api/conversations").json()["id"]
    synthetic(monkeypatch, IntentReply(intent=intent, reply="Review the details first."))
    result, _ = send(member, conversation)
    run_once(result["request_id"])
    history = member.get(f"/api/conversations/{conversation}").json()
    if intent in {"Feedback", "Substitution"}:
        preview = next(c for c in history["messages"][-1]["cards"] if c["type"] == "MemberAction")
        assert preview["kind"] == intent
        assert preview["state"] == "Pending"
    else:
        assert history["messages"][-1]["cards"] == [{"type": card}]
    assert history["requests"][-1]["kind"] == "Auto"


def test_usage_failure_does_not_report_zero(member, monkeypatch):
    from redis.exceptions import ConnectionError

    monkeypatch.setattr(Redis, "time", lambda *a: (_ for _ in ()).throw(ConnectionError()))
    assert member.get("/api/me/usage").status_code == 503


def test_display_nutrition_totals_are_recomputed_not_trusted():
    from types import SimpleNamespace

    from app.catalogue import food_catalogue
    from app.plans import nutrition_totals

    food = next(iter(food_catalogue().values()))
    plan = SimpleNamespace(
        kind="Nutrition",
        content={
            "title": "Example",
            "rationale": "Example",
            "training_version_id": None,
            "meals": [
                {"name": "Meal", "portions": [{"food_id": food.id, "grams": 100}], "preparation": "Prepare."}
            ],
            "limitations": [],
            "nutrient_totals": {"energy_kcal": "999999"},
        },
    )
    result = nutrition_totals(plan)
    assert result is not None and float(result["energy_kcal"]) == round(float(food.energy_kcal_per_100g), 2)


def test_declined_invitation_is_not_repeated_and_scope_choice_keeps_deadline(member, monkeypatch):
    conversation = member.post("/api/conversations").json()["id"]
    send(member, conversation, action="KeepChat")
    synthetic(
        monkeypatch, IntentReply(intent="Answer", reply="An explanation.", offer_consultation="Nutrition")
    )
    result, _ = send(member, conversation)
    run_once(result["request_id"])
    history = member.get(f"/api/conversations/{conversation}").json()
    assert history["messages"][-1]["cards"] == []
    current_plans(member)
    synthetic(
        monkeypatch,
        IntentReply(intent="Training", reply="A draft.", task_goal="Shorten training to twenty minutes."),
    )
    result, _ = send(member, conversation, "Twenty minutes.")
    run_once(result["request_id"])
    with transaction() as db:
        source = db.get(Request, result["request_id"])
        deadline = source.deadline
        assert source.payload["content"] == "Shorten training to twenty minutes."
    with transaction() as db:
        assert db.get(Request, result["request_id"]).deadline == deadline
        assert db.get(Request, result["request_id"]).payload["training_first"]
