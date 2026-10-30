"""Training-first decisions use concrete drafts and preserve current plans until confirmation."""

from copy import deepcopy
from uuid import uuid4

import pytest
from sqlalchemy import select
from test_admission_integration import member as member_fixture
from test_sage_experience_integration import current_plans, send, synthetic
from test_training import proposal as training_sample

from app.activation import plan_fingerprint
from app.database import transaction
from app.intent_router import IntentReply
from app.models import CurrentPlans, Member, ModelCall, PlanVersion
from app.nutrition_runtime import MealCompatibilityReview
from app.worker import run_once

member = member_fixture


@pytest.mark.parametrize("decision", ["Retain", "SuggestChanges"])
def test_training_first_drafts_then_decides(member, monkeypatch, decision):
    current_plans(member)
    training = training_sample().model_dump(mode="json")
    training["sessions"][0]["exercises"][0]["exercise_id"] = "biceps-curl"
    meals = dict(
        title="Existing meals",
        rationale="General example",
        training_version_id=None,
        meals=[
            dict(name="Meal", portions=[dict(food_id="F007661", grams="150")], preparation="Use cooked food.")
        ],
        limitations=["General example only."],
    )
    uid = member.get("/api/auth/me").json()["user"]["id"]
    with transaction() as db:
        person = db.scalar(select(Member).where(Member.user_id == uid))
        current = db.get(CurrentPlans, person.id)
        member_id = person.id
        original = (current.training_id, current.nutrition_id, current.version)
        meals["training_version_id"] = current.training_id
        for identifier, content in [(current.training_id, training), (current.nutrition_id, meals)]:
            plan = db.get(PlanVersion, identifier)
            plan.content = content
            plan.fingerprint = plan_fingerprint(plan)
    synthetic(monkeypatch, IntentReply(intent="Training", reply="Simplify training."))
    contexts = []

    def generate(claim, instruction, context, output_type, max_output_tokens=2048):
        contexts.append(context)
        if claim.role == "Coach":
            content = deepcopy(training)
            content["sessions"][0]["exercises"][0]["sets"] = 2
            output = output_type.model_validate(content)
        else:
            assert output_type is MealCompatibilityReview
            content = deepcopy(meals)
            content["training_version_id"] = context["training_version_id"]
            # Even if a Retain response accidentally supplies a different portion,
            # the application must copy the original meals exactly.
            content["meals"][0]["portions"][0]["grams"] = "175"
            output = output_type.model_validate(
                dict(decision=decision, explanation="Specific review explanation.", proposal=content)
            )
        with transaction() as db:
            call = ModelCall(
                request_id=claim.request_id,
                user_id=uid,
                role=claim.role,
                model="synthetic",
                status="Settled",
                reserved_micros=0,
                actual_micros=0,
                price_version="test",
            )
            db.add(call)
            db.flush()
            return output, call.id

    monkeypatch.setattr("app.coach_runtime.generate", generate)
    monkeypatch.setattr("app.nutrition_runtime.generate", generate)
    conversation = member.post("/api/conversations").json()["id"]
    result, _ = send(member, conversation, "Make training easier, within 30 minutes.")
    rid = result["request_id"]
    for _ in range(2):
        assert run_once(rid)
    assert not run_once(rid), "Nutrition must not run before member endorsement"
    workflow = member.get(f"/api/conversations/{conversation}").json()["workflows"][0]
    assert workflow["stage"] == "TrainingDiscussion"
    if decision == "Retain":
        initial = workflow["training_id"]
        deadline = member.get(f"/api/requests/{rid}").json()["deadline"]
        path = f"/api/me/workflows/{workflow['id']}/actions"
        follow, _ = send(member, conversation, "Please adjust the current draft to use fewer exercises.")
        assert run_once(follow["request_id"])
        assert member.get(f"/api/requests/{follow['request_id']}").json()["status"] == "Completed"
        assert run_once(rid)
        assert not run_once(rid)
        workflow = member.get(f"/api/conversations/{conversation}").json()["workflows"][0]
        for feedback in ["Remove one exercise.", "Use fewer sets.", "Allow longer rests."]:
            revised = member.post(
                path,
                json=dict(
                    action="revise_training",
                    expected_revision=workflow["revision"],
                    operation_key=str(uuid4()),
                    feedback=feedback,
                ),
            )
            assert revised.status_code == 200, revised.text
            assert run_once(rid)
            assert not run_once(rid)
            workflow = member.get(f"/api/conversations/{conversation}").json()["workflows"][0]
            assert workflow["stage"] == "TrainingDiscussion"
            assert workflow["training_id"] != initial
        assert member.get(f"/api/requests/{rid}").json()["deadline"] == deadline
        assert not any(c.get("training_version_id") for c in contexts)
        messages = member.get(f"/api/conversations/{conversation}").json()["messages"]
        assert len({m["id"] for m in messages}) == len(messages)
    draft = member.get("/api/me/plan-drafts/" + workflow["training_id"]).json()["plan"]
    operation = dict(
        action="endorse_training",
        expected_revision=workflow["revision"],
        operation_key=str(uuid4()),
        target_id=draft["id"],
        target_fingerprint=draft["fingerprint"],
    )
    path = f"/api/me/workflows/{workflow['id']}/actions"
    queued = member.post(path, json=operation)
    assert queued.status_code == 200, queued.text
    assert member.post(path, json=operation).json() == queued.json()
    assert run_once(rid)
    response = member.get(f"/api/requests/{rid}").json()
    assert response["status"] == "AwaitingConfirmation", response
    assert response["result"].get("meal_changes") == (decision == "SuggestChanges"), response
    pair_id = response["result"]["pair_id"]
    with transaction() as db:
        current = db.get(CurrentPlans, member_id)
        assert (current.training_id, current.nutrition_id, current.version) == original
        draft = db.get(PlanVersion, response["result"]["nutrition_id"])
        assert draft.content["meals"][0]["portions"][0]["grams"] == ("150" if decision == "Retain" else "175")
    if decision == "SuggestChanges":
        workflow = member.get(f"/api/conversations/{conversation}").json()["workflows"][0]
        assert workflow["stage"] == "MealImpact"
        rejected = member.post(
            f"/api/me/plans/pairs/{pair_id}/confirm",
            json=dict(request_id=rid, expected_current_version=original[2], operation_key=str(uuid4())),
        )
        assert rejected.status_code == 409
        revisit = member.post(
            path,
            json=dict(
                action="keep_meals", expected_revision=workflow["revision"], operation_key=str(uuid4())
            ),
        )
        assert revisit.status_code == 200, revisit.text
        assert run_once(rid)
        assert contexts[-1]["meal_constraint"]["meals"]["meals"] == meals["meals"]
        assert not run_once(rid)
        with transaction() as db:
            assert db.get(CurrentPlans, member_id).version == original[2]
    else:
        activated = member.post(
            f"/api/me/plans/pairs/{pair_id}/confirm",
            json=dict(request_id=rid, expected_current_version=original[2], operation_key=str(uuid4())),
        )
        assert activated.status_code == 200, activated.text
        assert member.get(f"/api/requests/{rid}").json()["status"] == "Completed"
        assert member.post(f"/api/requests/{rid}/cancel", json={}).status_code == 409
        with transaction() as db:
            current = db.get(CurrentPlans, member_id)
            assert current.training_id != original[0]
            assert db.get(PlanVersion, current.nutrition_id).content["meals"] == meals["meals"]
