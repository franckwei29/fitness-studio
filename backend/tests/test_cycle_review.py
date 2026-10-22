"""Cycle changes require explicit feedback and match the reported adjustment."""

from datetime import date, timedelta

import pytest
from pydantic import ValidationError

from app.cycle_review import CycleFeedback, validate_cycle_adjustment
from app.training import TrainingProposal


def feedback(**changes):
    return CycleFeedback.model_validate(
        {
            "training_id": "training",
            "expected_current_version": 1,
            "period_start": date.today() - timedelta(days=7),
            "period_end": date.today(),
            "planned_sessions": 3,
            "completed_sessions": 3,
            "difficulty": "TooEasy",
            "discomfort": False,
            "adjustment": "Progression",
            "experience": "I completed the planned sessions comfortably.",
            "confirmed": True,
            **changes,
        }
    )


def proposal(seconds):
    return TrainingProposal.model_validate(
        {
            "title": "Next cycle",
            "rationale": "Confirmed feedback",
            "sessions": [
                {
                    "day": 1,
                    "title": "Session",
                    "warmup_minutes": 5,
                    "cooldown_minutes": 5,
                    "transition_minutes": 0,
                    "exercises": [
                        {
                            "exercise_id": "test",
                            "sets": 2,
                            "seconds_per_set": seconds,
                            "rest_seconds": 30,
                            "execution_notes": "Controlled movement.",
                        }
                    ],
                }
            ],
            "progression": "Review the next cycle.",
            "limitations": [],
        }
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"confirmed": False},
        {"completed_sessions": 4},
        {"completed_sessions": 2},
        {"difficulty": "Manageable"},
        {"difficulty": "TooHard"},
        {"discomfort": True},
        {"period_end": date.today() + timedelta(days=1)},
        {"experience": "          "},
    ],
)
def test_incomplete_or_inconsistent_progression_is_rejected(changes):
    with pytest.raises(ValidationError):
        feedback(**changes)


@pytest.mark.parametrize("adjustment,seconds", [("Progression", 50), ("Maintenance", 40), ("Deload", 30)])
def test_workload_comparison_matches_confirmed_direction(adjustment, seconds):
    result = validate_cycle_adjustment(
        proposal(seconds), proposal(40).model_dump(), feedback(adjustment=adjustment)
    )
    assert result["passed"]
    assert result["basis"]["difference_seconds"] == 2 * (seconds - 40)


def test_progression_label_cannot_hide_unchanged_workload():
    assert not validate_cycle_adjustment(proposal(40), proposal(40).model_dump(), feedback())["passed"]
