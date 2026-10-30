"""Full combined-plan workflow with synthetic model outputs and real persistence."""

from copy import deepcopy
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from progressive_test_support import run_with_member_approvals as run_once
from sqlalchemy import select

from app.bootstrap import bootstrap
from app.database import transaction
from app.main import app
from app.models import ModelCall, Request
from app.rules import EXCLUSIONS


@pytest.mark.parametrize(
    "first_invalid", [None, "Coach", "Nutrition", "Both", "Always", "CallLimit", "Legacy"]
)
def test_combined_candidate_to_atomic_activation(monkeypatch, first_invalid):
    bootstrap()
    calls = []
    expected_initial = (
        ["Coach", "Coach", "Nutrition"]
        if first_invalid in {"Coach", "Legacy"}
        else ["Coach", "Nutrition", "Nutrition"]
        if first_invalid == "Nutrition"
        else ["Coach", "Nutrition"]
    )

    if first_invalid == "Both":
        expected_initial = ["Coach", "Coach", "Nutrition", "Nutrition"]

    if first_invalid == "Legacy":

        def old_validation_wait(db, item, request, plan):
            item.status = "Waiting"
            item.wait_reason = "CandidateValidation"
            item.lease_until = None
            return {"status": "Waiting"}

        monkeypatch.setattr("app.coach_runtime.queue_correction", old_validation_wait)

    def synthetic_generate(claim, instruction, context, output_type, max_output_tokens=2048):
        calls.append(claim.role)
        if claim.role == "Coach":
            content = {
                "title": "Test strength routine",
                "rationale": "Synthetic workflow verification",
                "sessions": [
                    {
                        "day": 1,
                        "title": "Foundation",
                        "warmup_minutes": 5,
                        "cooldown_minutes": 5,
                        "transition_minutes": 2,
                        "exercises": [
                            {
                                "exercise_id": "biceps-curl",
                                "sets": 2,
                                "repetitions": 8,
                                "seconds_per_set": 40,
                                "rest_seconds": 60,
                                "execution_notes": "Move with control.",
                            }
                        ],
                    }
                ],
                "progression": "Review before increasing difficulty.",
                "limitations": ["Synthetic test output"],
            }
        else:
            content = {
                "title": "Test meal example",
                "rationale": "Synthetic workflow verification",
                "training_version_id": context["training_version_id"],
                "meals": [
                    {
                        "name": "Test meal",
                        "portions": [
                            {"food_id": "F007661", "grams": "150"},
                            {"food_id": "F002592", "grams": "100"},
                            {"food_id": "F001900", "grams": "100"},
                        ],
                        "preparation": "Use the named cooked food preparation states.",
                    }
                ],
                "limitations": ["Synthetic test output; not a daily dietary recommendation"],
            }
        if context.get("substitution"):
            content = deepcopy(context["baseline_plan"])
            content.pop("nutrient_totals", None)
            selection = context["substitution"]
            content["meals"][selection["meal_index"]]["portions"][selection["portion_index"]] = {
                "food_id": "F000262",
                "grams": "100",
            }
        if (
            claim.role == "Coach"
            and context.get("confirmed_cycle_feedback", {}).get("adjustment") == "Progression"
        ):
            content["sessions"][0]["exercises"][0]["seconds_per_set"] = 50
        if (first_invalid in {"Coach", "Both", "Legacy"} and len(calls) == 1) or first_invalid in {
            "Always",
            "CallLimit",
        }:
            content["sessions"][0]["exercises"][0]["exercise_id"] = "not-in-the-catalogue"
        if first_invalid in {"Coach", "Both", "Legacy"} and len(calls) == 2:
            assert context["correction"]["violations"] == ["Unknown exercise: not-in-the-catalogue."]
        if (first_invalid == "Nutrition" and len(calls) == 2) or (
            first_invalid == "Both" and len(calls) == 3
        ):
            content["meals"][0]["portions"][0]["food_id"] = "unknown-food"
        if (first_invalid == "Nutrition" and len(calls) == 3) or (
            first_invalid == "Both" and len(calls) == 4
        ):
            assert context["correction"]["violations"] == ["Unknown food catalogue entry: unknown-food."]
        if output_type.__name__ == "MealCompatibilityReview":
            content = {
                "decision": "SuggestChanges",
                "explanation": "Specific synthetic compatibility review.",
                "proposal": content,
            }
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
            return output, call.id

    monkeypatch.setattr("app.coach_runtime.generate", synthetic_generate)
    monkeypatch.setattr("app.nutrition_runtime.generate", synthetic_generate)
    with TestClient(app) as client:
        auth = client.post(
            "/api/auth/register",
            json={
                "email": f"combined-{uuid4()}@example.test",
                "password": "Integration-test-123!",
                "display_name": "Combined Workflow Test",
            },
        ).json()
        client.headers["x-csrf-token"] = auth["csrf_token"]
        assessment = client.post("/api/assessments").json()
        facts = dict(
            age=28,
            goal="Build strength",
            experience="Beginner",
            available_days=3,
            session_minutes=45,
            equipment=["Dumbbells"],
            dietary_restrictions=[],
            **{key: False for key in EXCLUSIONS},
        )
        accepted = client.post(
            f"/api/assessments/{assessment['id']}/confirm",
            json={"expected_version": assessment["version"], "facts": facts},
        )
        assert accepted.json()["status"] == "Accepted"
        session = client.post("/api/conversations").json()
        submitted = client.post(
            f"/api/conversations/{session['id']}/messages",
            json={
                "key": str(uuid4()),
                "purpose": "Combined",
                "content": "Create training and nutrition candidates.",
            },
        )
        assert submitted.status_code == 202
        request_id = submitted.json()["request_id"]
        if first_invalid in {"Always", "CallLimit"}:
            original_deadline = client.get(f"/api/requests/{request_id}").json()["deadline"]
            if first_invalid == "CallLimit":
                with transaction() as db:
                    db.get(Request, request_id).max_model_calls = 1
            runs = 1 if first_invalid == "CallLimit" else 3
            for _ in range(runs):
                assert run_once(request_id)
            assert not run_once(request_id)
            stopped = client.get(f"/api/requests/{request_id}").json()
            assert stopped["status"] == "Stopped"
            assert stopped["deadline"] == original_deadline
            assert stopped["revisions"] == runs - 1
            assert stopped["model_calls"] == runs
            assert calls == ["Coach"] * runs
            history = client.get("/api/me/plans").json()
            assert len(history["plans"]) == runs
            assert all(plan["status"] == "Invalid" for plan in history["plans"])
            assert not history["pairs"] and history["current"]["training_id"] is None
            return
        if first_invalid in {"Coach", "Both", "Legacy"}:
            assert run_once(request_id)
            failed = client.get(f"/api/requests/{request_id}").json()
            if first_invalid == "Legacy":
                assert failed["status"] == "Waiting"
                endpoint = f"/api/me/requests/{request_id}/correct"
                body = {"plan_id": failed["result"]["training_id"]}
                response = client.post(endpoint, json=body)
                assert response.status_code == 202, response.text
                assert client.post(endpoint, json=body).json() == response.json()
                failed = client.get(f"/api/requests/{request_id}").json()
            assert failed["status"] == "Queued" and failed["revisions"] == 1
            assert failed["result"]["correction_work_id"]
        assert run_once(request_id)
        assert client.get(f"/api/requests/{request_id}").json()["status"] == "Queued"
        assert run_once(request_id)
        if first_invalid in {"Nutrition", "Both"}:
            failed = client.get(f"/api/requests/{request_id}").json()
            assert failed["status"] == "Queued" and failed["revisions"] == (
                2 if first_invalid == "Both" else 1
            )
            assert run_once(request_id)
        assert calls == expected_initial
        status = client.get(f"/api/requests/{request_id}").json()
        assert status["status"] == "AwaitingConfirmation", status
        plans = client.get("/api/me/plans").json()
        assert len(plans["plans"]) == len(expected_initial)
        assert plans["current"]["training_id"] is None
        assert len(plans["pairs"]) == 1
        pair = plans["pairs"][0]
        body = {"request_id": request_id, "expected_current_version": 0, "operation_key": str(uuid4())}
        result = client.post(f"/api/me/plans/pairs/{pair['id']}/confirm", json=body)
        assert result.status_code == 200, result.text
        assert client.post(f"/api/me/plans/pairs/{pair['id']}/confirm", json=body).json() == result.json()
        current = client.get("/api/me/plans").json()["current"]
        assert current["training_id"] == pair["training_id"]
        assert current["nutrition_id"] == pair["nutrition_id"]
        assert current["version"] == 1
        assert not run_once(request_id)
        with transaction() as db:
            assert len(list(db.scalars(select(ModelCall).where(ModelCall.request_id == request_id)))) == len(
                expected_initial
            )

        substitution = {
            "key": str(uuid4()),
            "nutrition_id": current["nutrition_id"],
            "expected_current_version": current["version"],
            "meal_index": 0,
            "portion_index": 0,
            "preference": "Replace the rice with banana.",
        }
        queued = client.post("/api/me/food-substitutions", json=substitution)
        assert queued.status_code == 202, queued.text
        assert client.post("/api/me/food-substitutions", json=substitution).json() == queued.json()
        replacement_id = queued.json()["request_id"]
        assert run_once(replacement_id)
        replacement = client.get(f"/api/requests/{replacement_id}").json()
        assert replacement["status"] == "AwaitingConfirmation", replacement
        plans = client.get("/api/me/plans").json()
        assert plans["current"]["nutrition_id"] == current["nutrition_id"]
        nutrition = next(
            plan for plan in plans["plans"] if plan["id"] == replacement["result"]["nutrition_id"]
        )
        assert nutrition["baseline_id"] == current["nutrition_id"]
        assert nutrition["content"]["meals"][0]["portions"][0]["food_id"] == "F000262"
        assert nutrition["content"]["nutrient_totals"]
        confirmed = client.post(
            f"/api/me/plans/pairs/{replacement['result']['pair_id']}/confirm",
            json={"request_id": replacement_id, "expected_current_version": 1, "operation_key": str(uuid4())},
        )
        assert confirmed.status_code == 200, confirmed.text
        assert (
            client.post("/api/me/food-substitutions", json=substitution | {"key": str(uuid4())}).status_code
            == 409
        )
        assert calls == expected_initial + ["Nutrition"]

        from datetime import date, timedelta

        cycle_body = {
            "key": str(uuid4()),
            "feedback": {
                "training_id": current["training_id"],
                "expected_current_version": 2,
                "period_start": (date.today() - timedelta(days=7)).isoformat(),
                "period_end": date.today().isoformat(),
                "planned_sessions": 3,
                "completed_sessions": 3,
                "difficulty": "Manageable",
                "discomfort": False,
                "adjustment": "Maintenance",
                "experience": "The completed sessions were manageable and fit my routine.",
                "confirmed": True,
            },
        }
        cycle = client.post("/api/me/cycle-reviews", json=cycle_body)
        assert cycle.status_code == 202, cycle.text
        assert client.post("/api/me/cycle-reviews", json=cycle_body).json() == cycle.json()
        cycle_id = cycle.json()["request_id"]
        assert run_once(cycle_id)
        assert run_once(cycle_id)
        pending = client.get(f"/api/requests/{cycle_id}").json()
        assert pending["status"] == "AwaitingConfirmation", pending
        assert pending["result"]["generation_method"] == "ValidatedRetention"
        assert pending["result"]["call_id"] is None
        assert calls == expected_initial + ["Nutrition", "Coach"]
        assert client.get("/api/me/plans").json()["current"]["version"] == 2
        activated = client.post(
            f"/api/me/plans/pairs/{pending['result']['pair_id']}/confirm",
            json={"request_id": cycle_id, "expected_current_version": 2, "operation_key": str(uuid4())},
        )
        assert activated.status_code == 200, activated.text
        assert client.get(f"/api/requests/{cycle_id}").json()["status"] == "Completed"

        progressed = deepcopy(cycle_body)
        progressed["key"] = str(uuid4())
        progressed["feedback"].update(
            {
                "training_id": activated.json()["training_id"],
                "expected_current_version": 3,
                "difficulty": "TooEasy",
                "adjustment": "Progression",
            }
        )
        progression = client.post("/api/me/cycle-reviews", json=progressed)
        assert progression.status_code == 202, progression.text
        progression_id = progression.json()["request_id"]
        assert run_once(progression_id)
        assert run_once(progression_id)
        changed = client.get(f"/api/requests/{progression_id}").json()
        assert changed["status"] == "AwaitingConfirmation", changed
        assert changed["result"]["generation_method"] == "Model"
        assert calls == expected_initial + [
            "Nutrition",
            "Coach",
            "Coach",
            "Nutrition",
        ]

        direct = client.post(
            f"/api/conversations/{session['id']}/messages",
            json={
                "key": str(uuid4()),
                "purpose": "Training",
                "content": "Revise my current routine to be easier to follow.",
            },
        )
        assert direct.status_code == 409, "Competing updates must not silently overwrite a pending routine"
        latest = client.post(
            f"/api/me/plans/pairs/{changed['result']['pair_id']}/confirm",
            json={"request_id": progression_id, "expected_current_version": 3, "operation_key": str(uuid4())},
        )
        assert latest.status_code == 200, latest.text
        follow = client.post(
            f"/api/conversations/{session['id']}/messages",
            json={
                "key": str(uuid4()),
                "purpose": "Training",
                "content": "Revise my current routine to be easier to follow.",
            },
        )
        assert follow.status_code == 202, follow.text
        new_request = client.get(f"/api/requests/{follow.json()['request_id']}").json()
        assert new_request["payload"]["plan_baseline"]["version"] == 4
        assert calls == expected_initial + [
            "Nutrition",
            "Coach",
            "Coach",
            "Nutrition",
        ]
