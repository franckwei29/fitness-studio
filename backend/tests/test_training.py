"""Structural training validation with an explicitly synthetic catalogue."""

import pytest
from pydantic import ValidationError

from app.training import ExerciseDefinition, TrainingProposal, validate_training


def proposal():
    return TrainingProposal.model_validate(
        {
            "title": "Synthetic training",
            "rationale": "Test structure",
            "sessions": [
                {
                    "day": 1,
                    "title": "Test session",
                    "warmup_minutes": 5,
                    "cooldown_minutes": 5,
                    "transition_minutes": 2,
                    "exercises": [
                        {
                            "exercise_id": "test-squat",
                            "sets": 3,
                            "repetitions": 10,
                            "seconds_per_set": 60,
                            "rest_seconds": 60,
                            "execution_notes": "Synthetic instructions for validation tests.",
                        }
                    ],
                }
            ],
            "progression": "Test progression",
            "limitations": [],
        }
    )


def catalogue():
    return {
        "test-squat": ExerciseDefinition(
            id="test-squat",
            name="Synthetic squat",
            equipment=["Dumbbells"],
            instructions=["Synthetic instructions"],
            source_url="https://example.test/synthetic",
            catalogue_version="synthetic-v1",
        )
    }


def facts(**changes):
    return dict(available_days=3, session_minutes=45, equipment=["Dumbbells"]) | changes


def test_duration_includes_sets_rest_warmup_and_transitions():
    plan = proposal()
    assert plan.sessions[0].duration_seconds() == 1020
    assert validate_training(plan, facts(), catalogue())["passed"]
    assert not validate_training(plan, facts(session_minutes=15), catalogue())["passed"]


def test_unavailable_equipment_and_unknown_exercises_fail():
    assert not validate_training(proposal(), facts(equipment=[]), catalogue())["passed"]
    assert not validate_training(proposal(), facts(), {})["passed"]


def test_missing_availability_is_not_silently_assumed():
    assert not validate_training(proposal(), {}, catalogue())["passed"]


def test_duplicate_training_days_rejected():
    body = proposal().model_dump()
    body["sessions"] *= 2
    with pytest.raises(ValidationError, match="distinct"):
        TrainingProposal.model_validate(body)


def test_days_beyond_availability_fail():
    body = proposal().model_dump()
    second = body["sessions"][0] | {"day": 2}
    body["sessions"].append(second)
    assert not validate_training(TrainingProposal.model_validate(body), facts(available_days=1), catalogue())[
        "passed"
    ]
