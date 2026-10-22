"""Configuration approval is bound to a candidate and its exact baseline."""

from copy import deepcopy
from uuid import uuid4

from fastapi.testclient import TestClient

from app.bootstrap import DEFAULT_CONFIGURATION, bootstrap
from app.database import transaction
from app.main import app
from app.models import MemberGroup
from app.settings import get_settings


def test_configuration_requires_approval_and_rejects_stale_baseline():
    bootstrap()
    with transaction() as db:
        group = MemberGroup(name=f"Configuration-{uuid4()}", configuration=DEFAULT_CONFIGURATION)
        db.add(group)
        db.flush()
        group_id = group.id
    with TestClient(app) as client:
        settings = get_settings()
        auth = client.post(
            "/api/auth/login",
            json={"email": settings.owner_email, "password": settings.owner_password.get_secret_value()},
        ).json()
        client.headers["x-csrf-token"] = auth["csrf_token"]
        candidate = deepcopy(DEFAULT_CONFIGURATION)
        candidate["review_required"] = True
        body = {"expected_version": 1, "configuration": candidate, "reason": "Enable candidate review"}
        first = client.post(f"/api/owner/groups/{group_id}/changes", json=body)
        assert first.status_code == 201
        first = first.json()
        second = client.post(f"/api/owner/groups/{group_id}/changes", json=body).json()
        assert client.post(f"/api/owner/changes/{first['id']}/execute").status_code == 409
        for change in (first, second):
            assert (
                client.post(
                    f"/api/owner/changes/{change['id']}/review",
                    json={"decision": "Approved", "fingerprint": change["fingerprint"]},
                ).status_code
                == 200
            )
        execute_url = f"/api/owner/changes/{first['id']}/execute"
        assert client.post(execute_url).json()["execution"]["group_version"] == 2
        assert client.post(execute_url).json()["execution"]["group_version"] == 2
        assert client.post(f"/api/owner/changes/{second['id']}/execute").status_code == 409
        invalid = deepcopy(candidate)
        invalid["field_permissions"] = {"TechnicalManager": ["age"]}
        assert (
            client.post(
                f"/api/owner/groups/{group_id}/changes",
                json=body | {"expected_version": 2, "configuration": invalid},
            ).status_code
            == 422
        )
