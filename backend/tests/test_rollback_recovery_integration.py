"""Synthetic records verify fresh rollback approval and independent recovery evidence."""

from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.admission import fingerprint
from app.bootstrap import DEFAULT_CONFIGURATION
from app.database import transaction
from app.main import app
from app.models import (
    AgentVersion,
    Approval,
    ChangeRequest,
    Incident,
    MemberGroup,
    ModelCall,
    Request,
    WorkItem,
    now,
)
from app.pricing import price_for
from app.settings import get_settings


@pytest.fixture
def blocked_release():
    with transaction() as db:
        versions = []
        for label in ("Retained", "Failed"):
            values = {"role": "Coach", "model": get_settings().gemini_model, "prompt": f"{label}-{uuid4()}"}
            version = AgentVersion(label=label, fingerprint=fingerprint(values), **values)
            db.add(version)
            versions.append(version)
        db.flush()
        old = DEFAULT_CONFIGURATION | {"agent_versions": {"Coach": versions[1].id}}
        newer = old | {"review_required": True}
        group = MemberGroup(name=f"Recovery-{uuid4()}", version=3, configuration=newer)
        db.add(group)
        db.flush()
        baseline = {"version": 1, "configuration": DEFAULT_CONFIGURATION}
        candidate = {
            "configuration": old,
            "role": "Coach",
            "agent_version_id": versions[1].id,
            "rollback_version_id": versions[0].id,
            "rollback_fingerprint": versions[0].fingerprint,
        }
        digest = fingerprint({"group_id": group.id, "baseline": baseline, "candidate": candidate})
        release = ChangeRequest(
            kind="AgentRelease",
            group_id=group.id,
            baseline=baseline,
            candidate=candidate,
            fingerprint=digest,
            status="RollbackBlocked",
            reason="Synthetic rollback conflict",
            execution={"group_version": 2, "started_at": now().isoformat()},
        )
        db.add(release)
        db.flush()
        db.add(
            Approval(
                subject_kind="AgentRelease",
                subject_id=release.id,
                subject_version=1,
                fingerprint=digest,
                decision="Approved",
                scope={},
                context={},
                expires_at=now() - timedelta(hours=1),
            )
        )
        incident = Incident(
            event_key=str(uuid4()),
            title="Synthetic release failure",
            evidence={
                "release_id": release.id,
                "group_id": group.id,
                "role": "Coach",
                "reason": "RollbackBlocked",
            },
        )
        db.add(incident)
        db.flush()
        return {"release": release.id, "group": group.id, "retained": versions[0].id, "incident": incident.id}


@pytest.mark.parametrize("conflict", [None, "Group", "Version", "Evidence", "RemovedEvidence"])
def test_recovery_requires_fresh_approval_and_preserves_newer_settings(blocked_release, conflict):
    ids = blocked_release
    settings = get_settings()
    with TestClient(app) as client:
        auth = client.post(
            "/api/auth/login",
            json={"email": settings.owner_email, "password": settings.owner_password.get_secret_value()},
        ).json()
        client.headers["x-csrf-token"] = auth["csrf_token"]
        endpoint = f"/api/owner/releases/{ids['release']}/recovery-proposals"
        assert (
            client.post(
                endpoint, json={"expected_group_version": 2, "reason": "Restore retained role only"}
            ).status_code
            == 409
        )
        response = client.post(
            endpoint, json={"expected_group_version": 3, "reason": "Restore retained role only"}
        )
        assert response.status_code == 201
        change = response.json()
        assert change["candidate"]["review_required"] is True
        assert change["candidate"]["agent_versions"]["Coach"] == ids["retained"]
        execute_url = f"/api/owner/changes/{change['id']}/execute"
        assert client.post(execute_url, json={}).status_code == 409
        assert (
            client.post(
                f"/api/owner/changes/{change['id']}/review",
                json={
                    "decision": "Approved",
                    "fingerprint": change["fingerprint"],
                    "comment": "Reviewed current settings and retained role",
                },
            ).status_code
            == 200
        )
        if conflict:
            with transaction() as db:
                if conflict == "Group":
                    db.get(MemberGroup, ids["group"]).version += 1
                elif conflict == "Version":
                    db.get(AgentVersion, ids["retained"]).prompt = "Modified after approval"
                else:
                    row = db.get(ChangeRequest, change["id"])
                    row.evaluation = (
                        {}
                        if conflict == "RemovedEvidence"
                        else row.evaluation | {"retained_fingerprint": "Changed"}
                    )
            assert client.post(execute_url, json={}).status_code == 409
            with transaction() as db:
                assert db.get(ChangeRequest, ids["release"]).status == "RollbackBlocked"
            return
        result = client.post(execute_url, json={})
        assert result.status_code == 200
        assert client.post(execute_url, json={}).json() == result.json()
        with transaction() as db:
            group = db.get(MemberGroup, ids["group"])
            assert group.version == 4
            assert group.configuration["review_required"] is True
            assert group.configuration["agent_versions"]["Coach"] == ids["retained"]
            release = db.get(ChangeRequest, ids["release"])
            assert release.status == "RolledBack" and release.execution["rollback"]["verified"]
            assert db.get(Incident, ids["incident"]).outcome == "Unresolved"
        verify_url = f"/api/owner/incidents/{ids['incident']}/verify-recovery"
        assert client.post(verify_url, json={}).status_code == 409
        with transaction() as db:
            request = Request(
                user_id=auth["user"]["id"],
                kind="Chat",
                status="Completed",
                deadline=now() + timedelta(hours=1),
            )
            db.add(request)
            db.flush()
            task = WorkItem(
                request_id=request.id,
                role="Coach",
                subject_kind="Conversation",
                subject_id="synthetic",
                status="Completed",
                input_versions={"group_id": "unrelated-group", "group": 4, "agent_version": ids["retained"]},
            )
            db.add(task)
            db.add(
                ModelCall(
                    request_id=request.id,
                    user_id=request.user_id,
                    role="Coach",
                    model=settings.gemini_model,
                    status="Settled",
                    reserved_micros=0,
                    actual_micros=1,
                    price_version=price_for(settings.gemini_model).version,
                    request_fingerprint="synthetic-evidence",
                    output={"reply": "Synthetic recovery evidence"},
                )
            )
            db.flush()
            task_id = task.id
        assert client.post(verify_url, json={}).status_code == 409
        with transaction() as db:
            task = db.get(WorkItem, task_id)
            task.input_versions = task.input_versions | {"group_id": ids["group"]}
        recovered = client.post(verify_url, json={})
        assert recovered.status_code == 200
        assert recovered.json()["outcome"] == "Recovered"
        assert recovered.json()["recovery"]["service_restored"] is True
