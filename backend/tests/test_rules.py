import pytest

from app.rules import EXCLUSIONS, assess


@pytest.fixture
def facts():
    return dict(
        age=28,
        goal="Build strength",
        experience="Beginner",
        available_days=3,
        session_minutes=45,
        equipment=["Dumbbells"],
        dietary_restrictions=[],
        **{key: False for key in EXCLUSIONS},
    )


def test_complete_application_is_eligible(facts):
    assert assess(facts).outcome == "Eligible"


@pytest.mark.parametrize("field", list(EXCLUSIONS))
def test_each_exclusion_blocks_service(facts, field):
    facts[field] = True
    assert assess(facts).outcome == "ReferredOut"


def test_missing_answer_cannot_be_approved(facts):
    facts.pop("medical_supervision")
    facts["resolved_past_injury"] = True
    assert assess(facts).outcome == "NeedsInformation"


def test_string_false_is_not_a_confirmed_boolean(facts):
    facts["medical_supervision"] = "false"
    assert assess(facts).outcome == "NeedsInformation"


def test_minor_is_excluded(facts):
    facts["age"] = 17
    assert assess(facts).outcome == "ReferredOut"


def test_complete_discretionary_case_needs_review(facts):
    facts["resolved_past_injury"] = True
    assert assess(facts).outcome == "PendingOwnerReview"
