"""Typed training proposals and deterministic catalogue/availability checks."""

from math import isfinite
from typing import Literal

from pydantic import Field, model_validator

from app.agent_contracts import StrictModel

TRAINING_RULE_VERSION = "training-met-v2"


class ExerciseDefinition(StrictModel):
    id: str
    name: str = Field(min_length=1, max_length=1000)
    equipment: list[str]
    instructions: list[str]
    aliases: list[str] = []
    category: str = "strength"
    measurement: Literal["reps", "duration"] = "reps"
    met: float = Field(default=3.5, gt=0, le=50, allow_inf_nan=False)
    met_basis: str = "Estimated average activity intensity"
    origin: str = "Estimate"
    source_url: str
    catalogue_version: str


class ExerciseSet(StrictModel):
    exercise_id: str
    sets: int = Field(ge=1, le=20)
    repetitions: int | None = Field(default=None, ge=1, le=100)
    seconds_per_set: int = Field(ge=1, le=14400)
    rest_seconds: int = Field(ge=0, le=900)
    execution_notes: str = Field(min_length=1, max_length=1000)


class TrainingSession(StrictModel):
    day: int = Field(ge=1, le=7)
    title: str = Field(min_length=1, max_length=120)
    warmup_minutes: int = Field(ge=0, le=60)
    cooldown_minutes: int = Field(ge=0, le=60)
    transition_minutes: int = Field(ge=0, le=60)
    exercises: list[ExerciseSet] = Field(min_length=1, max_length=20)

    def duration_seconds(self):
        return 60 * (self.warmup_minutes + self.cooldown_minutes + self.transition_minutes) + sum(
            item.sets * item.seconds_per_set + max(item.sets - 1, 0) * item.rest_seconds
            for item in self.exercises
        )


class TrainingProposal(StrictModel):
    title: str = Field(min_length=1, max_length=160)
    rationale: str = Field(min_length=1, max_length=3000)
    sessions: list[TrainingSession] = Field(min_length=1, max_length=7)
    progression: str = Field(min_length=1, max_length=2000)
    limitations: list[str]

    @model_validator(mode="after")
    def distinct_days(self):
        days = [session.day for session in self.sessions]
        if len(set(days)) != len(days):
            raise ValueError("Training days must be distinct.")
        return self


def validate_training(
    proposal: TrainingProposal, facts: dict, catalogue: dict[str, ExerciseDefinition], required_ids=()
):
    """These checks establish structural fit, not clinical safety certification."""
    violations = []
    used = {x.exercise_id for s in proposal.sessions for x in s.exercises}
    if set(required_ids) - used:
        violations.append("The draft omitted a requested activity.")
    weight = facts.get("weight_kg")
    if type(weight) not in (int, float) or not isfinite(weight) or weight <= 0:
        violations.append("Confirmed weight is missing or invalid.")
    days = facts.get("available_days")
    minutes = facts.get("session_minutes")
    equipment = facts.get("equipment")
    if type(days) is not int or not 1 <= days <= 7:
        violations.append("Confirmed weekly availability is missing or invalid.")
    elif len(proposal.sessions) > days:
        violations.append("The plan exceeds the confirmed weekly training availability.")
    if type(minutes) is not int or not 5 <= minutes <= 240:
        violations.append("Confirmed session duration is missing or invalid.")
    if not isinstance(equipment, list) or any(not isinstance(item, str) for item in equipment):
        violations.append("Confirmed equipment is missing or invalid.")
    for session in proposal.sessions:
        if type(minutes) is int and session.duration_seconds() > minutes * 60:
            violations.append(f"Day {session.day} exceeds the confirmed session duration.")
        for item in session.exercises:
            definition = catalogue.get(item.exercise_id)
            if not definition:
                violations.append(f"Unknown exercise: {item.exercise_id}.")
                continue
            if not definition.instructions:
                violations.append(f"Exercise instructions are missing: {definition.id}.")
    return {
        "passed": not violations,
        "violations": violations,
        "rule_version": TRAINING_RULE_VERSION,
        "basis": {
            "available_days": days,
            "session_minutes": minutes,
            "catalogue_versions": sorted({item.catalogue_version for item in catalogue.values()}),
            "session_seconds": [session.duration_seconds() for session in proposal.sessions],
        },
    }


def training_estimate(proposal, profile, catalogue):
    """Gross estimated energy of exercise blocks only; never count rest/transition twice."""
    weight = profile.get("weight_kg")
    if type(weight) not in (int, float) or not isfinite(weight) or weight <= 0:
        return None
    sessions = []
    for session in proposal.sessions:
        entries = []
        for item in session.exercises:
            definition = catalogue.get(item.exercise_id)
            if definition is None:
                continue
            seconds = item.sets * item.seconds_per_set
            entries.append(
                {
                    "exercise_id": item.exercise_id,
                    "seconds": seconds,
                    "met": definition.met,
                    "kcal": round(definition.met * weight * seconds / 3600, 1),
                }
            )
        sessions.append(
            {"day": session.day, "items": entries, "kcal": round(sum(x["kcal"] for x in entries), 1)}
        )
    return {
        "weight_kg": weight,
        "sessions": sessions,
        "kcal": round(sum(s["kcal"] for s in sessions), 1),
        "basis": "Estimated gross energy for exercise time only; warm-up, cooldown, transitions and rests excluded. Not measured activity.",
    }
