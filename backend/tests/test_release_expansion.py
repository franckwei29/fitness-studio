"""Expansion rechecks current observations rather than trusting an old status."""

from types import SimpleNamespace as Row

import pytest

from app.agent_release import verify_canary_source
from app.models import ChangeRequest
from app.policy import PolicyDenied


@pytest.mark.parametrize(
    "invalid",
    [
        None,
        "short-window",
        "few-runs",
        "failure",
        "rule-failure",
        "new-config",
        "rolled-back",
        "nested-source",
    ],
)
def test_expansion_needs_current_successful_original_canary(monkeypatch, invalid):
    source = Row(
        kind="AgentRelease",
        status="Observed",
        group_id="canary",
        candidate={"evaluation_id": "evaluation", "configuration": {"test": True}},
        execution={"group_version": 2},
    )
    group = Row(name="Canary", version=2, configuration={"test": True})
    evidence = {
        "window_elapsed": True,
        "minimum_runs_met": True,
        "hard_rule_failures": [],
        "execution_failures": [],
    }
    if invalid == "short-window":
        evidence["window_elapsed"] = False
    if invalid == "few-runs":
        evidence["minimum_runs_met"] = False
    if invalid == "failure":
        evidence["execution_failures"] = ["task"]
    if invalid == "rule-failure":
        evidence["hard_rule_failures"] = ["task"]
    if invalid == "new-config":
        group.version = 3
    if invalid == "rolled-back":
        source.status = "RolledBack"
    if invalid == "nested-source":
        source.candidate["source_release_id"] = "another-expansion"
    checked = []
    monkeypatch.setattr("app.agent_release.check_proposal", lambda db, row: checked.append(row))
    monkeypatch.setattr("app.canary_monitor.observation", lambda *args: evidence)

    class Database:
        def get(self, table, identifier):
            return source if table is ChangeRequest else group

    if invalid:
        with pytest.raises(PolicyDenied):
            verify_canary_source(Database(), "source", "evaluation")
    else:
        assert verify_canary_source(Database(), "source", "evaluation") is source
        assert checked == [source]
