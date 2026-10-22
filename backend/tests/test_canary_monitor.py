"""Observation metrics exclude other configurations and non-dispatched tasks."""

from datetime import timedelta
from types import SimpleNamespace as Row

from app.canary_monitor import observation
from app.models import now
from app.pricing import price_for
from app.settings import get_settings


def test_observation_is_version_scoped_and_requires_call_evidence():
    at = now()
    change = Row(
        group_id="canary",
        candidate={
            "role": "Coach",
            "agent_version_id": "candidate",
            "observation_minutes": 30,
            "minimum_runs": 2,
        },
        execution={"started_at": (at - timedelta(minutes=31)).isoformat(), "group_version": 2},
    )
    inputs = {"group_id": "canary", "group": 2, "agent_version": "candidate"}
    tasks = [
        Row(
            id=key,
            request_id=key,
            role="Coach",
            input_versions=inputs.copy(),
            status="Completed",
            wait_reason=None,
        )
        for key in ["valid", "other-group", "old-version", "fixture", "queued", "invalid"]
    ]
    tasks[1].input_versions["group_id"] = "default"
    tasks[2].input_versions["agent_version"] = "old"
    tasks[4].status = "Queued"
    tasks[5].status = "Waiting"
    tasks[5].wait_reason = "CandidateValidation"
    model = get_settings().gemini_model
    call = Row(
        model=model,
        price_version=price_for(model).version,
        request_fingerprint="test",
        status="Settled",
        output={"test": True},
    )

    class Database:
        counter = 0

        def scalars(self, query):
            self.counter += 1
            if self.counter == 1:
                return tasks
            return {2: [call], 3: [], 4: [], 5: [call]}[self.counter]

    result = observation(Database(), change, at)
    assert result["recorded_tasks"] == ["valid", "invalid"]
    assert result["completed_tasks"] == ["valid"]
    assert result["hard_rule_failures"] == ["invalid"]
    assert result["window_elapsed"] and not result["minimum_runs_met"]
