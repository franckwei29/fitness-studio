"""Task version selection rejects tampering, wrong roles, and silent rebinding."""

from types import SimpleNamespace

import pytest

from app.admission import fingerprint
from app.agent_versions import resolve_version, verify_version
from app.models import AgentVersion, MemberGroup
from app.policy import PolicyDenied
from app.settings import get_settings


def version():
    values = {"role": "Coach", "model": get_settings().gemini_model, "prompt": "Test instruction"}
    return SimpleNamespace(id="version-a", fingerprint=fingerprint(values), **values)


def test_version_contents_are_immutable():
    row = version()
    assert verify_version(row, "Coach") is row
    row.prompt = "Changed instruction"
    with pytest.raises(PolicyDenied):
        verify_version(row, "Coach")


def test_wrong_role_and_missing_version_are_rejected():
    with pytest.raises(PolicyDenied):
        verify_version(version(), "Nutrition")
    with pytest.raises(PolicyDenied):
        verify_version(None, "Coach")


def test_configured_version_is_pinned_and_cannot_silently_change():
    row = version()
    group = SimpleNamespace(configuration={"agent_versions": {"Coach": row.id}})

    class Database:
        def get(self, model, identifier):
            if model is MemberGroup:
                return group
            assert model is AgentVersion and identifier == row.id
            return row

    item = SimpleNamespace(role="Coach", input_versions={"profile": 2})
    assert resolve_version(Database(), item, {"group_id": "group"}, "Unused default") is row
    assert item.input_versions == {"profile": 2, "agent_version": row.id}
    item.input_versions = {"agent_version": "earlier-version"}
    with pytest.raises(PolicyDenied):
        resolve_version(Database(), item, {"group_id": "group"}, "Unused default")
