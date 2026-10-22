"""Owner revision requests preserve limits and require review of each new candidate."""

from copy import deepcopy
from uuid import uuid4

import pytest
from sqlalchemy import select
from test_admission_integration import facts, submit
from test_admission_integration import member as member_fixture
from test_admission_integration import owner_client as owner_fixture

from app.bootstrap import DEFAULT_CONFIGURATION
from app.database import transaction
from app.models import Member, MemberGroup, ModelCall, Request, WorkItem
from app.worker import run_once

member = member_fixture
owner_client = owner_fixture


@pytest.mark.parametrize("purpose", ["Training", "Nutrition", "Combined"])
def test_owner_revisions_remain_in_original_request(monkeypatch, member, owner_client, purpose):
    accepted = submit(member, facts())
    with transaction() as db:
        configuration = deepcopy(DEFAULT_CONFIGURATION)
        configuration["review_required"] = True
        group = MemberGroup(name=f"Review-{uuid4()}", configuration=configuration)
        db.add(group)
        db.flush()
        db.get(Member, accepted["member_id"]).group_id = group.id
    seen = []

    def generate(claim, instruction, context, output_type, max_output_tokens=2048):
        seen.append((claim.role, context.get("owner_review")))
        if claim.role == "Coach":
            content = {
                "title": "Routine",
                "rationale": "Respond to confirmed requirements.",
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
                "title": "Meal example",
                "rationale": "Respond to confirmed requirements.",
                "training_version_id": context["training_version_id"],
                "meals": [
                    {
                        "name": "Meal",
                        "portions": [{"food_id": "F007661", "grams": "150"}],
                        "preparation": "Use the specified preparation state.",
                    }
                ],
                "limitations": ["Synthetic meal example, not a daily recommendation."],
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

    monkeypatch.setattr("app.coach_runtime.generate", generate)
    monkeypatch.setattr("app.nutrition_runtime.generate", generate)
    session = member.post("/api/conversations").json()
    queued = member.post(
        f"/api/conversations/{session['id']}/messages",
        json={"key": str(uuid4()), "purpose": purpose, "content": "Create a candidate for review."},
    ).json()
    request_id = queued["request_id"]
    deadline = member.get(f"/api/requests/{request_id}").json()["deadline"]
    expected_kind = "PlanPair" if purpose == "Combined" else purpose + "Plan"
    for revision in range(3):
        assert run_once(request_id)
        if purpose == "Combined":
            assert run_once(request_id)
        approvals = [
            row
            for row in owner_client.get("/api/owner/approvals").json()
            if row["scope"].get("member_id") == accepted["member_id"] and row["decision"] == "Pending"
        ]
        assert len(approvals) == 1 and approvals[0]["subject_kind"] == expected_kind
        approval = approvals[0]
        endpoint = f"/api/owner/approvals/{approval['id']}/decisions"
        body = {
            "decision": "Revise",
            "fingerprint": approval["fingerprint"],
            "comment": "Explain the preparation or execution more clearly.",
        }
        if revision == 0:
            assert owner_client.post(endpoint, json=body | {"comment": " "}).status_code == 422
        decision = owner_client.post(endpoint, json=body)
        assert decision.status_code == 200, decision.text
        assert owner_client.post(endpoint, json=body).json() == decision.json()
        status = member.get(f"/api/requests/{request_id}").json()
        assert status["deadline"] == deadline
        assert status["revisions"] == min(revision + 1, 2)
        assert status["status"] == ("Stopped" if revision == 2 else "Queued")
        assert "owner requested changes" in status["status_message"].lower()
        assert member.get("/api/me/plans").json()["current"]["version"] == 0
        with transaction() as db:
            pending = list(
                db.scalars(
                    select(WorkItem).where(WorkItem.request_id == request_id, WorkItem.status == "Queued")
                )
            )
            assert len(pending) == (0 if revision == 2 else 1)
    assert not run_once(request_id)
    first_size = 2 if purpose == "Combined" else 1
    assert len(seen) == first_size * 3
    assert all(review is None for _, review in seen[:first_size])
    assert all(
        review and review["notes"] == body["comment"] and review["previous_candidate"]
        for _, review in seen[first_size:]
    )
