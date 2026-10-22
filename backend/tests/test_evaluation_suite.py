"""Fixed regression inputs detect hard failures without calling a model."""

from app.evaluation_suite import evaluation_cases, score_case
from app.rules import assess


def test_suite_covers_all_roles_and_copies_inputs():
    roles = ["Intake", "Coach", "Nutrition", "TechnicalManager"]
    assert sum(len(evaluation_cases(role)) for role in roles) == 12
    suite = evaluation_cases("Coach")
    suite[0]["input"]["profile"]["session_minutes"] = 999
    assert evaluation_cases("Coach")[0]["input"]["profile"]["session_minutes"] == 10
    assert all(case["input"]["exercise_catalogue"] for case in evaluation_cases("Coach"))
    assert all(case["input"]["foods"] for case in evaluation_cases("Nutrition"))


def test_unanswered_health_conditions_must_stay_unknown():
    case = evaluation_cases("Intake")[0]
    facts = case["expected"]["facts"]
    output = {
        "reply": "Please complete the remaining questions.",
        "proposed_facts": facts,
        "missing_fields": assess(facts).missing,
        "contradictions": [],
    }
    assert score_case("Intake", case, output)["passed"]
    output["proposed_facts"] = facts | {"medical_supervision": False}
    result = score_case("Intake", case, output)
    assert not result["passed"]
    assert any("Unanswered eligibility" in value for value in result["violations"])


def test_training_duration_violation_is_scored():
    case = evaluation_cases("Coach")[0]
    identifier = case["input"]["exercise_catalogue"][0]["id"]
    output = {
        "title": "Test",
        "rationale": "Test example",
        "sessions": [
            {
                "day": 1,
                "title": "Session",
                "warmup_minutes": 20,
                "cooldown_minutes": 0,
                "transition_minutes": 0,
                "exercises": [
                    {
                        "exercise_id": identifier,
                        "sets": 1,
                        "seconds_per_set": 30,
                        "rest_seconds": 0,
                        "execution_notes": "Controlled movement",
                    }
                ],
            }
        ],
        "progression": "Review feedback.",
        "limitations": [],
    }
    result = score_case("Coach", case, output)
    assert not result["passed"]
    assert any("duration" in text for text in result["violations"])


def test_nutrition_must_reference_exact_training_and_known_food():
    case = evaluation_cases("Nutrition")[0]
    output = {
        "title": "Test",
        "rationale": "Example",
        "training_version_id": "wrong",
        "meals": [
            {"name": "Meal", "portions": [{"food_id": "invented", "grams": 100}], "preparation": "Prepare."}
        ],
        "limitations": [],
    }
    result = score_case("Nutrition", case, output)
    assert not result["passed"] and len(result["violations"]) == 2


def test_text_quality_never_claims_automatic_approval():
    case = evaluation_cases("TechnicalManager")[0]
    result = score_case("TechnicalManager", case, {"reply": "There is one unknown call."})
    assert result["passed"] and result["manual_review_required"]
    assert not score_case("TechnicalManager", case, {"reply": "中文回复"})["passed"]
    assert not score_case("TechnicalManager", case, {"unrecognized": "value"})["passed"]
