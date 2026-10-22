"""Role restrictions cannot be widened through group configuration."""

import pytest

from app.policy import PolicyDenied, authorize_tool, filter_profile, permitted_fields


def test_technical_manager_never_receives_health_profile():
    assert (
        filter_profile(
            "TechnicalManager",
            {"age": 28, "medical_supervision": True},
            {"field_permissions": {"TechnicalManager": ["age", "medical_supervision"]}},
        )
        == {}
    )


def test_group_can_restrict_but_not_expand_coach_fields():
    profile = {"age": 28, "goal": "Build strength", "dietary_restrictions": ["Peanuts"]}
    assert filter_profile(
        "Coach", profile, {"field_permissions": {"Coach": ["goal", "dietary_restrictions"]}}
    ) == {"goal": "Build strength"}
    assert permitted_fields("Coach", {"field_permissions": {"Coach": []}}) == set()


@pytest.mark.parametrize(
    "role,tool",
    [("Nutrition", "propose_training"), ("TechnicalManager", "propose_intake"), ("Coach", "propose_change")],
)
def test_cross_role_tools_are_denied(role, tool):
    with pytest.raises(PolicyDenied):
        authorize_tool(role, tool)


def test_invalid_permission_configuration_fails_closed():
    with pytest.raises(PolicyDenied):
        permitted_fields("Coach", {"field_permissions": {"Coach": "all"}})
