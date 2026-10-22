"""Confirmed cycle feedback and explicit next-cycle workload comparisons."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.training import TrainingProposal


class CycleFeedback(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    training_id: str
    expected_current_version: int = Field(ge=1)
    period_start: date = Field(strict=False)
    period_end: date = Field(strict=False)
    planned_sessions: int = Field(ge=1, le=100)
    completed_sessions: int = Field(ge=0, le=100)
    difficulty: Literal["TooEasy", "Manageable", "TooHard"]
    discomfort: bool
    adjustment: Literal["Progression", "Maintenance", "Deload"]
    experience: str = Field(min_length=10, max_length=3000)
    confirmed: bool

    @model_validator(mode="after")
    def confirmed_feedback(self):
        if not self.confirmed:
            raise ValueError("Confirm the cycle feedback before requesting a review.")
        if not self.experience.strip():
            raise ValueError("Describe your actual experience during this cycle.")
        if self.period_start > self.period_end or self.period_end > date.today():
            raise ValueError("The review period must be ordered and cannot end in the future.")
        if (self.period_end - self.period_start).days > 90:
            raise ValueError("Review at most 90 days in one cycle.")
        if self.completed_sessions > self.planned_sessions:
            raise ValueError("Completed sessions cannot exceed the sessions reported as planned.")
        if self.discomfort:
            raise ValueError(
                "Record your discomfort and review your profile before requesting another training cycle."
            )
        if self.adjustment == "Progression" and (
            self.completed_sessions != self.planned_sessions or self.difficulty != "TooEasy"
        ):
            raise ValueError(
                "Progression requires confirmed completion of the planned sessions and a too-easy assessment."
            )
        if self.difficulty == "TooHard" and self.adjustment != "Deload":
            raise ValueError("Choose a reduced workload when the previous cycle was too hard.")
        return self


def active_seconds(content):
    """Planned active exercise time is a workload proxy, not physiological load."""
    return sum(
        exercise["sets"] * exercise["seconds_per_set"]
        for session in content["sessions"]
        for exercise in session["exercises"]
    )


def validate_cycle_adjustment(proposal: TrainingProposal, baseline: dict, feedback: CycleFeedback):
    previous = active_seconds(baseline)
    candidate = active_seconds(proposal.model_dump(mode="json"))
    adjustment = feedback.adjustment
    valid = {
        "Progression": candidate > previous,
        "Maintenance": candidate == previous,
        "Deload": candidate < previous,
    }[adjustment]
    return {
        "passed": valid,
        "violations": [] if valid else ["The candidate workload does not match the confirmed adjustment."],
        "basis": {
            "adjustment": adjustment,
            "metric": "Planned active exercise seconds per cycle",
            "baseline_seconds": previous,
            "candidate_seconds": candidate,
            "difference_seconds": candidate - previous,
            "limitation": "This time-based comparison does not measure exercise intensity or clinical suitability.",
            "feedback_source": "Member-confirmed self-report",
        },
    }
