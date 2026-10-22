"""Deterministic admission rules independent of model recommendations."""

from dataclasses import dataclass

RULE_VERSION = "admission-v1"
EXCLUSIONS = {
    "prescribed_exercise": "A prescribed exercise program requires professional supervision.",
    "prescribed_nutrition": "A prescribed nutrition program is outside our service scope.",
    "medical_supervision": "Ongoing medical supervision is outside our service scope.",
    "pregnancy_postpartum": "Pregnancy and early postpartum support are outside our service scope.",
    "postoperative_acute_injury": "Postoperative recovery or an unresolved acute injury requires professional support.",
}
REQUIRED = (
    "age",
    "goal",
    "experience",
    "available_days",
    "session_minutes",
    "equipment",
    "dietary_restrictions",
    *EXCLUSIONS,
)


@dataclass(frozen=True)
class EligibilityResult:
    outcome: str
    reasons: list[str]
    missing: list[str]
    rule_version: str = RULE_VERSION


def assess(facts: dict) -> EligibilityResult:
    missing = [key for key in REQUIRED if key not in facts or facts[key] is None]
    for key in EXCLUSIONS:
        if key in facts and type(facts[key]) is not bool and key not in missing:
            missing.append(key)
    for key in ("goal", "experience"):
        if key in facts and (not isinstance(facts[key], str) or not facts[key].strip()):
            if key not in missing:
                missing.append(key)
    for key in ("equipment", "dietary_restrictions"):
        if key in facts and not isinstance(facts[key], list) and key not in missing:
            missing.append(key)
    for key, low, high in (("age", 1, 120), ("available_days", 1, 7), ("session_minutes", 5, 240)):
        value = facts.get(key)
        if type(value) is not int or not low <= value <= high:
            if key not in missing:
                missing.append(key)
    reasons = [reason for key, reason in EXCLUSIONS.items() if facts.get(key) is True]
    age = facts.get("age")
    if type(age) is int and 0 < age < 18:
        reasons.append("This studio serves adults aged 18 and over.")
    if reasons:
        return EligibilityResult("ReferredOut", reasons, missing)
    if missing or facts.get("conflicting_information"):
        return EligibilityResult("NeedsInformation", ["Please confirm the required information."], missing)
    if facts.get("resolved_past_injury") is True or facts.get("goal_review_required") is True:
        return EligibilityResult("PendingOwnerReview", ["A complete case requires an authorised review."], [])
    return EligibilityResult("Eligible", [], [])
