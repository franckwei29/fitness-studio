"""Requested local foods/sports reach validated immutable plans, then explicit activation."""

from uuid import uuid4

import pytest
from progressive_test_support import run_with_member_approvals
from test_admission_integration import facts, submit
from test_admission_integration import member as member_fixture

from app.database import transaction
from app.models import ModelCall, Request

member = member_fixture


@pytest.mark.parametrize("kind,query", [("Training", "游泳"), ("Training", "篮球"), ("Nutrition", "牛排")])
def test_requested_items_reach_plan_and_activation(monkeypatch, member, kind, query):
    submit(member, facts(weight_kg=80, equipment=[]))
    monkeypatch.setattr("app.catalogue_resolver.requested_queries", lambda *args: [query])

    def generate(claim, instruction, context, output_type, *args):
        if kind == "Training":
            identifier = context["requested_exercise_ids"][0]
            assert any(x["id"] == identifier for x in context["exercise_catalogue"])
            content = dict(
                title="Requested sport",
                rationale="Explicit member preference",
                sessions=[
                    dict(
                        day=1,
                        title="Activity",
                        warmup_minutes=5,
                        cooldown_minutes=5,
                        transition_minutes=0,
                        exercises=[
                            dict(
                                exercise_id=identifier,
                                sets=1,
                                repetitions=None,
                                seconds_per_set=1800,
                                rest_seconds=0,
                                execution_notes="Comfortable pace.",
                            )
                        ],
                    )
                ],
                progression="Review duration.",
                limitations=["Estimated energy."],
            )
        else:
            identifier = context["requested_food_ids"][0]
            assert any(x["id"] == identifier for x in context["foods"])
            content = dict(
                title="Requested meal",
                rationale="Explicit member preference",
                training_version_id=None,
                meals=[
                    dict(
                        name="Meal",
                        portions=[dict(food_id=identifier, grams="150")],
                        preparation="Use the named state.",
                    )
                ],
                limitations=["Estimated nutrition."],
            )
        output = output_type.model_validate(content)
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
                output=output.model_dump(mode="json"),
            )
            db.add(call)
            db.flush()
            return output, call.id

    monkeypatch.setattr("app.coach_runtime.generate", generate)
    monkeypatch.setattr("app.nutrition_runtime.generate", generate)
    session = member.post("/api/conversations").json()
    queued = member.post(
        f"/api/conversations/{session['id']}/messages",
        json={"key": str(uuid4()), "purpose": kind, "content": "请加入" + query},
    )
    assert queued.status_code == 202
    request_id = queued.json()["request_id"]
    assert run_with_member_approvals(request_id)
    data = member.get("/api/me/plans").json()
    plan = next(p for p in data["plans"] if p["request_id"] == request_id)
    assert plan["status"] == "Validated"
    assert data["current"]["version"] == 0
    content = plan["content"]
    snapshot = next(iter(content["catalogue_snapshot"].values()))
    if kind == "Training":
        assert content["training_estimate"]["weight_kg"] == 80
        assert content["training_estimate"]["kcal"] == round(snapshot["met"] * 80 * 0.5, 1)
        assert snapshot["measurement"] == "duration"
    else:
        assert float(content["nutrient_totals"]["energy_kcal"]) == pytest.approx(
            float(snapshot["energy_kcal_per_100g"]) * 1.5, abs=0.01
        )
    confirmed = member.post(
        f"/api/me/plans/{kind.lower()}/{plan['id']}/confirm",
        json={"expected_current_version": 0, "operation_key": str(uuid4())},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert member.get("/api/me/plans").json()["current"][kind.lower() + "_id"] == plan["id"]


def test_catalogue_schema_failure_settles_usage_before_one_retry(monkeypatch, member):
    from pydantic import SecretStr
    from sqlalchemy import select
    from test_catalogue_resolution import food
    from test_openai_provider import usage

    from app import model_gateway
    from app.catalogue_resolver import SearchEvidence, resolve_food
    from app.gemini import ProviderFailure, ProviderResult
    from app.models import FoodRecord
    from app.settings import get_settings
    from app.work_queue import claim_next

    submit(member, facts())
    session = member.post("/api/conversations").json()
    queued = member.post(
        f"/api/conversations/{session['id']}/messages",
        json={"key": str(uuid4()), "purpose": "Nutrition", "content": "Synthetic lookup"},
    )
    request_id = queued.json()["request_id"]
    claim = claim_next(request_id=request_id)
    settings = get_settings().model_copy(
        update={
            "model_calls_enabled": True,
            "model_provider": "openai",
            "openai_api_key": SecretStr("synthetic-not-sent"),
        }
    )
    monkeypatch.setattr(model_gateway, "get_settings", lambda: settings)
    query = "Synthetic retry food " + uuid4().hex
    attempts = []

    def send(self, payload, output_type):
        attempts.append(payload)
        if len(attempts) == 1:
            raise ProviderFailure("Invalid synthetic schema", usage=usage(), provider_id="synthetic-invalid")
        output = (
            SearchEvidence(evidence="Synthetic evidence") if output_type is SearchEvidence else food(query)
        )
        return ProviderResult("synthetic-valid-" + str(len(attempts)), output, usage())

    monkeypatch.setattr(model_gateway.OpenAITransport, "send", send)
    monkeypatch.setattr(model_gateway.OpenAITransport, "count_input_tokens", lambda self, payload: 1000)
    entry = resolve_food(claim, query)
    try:
        assert len(attempts) == 3
        with transaction() as db:
            calls = list(
                db.scalars(
                    select(ModelCall).where(ModelCall.request_id == request_id).order_by(ModelCall.created_at)
                )
            )
            assert len(calls) == 3 and all(c.status == "Settled" for c in calls)
            assert sum(c.actual_micros for c in calls) == 396
            assert calls[0].output is None
        assert resolve_food(claim, query).id == entry.id and len(attempts) == 3
    finally:
        member.post(f"/api/requests/{request_id}/cancel", json={})
        with transaction() as db:
            db.delete(db.get(FoodRecord, entry.id))
