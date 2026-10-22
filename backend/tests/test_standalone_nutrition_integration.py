"""Independent nutrition generation, review, substitution, and activation boundaries."""

from copy import deepcopy
from uuid import uuid4

import pytest
from sqlalchemy import select
from test_admission_integration import facts, submit
from test_admission_integration import member as member_fixture
from test_admission_integration import owner_client as owner_fixture

from app.bootstrap import DEFAULT_CONFIGURATION
from app.database import transaction
from app.models import Member, MemberGroup, ModelCall, Request
from app.worker import run_once

member = member_fixture
owner_client = owner_fixture


@pytest.mark.parametrize("training_enabled,review_required", [(True, False), (False, False), (False, True)])
def test_independent_nutrition_lifecycle(
    monkeypatch, member, owner_client, training_enabled, review_required
):
    submit(member, facts())
    member_id = member.get("/api/me/profile").json()["member"]["id"]
    with transaction() as db:
        configuration = deepcopy(DEFAULT_CONFIGURATION)
        configuration["capabilities"]["training"] = training_enabled
        configuration["review_required"] = review_required
        group = MemberGroup(name=f"Nutrition-{uuid4()}", configuration=configuration)
        db.add(group)
        db.flush()
        db.get(Member, member_id).group_id = group.id

    calls = []

    def generate(claim, instruction, context, output_type, max_output_tokens=2048):
        assert claim.role == "Nutrition"
        assert context["training_version_id"] is None
        content = {
            "title": "Independent meal example",
            "rationale": "Synthetic independent nutrition verification",
            "training_version_id": None,
            "meals": [
                {
                    "name": "Meal",
                    "portions": [
                        {"food_id": "F007661", "grams": "150"},
                        {"food_id": "F002592", "grams": "100"},
                        {"food_id": "F001900", "grams": "100"},
                    ],
                    "preparation": "Use the specified preparation states.",
                }
            ],
            "limitations": ["Synthetic meal example, not a full daily recommendation."],
        }
        if context.get("substitution"):
            content = deepcopy(context["baseline_plan"])
            content.pop("nutrient_totals", None)
            content["meals"][0]["portions"][0]["grams"] = "160"
        output = output_type.model_validate(content)
        with transaction() as db:
            request = db.get(Request, claim.request_id)
            request.model_calls += 1
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
            calls.append(call.id)
            return output, call.id

    monkeypatch.setattr("app.nutrition_runtime.generate", generate)
    session = member.post("/api/conversations").json()
    response = member.post(
        f"/api/conversations/{session['id']}/messages",
        json={"key": str(uuid4()), "purpose": "Nutrition", "content": "Create a meal example."},
    )
    assert response.status_code == 202, response.text

    def finish(request_id, expected_version):
        assert run_once(request_id)
        status = member.get(f"/api/requests/{request_id}").json()
        assert status["status"] == "AwaitingConfirmation", status
        plans = member.get("/api/me/plans").json()
        assert not plans["pairs"]
        assert plans["current"]["version"] == expected_version
        plan = next(row for row in plans["plans"] if row["request_id"] == request_id)
        assert plan["training_input_id"] is None
        assert plan["content"]["training_version_id"] is None
        endpoint = f"/api/me/plans/nutrition/{plan['id']}/confirm"
        body = {"expected_current_version": expected_version, "operation_key": str(uuid4())}
        if review_required:
            assert member.post(endpoint, json=body).status_code == 409
            approval = next(
                row
                for row in owner_client.get("/api/owner/approvals").json()
                if row["subject_id"] == plan["id"]
            )
            assert approval["subject_kind"] == "NutritionPlan"
            reviewed = owner_client.post(
                f"/api/owner/approvals/{approval['id']}/decisions",
                json={"decision": "Approved", "fingerprint": approval["fingerprint"]},
            )
            assert reviewed.status_code == 200, reviewed.text
        stale = member.post(endpoint, json=body | {"expected_current_version": expected_version + 10})
        assert stale.status_code == 409
        confirmed = member.post(endpoint, json=body)
        assert confirmed.status_code == 200, confirmed.text
        assert member.post(endpoint, json=body).json() == confirmed.json()
        current = member.get("/api/me/plans").json()["current"]
        assert current["nutrition_id"] == plan["id"]
        assert current["training_id"] is None and current["pair_id"] is None
        assert current["version"] == expected_version + 1
        assert member.get(f"/api/requests/{request_id}").json()["status"] == "Completed"
        return plan

    first = finish(response.json()["request_id"], 0)
    substitution = member.post(
        "/api/me/food-substitutions",
        json={
            "key": str(uuid4()),
            "nutrition_id": first["id"],
            "expected_current_version": 1,
            "meal_index": 0,
            "portion_index": 0,
            "preference": "Increase this portion slightly.",
        },
    )
    assert substitution.status_code == 202, substitution.text
    second = finish(substitution.json()["request_id"], 1)
    assert second["content"]["meals"][0]["portions"][0]["grams"] == "160"
    assert len(calls) == 2
    with transaction() as db:
        assert len(list(db.scalars(select(ModelCall).where(ModelCall.id.in_(calls))))) == 2

    revision = member.post(
        f"/api/conversations/{session['id']}/messages",
        json={"key": str(uuid4()), "purpose": "Nutrition", "content": "Revise my meal example."},
    )
    assert revision.status_code == 202, revision.text
    revision_id = revision.json()["request_id"]
    assert run_once(revision_id)
    plans = member.get("/api/me/plans").json()
    candidate = next(row for row in plans["plans"] if row["request_id"] == revision_id)
    assert candidate["status"] == "Validated"
    cancelled = member.post(f"/api/requests/{revision_id}/cancel")
    assert cancelled.status_code == 200, cancelled.text
    confirmation = member.post(
        f"/api/me/plans/nutrition/{candidate['id']}/confirm",
        json={"expected_current_version": 2, "operation_key": str(uuid4())},
    )
    assert confirmation.status_code == 409
    unchanged = member.get("/api/me/plans").json()
    assert unchanged["current"]["nutrition_id"] == second["id"]
    assert unchanged["current"]["version"] == 2
    assert not next(row for row in unchanged["plans"] if row["id"] == candidate["id"])["availability"][
        "usable"
    ]


def test_no_supported_food_stops_before_inference(monkeypatch, member):
    submit(member, facts())
    monkeypatch.setattr("app.nutrition_runtime.food_catalogue", lambda: {})

    def forbidden(*args, **kwargs):
        raise AssertionError("No supported food must not dispatch inference")

    monkeypatch.setattr("app.nutrition_runtime.generate", forbidden)
    session = member.post("/api/conversations").json()
    queued = member.post(
        f"/api/conversations/{session['id']}/messages",
        json={"key": str(uuid4()), "purpose": "Nutrition", "content": "Create a general meal example."},
    )
    assert queued.status_code == 202, queued.text
    identifier = queued.json()["request_id"]
    assert run_once(identifier)
    status = member.get("/api/requests/" + identifier).json()
    assert status["status"] == "Waiting" and status["model_calls"] == 0
    assert "permissions or profile need review" in status["status_message"]
    assert member.get("/api/me/plans").json()["current"]["version"] == 0
