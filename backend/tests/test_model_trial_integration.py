"""Isolated database checks; no live model quality claims."""

from uuid import uuid4

from sqlalchemy import select
from test_admission_integration import owner_client as owner_fixture

from app.bootstrap import DEFAULT_CONFIGURATION
from app.database import transaction
from app.models import AgentVersion, Member, MemberGroup, ProfileSnapshot, User
from app.settings import get_settings
from app.version_admin import base_instruction

owner_client = owner_fixture


def test_current_comparison_before_first_coach_request(owner_client, monkeypatch):
    instruction = base_instruction("Coach") + f"\nTest installation {uuid4()}"
    monkeypatch.setattr("app.version_admin.base_instruction", lambda role: instruction)
    with transaction() as db:
        group = MemberGroup(name=f"Fresh-{uuid4()}", configuration=DEFAULT_CONFIGURATION)
        db.add(group)
        db.flush()
        identifier = group.id
        assert not db.scalar(select(AgentVersion).where(AgentVersion.prompt == instruction))
    other = "gpt-6-luna" if get_settings().default_model != "gpt-6-luna" else "gemini-3.5-flash-lite"
    response = owner_client.post(
        "/api/owner/agent-versions/compare-current",
        json={"group_id": identifier, "role": "Coach", "model": other},
    )
    assert response.status_code == 201, response.text
    run = response.json()
    with transaction() as db:
        current = db.get(AgentVersion, run["baseline_id"])
        candidate = db.get(AgentVersion, run["candidate_id"])
        assert current.model == get_settings().default_model
        assert candidate.model == other
        assert current.prompt == candidate.prompt == instruction
        assert db.get(MemberGroup, identifier).configuration == DEFAULT_CONFIGURATION
    repeated = owner_client.post(
        "/api/owner/agent-versions/compare-current",
        json={"group_id": identifier, "role": "Coach", "model": other},
    ).json()
    assert repeated["baseline_id"] == run["baseline_id"]
    assert repeated["candidate_id"] == run["candidate_id"]
    assert run["status"] == "Prepared"


def test_bulk_assignment_is_atomic_and_supports_multiple_members(owner_client):
    with transaction() as db:
        groups = [MemberGroup(name=f"Bulk-{uuid4()}", configuration=DEFAULT_CONFIGURATION) for _ in range(2)]
        db.add_all(groups)
        db.flush()
        source, target = [g.id for g in groups]
        ids = []
        for _ in range(4):
            account = User(
                email=f"bulk-{uuid4()}@example.test", display_name="Trial member", password_hash="Unused"
            )
            db.add(account)
            db.flush()
            member = Member(user_id=account.id, group_id=source)
            db.add(member)
            db.flush()
            ids.append(member.id)
            db.add(
                ProfileSnapshot(
                    member_id=member.id,
                    version=1,
                    facts={"goal": "Fitness"},
                    sources={},
                    confirmed_by=account.id,
                )
            )
    body = {
        "group_id": target,
        "expected_group_version": 1,
        "reason": "Try the new Coach model",
        "members": [{"member_id": id, "expected_group_id": source} for id in sorted(ids)],
    }
    broken = {**body, "members": [dict(row) for row in body["members"]]}
    broken["members"][-1]["expected_group_id"] = "stale-group"
    assert owner_client.post("/api/owner/members/assign-group", json=broken).status_code == 409
    with transaction() as db:
        assert all(
            db.get(Member, id).group_id == source and db.get(Member, id).profile_version == 1 for id in ids
        )
    assert (
        owner_client.post(
            "/api/owner/members/assign-group", json={**body, "expected_group_version": 99}
        ).status_code
        == 409
    )
    response = owner_client.post("/api/owner/members/assign-group", json=body)
    assert response.status_code == 200, response.text
    assert response.json()["assigned_count"] == 4
    with transaction() as db:
        assert all(
            db.get(Member, id).group_id == target and db.get(Member, id).profile_version == 2 for id in ids
        )
