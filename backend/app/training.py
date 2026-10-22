"""Typed training proposals and deterministic catalogue/availability checks."""

from pydantic import Field, model_validator

from app.agent_contracts import StrictModel

TRAINING_RULE_VERSION = "training-structure-v1"


class ExerciseDefinition(StrictModel):
    id: str
    name: str
    equipment: list[str]
    instructions: list[str]
    source_url: str
    catalogue_version: str


class ExerciseSet(StrictModel):
    exercise_id: str
    sets: int = Field(ge=1, le=20)
    repetitions: int | None = Field(default=None, ge=1, le=100)
    seconds_per_set: int = Field(ge=1, le=3600)
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


def validate_training(proposal: TrainingProposal, facts: dict, catalogue: dict[str, ExerciseDefinition]):
    """These checks establish structural fit, not clinical safety certification."""
    violations = []
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
        available = set()
    else:
        available = {item.strip().casefold() for item in equipment}
    for session in proposal.sessions:
        if type(minutes) is int and session.duration_seconds() > minutes * 60:
            violations.append(f"Day {session.day} exceeds the confirmed session duration.")
        for item in session.exercises:
            definition = catalogue.get(item.exercise_id)
            if not definition:
                violations.append(f"Unknown exercise: {item.exercise_id}.")
                continue
            if not {value.strip().casefold() for value in definition.equipment} <= available:
                violations.append(f"Equipment is unavailable for {definition.name}.")
            if not definition.source_url or not definition.instructions:
                violations.append(f"Exercise provenance or instructions are missing: {definition.id}.")
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
