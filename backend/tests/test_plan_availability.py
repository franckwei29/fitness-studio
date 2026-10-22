"""Previously activated plans must not appear usable after context changes."""

from types import SimpleNamespace

import pytest

from app.plans import availability


@pytest.mark.parametrize(
    "status,profile,configuration,enabled,usable",
    [
        ("Accepted", 2, 3, True, True),
        ("PendingOwner", 2, 3, True, False),
        ("Accepted", 1, 3, True, False),
        ("Accepted", 2, 2, True, False),
        ("Accepted", 2, 3, False, False),
    ],
)
@pytest.mark.parametrize("kind", ["Training", "Nutrition"])
def test_current_context_required(status, profile, configuration, enabled, usable, kind):
    member = SimpleNamespace(status=status, profile_version=2)
    group = SimpleNamespace(version=3, configuration={"capabilities": {kind.lower(): enabled}})
    plan = SimpleNamespace(kind=kind, profile_version=profile, config_version=configuration)
    result = availability(member, group, plan)
    assert result["usable"] is usable
    assert bool(result["reason"]) is not usable


def test_missing_group_fails_closed():
    member = SimpleNamespace(status="Accepted", profile_version=1)
    assert not availability(member, None, SimpleNamespace())["usable"]
