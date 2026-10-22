"""Revisions receive only confirmed feedback for the exact current role and plan."""

from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from test_activation_integration import candidate as candidate_fixture

from app.activation import activate_pair
from app.database import transaction
from app.models import Observation, now
from app.planning_context import revision_context

candidate = candidate_fixture


@pytest.mark.parametrize("role", ["Coach", "Nutrition"])
def test_feedback_is_filtered_before_limit_and_includes_confirmed_cycle_reviews(candidate, role):
    activated = activate_pair(**candidate)
    identifier = activated["training_id" if role == "Coach" else "nutrition_id"]
    kind = "TrainingFeedback" if role == "Coach" else "NutritionFeedback"
    at = now()
    with transaction() as db:
        valid = Observation(
            member_id=candidate["member_id"],
            kind=kind,
            content={"plan_id": identifier, "notes": "Current plan feedback"},
            created_at=at - timedelta(days=1),
        )
        cycle = Observation(
            member_id=candidate["member_id"],
            kind="CycleFeedback",
            content={
                "training_id": activated["training_id"],
                "confirmed": True,
                "completed_sessions": 2,
                "planned_sessions": 3,
            },
            created_at=at - timedelta(hours=12),
        )
        db.add_all([valid, cycle])
        # More recent unrelated versions must not displace relevant observations.
        for _ in range(25):
            db.add(
                Observation(
                    member_id=candidate["member_id"],
                    kind=kind,
                    content={"plan_id": str(uuid4()), "notes": "Other plan"},
                    created_at=at,
                )
            )
        db.add(
            Observation(
                member_id=candidate["member_id"],
                kind=kind,
                status="Draft",
                content={"plan_id": identifier, "notes": "Unconfirmed"},
                created_at=at,
            )
        )
        db.add(
            Observation(
                member_id=candidate["member_id"],
                kind="CycleFeedback",
                status="Draft",
                content={"training_id": identifier, "confirmed": False},
                created_at=at,
            )
        )
        db.flush()
        valid_id, cycle_id = valid.id, cycle.id
    with transaction() as db:
        context = revision_context(
            db, SimpleNamespace(payload={}), {"member_id": candidate["member_id"]}, role
        )
        assert context["previous_plan"]["id"] == identifier
        expected = [cycle_id, valid_id] if role == "Coach" else [valid_id]
        assert [row["id"] for row in context["confirmed_feedback"]] == expected
        assert context["confirmed_feedback"][-1]["kind"] == kind
        if role == "Coach":
            assert context["confirmed_feedback"][0]["content"]["completed_sessions"] == 2


def test_without_an_active_plan_no_unbound_feedback_is_forwarded(candidate):
    with transaction() as db:
        db.add(
            Observation(
                member_id=candidate["member_id"], kind="TrainingFeedback", content={"notes": "Unbound"}
            )
        )
    with transaction() as db:
        context = revision_context(
            db, SimpleNamespace(payload={}), {"member_id": candidate["member_id"]}, "Coach"
        )
        assert context["previous_plan"] is None and context["confirmed_feedback"] == []
