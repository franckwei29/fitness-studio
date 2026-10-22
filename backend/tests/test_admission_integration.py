"""Admission invariants verified through HTTP against MySQL."""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.bootstrap import bootstrap
from app.main import app
from app.rules import EXCLUSIONS
from app.settings import get_settings


@pytest.fixture
def member():
    bootstrap()
    with TestClient(app) as client:
        response = client.post(
            "/api/auth/register",
            json={
                "email": f"admission-{uuid4()}@example.test",
                "password": "Integration-test-123!",
                "display_name": "Applicant",
            },
        )
        assert response.status_code == 201
        client.headers["x-csrf-token"] = response.json()["csrf_token"]
        yield client


@pytest.fixture
def owner_client():
    bootstrap()
    settings = get_settings()
    with TestClient(app) as client:
        response = client.post(
            "/api/auth/login",
            json={
                "email": settings.owner_email,
                "password": settings.owner_password.get_secret_value(),
            },
        )
        assert response.status_code == 200
        client.headers["x-csrf-token"] = response.json()["csrf_token"]
        yield client


def facts(**changes):
    return (
        dict(
            age=28,
            goal="Build strength",
            experience="Beginner",
            available_days=3,
            session_minutes=45,
            equipment=["Dumbbells"],
            dietary_restrictions=[],
            **{key: False for key in EXCLUSIONS},
        )
        | changes
    )


def submit(client, values):
    assessment = client.post("/api/assessments").json()
    response = client.post(
        f"/api/assessments/{assessment['id']}/confirm",
        json={
            "expected_version": assessment["version"],
            "facts": values,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_acceptance_is_idempotent_and_profile_is_versioned(member):
    assert member.get("/api/me/profile").json()["member"] is None
    assessment = submit(member, facts())
    assert assessment["status"] == "Accepted"
    response = member.post(
        f"/api/assessments/{assessment['id']}/confirm",
        json={
            "expected_version": 1,
            "facts": facts(),
        },
    )
    assert response.status_code == 200
    assert member.get("/api/me/profile").json()["snapshot"]["version"] == 1
    submit(member, facts(available_days=4))
    profile = member.get("/api/me/profile").json()
    assert profile["snapshot"]["version"] == 2
    assert profile["snapshot"]["facts"]["available_days"] == 4


@pytest.mark.parametrize(
    "values,status",
    [
        (facts(age=17), "ReferredOut"),
        ({"age": 28}, "NeedsInformation"),
    ],
)
def test_ineligible_or_incomplete_does_not_create_member(member, values, status):
    assert submit(member, values)["status"] == status
    assert member.get("/api/me/profile").json()["member"] is None


def test_owner_approval_requires_exact_current_facts(member, owner_client):
    assessment = submit(member, facts(resolved_past_injury=True))
    assert assessment["status"] == "PendingOwnerReview"
    assert member.get("/api/me/profile").json()["member"] is None
    assert member.get("/api/owner/approvals").status_code == 403
    approval = next(
        row
        for row in owner_client.get("/api/owner/approvals").json()
        if row["subject_id"] == assessment["id"]
    )
    endpoint = f"/api/owner/approvals/{approval['id']}/decisions"
    assert (
        owner_client.post(
            endpoint,
            json={
                "decision": "Approved",
                "fingerprint": "incorrect",
            },
        ).status_code
        == 409
    )
    body = {"decision": "Approved", "fingerprint": approval["fingerprint"]}
    assert owner_client.post(endpoint, json=body).status_code == 200
    assert owner_client.post(endpoint, json=body).status_code == 200
    assert member.get("/api/me/profile").json()["snapshot"]["version"] == 1


def test_changed_assessment_invalidates_previous_approval(member, owner_client):
    assessment = submit(member, facts(resolved_past_injury=True))
    approval = next(
        row
        for row in owner_client.get("/api/owner/approvals").json()
        if row["subject_id"] == assessment["id"]
    )
    assert (
        member.post(
            f"/api/assessments/{assessment['id']}/confirm",
            json={
                "expected_version": assessment["version"],
                "facts": facts(resolved_past_injury=True, available_days=4),
            },
        ).status_code
        == 200
    )
    assert (
        owner_client.post(
            f"/api/owner/approvals/{approval['id']}/decisions",
            json={
                "decision": "Approved",
                "fingerprint": approval["fingerprint"],
            },
        ).status_code
        == 409
    )
    assert member.get("/api/me/profile").json()["member"] is None
    assert owner_client.get(f"/api/assessments/{assessment['id']}").status_code == 404
