"""Injected release records test atomic rollback mechanics, not live model quality."""

from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.admission import fingerprint
from app.bootstrap import DEFAULT_CONFIGURATION, bootstrap
from app.database import transaction
from app.main import app
from app.models import AgentVersion, Approval, ChangeRequest, MemberGroup, now
from app.settings import get_settings


@pytest.mark.parametrize("automatic", [False, True])
@pytest.mark.parametrize("newer_change", [False, True])
def test_rollback_restores_pinned_baseline_and_refuses_newer_configuration(
    newer_change, automatic, monkeypatch
):
    bootstrap()
    settings = get_settings()
    with transaction() as db:
        versions = []
        for label in ["Retained", "Candidate"]:
            values = {"role": "Coach", "model": settings.gemini_model, "prompt": f"{label}-{uuid4()}"}
            version = AgentVersion(label=label, fingerprint=fingerprint(values), **values)
            db.add(version)
            versions.append(version)
        db.flush()
        baseline = {"version": 1, "configuration": DEFAULT_CONFIGURATION}
        config = DEFAULT_CONFIGURATION | {"agent_versions": {"Coach": versions[1].id}}
        group = MemberGroup(
            name=f"Rollback-{uuid4()}", version=3 if newer_change else 2, configuration=config
        )
        db.add(group)
        db.flush()
        candidate = {
            "configuration": config,
            "role": "Coach",
            "agent_version_id": versions[1].id,
            "rollback_version_id": versions[0].id,
            "rollback_fingerprint": versions[0].fingerprint,
            "rollback_on_hard_rule_failure": True,
            "error_threshold": 1,
        }
        digest = fingerprint({"group_id": group.id, "baseline": baseline, "candidate": candidate})
        change = ChangeRequest(
            kind="AgentRelease",
            group_id=group.id,
            baseline=baseline,
            candidate=candidate,
            fingerprint=digest,
            status="Canary",
            reason="Injected rollback test",
            execution={"group_version": 2, "started_at": now().isoformat()},
        )
        db.add(change)
        db.flush()
        db.add(
            Approval(
                subject_kind="AgentRelease",
                subject_id=change.id,
                subject_version=1,
                fingerprint=digest,
                decision="Approved",
                scope={"group_id": group.id},
                context={},
                expires_at=now() - timedelta(hours=1),
            )
        )
        group_id, change_id, baseline_id = group.id, change.id, versions[0].id
    if automatic:
        from app.canary_monitor import monitor_canaries

        monkeypatch.setattr(
            "app.canary_monitor.observation",
            lambda *args: {
                "hard_rule_failures": ["injected-test-failure"],
                "execution_failures": [],
                "window_elapsed": False,
                "minimum_runs_met": False,
            },
        )
        assert monitor_canaries() >= 1
        with transaction() as db:
            change = db.get(ChangeRequest, change_id)
            assert change.status == ("RollbackBlocked" if newer_change else "RolledBack")
            if newer_change:
                assert "rollback" not in change.execution
                change.status = "Stopped"
            else:
                assert change.execution["rollback"]["verified"]
                assert db.get(MemberGroup, group_id).configuration["agent_versions"]["Coach"] == baseline_id
        return
    with TestClient(app) as client:
        auth = client.post(
            "/api/auth/login",
            json={"email": settings.owner_email, "password": settings.owner_password.get_secret_value()},
        ).json()
        client.headers["x-csrf-token"] = auth["csrf_token"]
        response = client.post(
            f"/api/owner/releases/{change_id}/rollback", json={"reason": "Restore evaluated baseline"}
        )
        assert response.status_code == (409 if newer_change else 200)
        if not newer_change:
            assert response.json()["execution"]["rollback"]["verified"]
            repeated = client.post(
                f"/api/owner/releases/{change_id}/rollback", json={"reason": "Repeated same rollback"}
            )
            assert repeated.status_code == 200
    with transaction() as db:
        group = db.get(MemberGroup, group_id)
        change = db.get(ChangeRequest, change_id)
        assert group.version == 3
        if newer_change:
            assert change.status == "Canary"
            assert group.configuration["agent_versions"]["Coach"] != baseline_id
            # This injected record must not later be treated as a real canary.
            change.status = "Stopped"
        else:
            assert change.status == "RolledBack"
            assert group.configuration["agent_versions"]["Coach"] == baseline_id
