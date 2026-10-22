"""Changing service groups invalidates context without editing confirmed facts."""

from datetime import timedelta
from uuid import uuid4

from fastapi.testclient import TestClient

from app.bootstrap import bootstrap
from app.database import transaction
from app.main import app
from app.models import Member, MemberGroup, ProfileSnapshot, Request, User, WorkItem, now
from app.settings import get_settings


def test_reassignment_preserves_facts_and_invalidates_queued_context():
    bootstrap()
    with transaction() as db:
        user = User(
            email=f"group-switch-{uuid4()}@example.test", display_name="Group Test", password_hash="Unused"
        )
        groups = [MemberGroup(name=f"Group-{uuid4()}", configuration={}) for _ in range(2)]
        db.add_all([user, *groups])
        db.flush()
        member = Member(user_id=user.id, group_id=groups[0].id)
        db.add(member)
        db.flush()
        db.add(
            ProfileSnapshot(
                member_id=member.id,
                version=1,
                facts={"goal": "Confirmed goal"},
                sources={"test": True},
                confirmed_by=user.id,
            )
        )
        request = Request(user_id=user.id, kind="Training", deadline=now() + timedelta(hours=1))
        db.add(request)
        db.flush()
        work = WorkItem(
            request_id=request.id,
            role="Coach",
            subject_kind="Test",
            subject_id=member.id,
            input_versions={"profile": 1, "group_id": groups[0].id},
        )
        db.add(work)
        db.flush()
        member_id, work_id, request_id = member.id, work.id, request.id
        old_group, new_group = groups[0].id, groups[1].id
    with TestClient(app) as client:
        settings = get_settings()
        auth = client.post(
            "/api/auth/login",
            json={"email": settings.owner_email, "password": settings.owner_password.get_secret_value()},
        ).json()
        client.headers["x-csrf-token"] = auth["csrf_token"]
        body = {"group_id": new_group, "expected_group_id": old_group, "reason": "Change service scope"}
        assert client.post(f"/api/owner/members/{member_id}/group", json=body).status_code == 200
        assert client.post(f"/api/owner/members/{member_id}/group", json=body).status_code == 409
    with transaction() as db:
        assert db.get(Member, member_id).profile_version == 2
        assert db.get(Member, member_id).group_id == new_group
        assert db.get(WorkItem, work_id).wait_reason == "PolicyReview"
        assert db.get(Request, request_id).status == "Waiting"
        from sqlalchemy import select

        snapshot = db.scalar(
            select(ProfileSnapshot).where(
                ProfileSnapshot.member_id == member_id, ProfileSnapshot.version == 2
            )
        )
        assert snapshot.facts == {"goal": "Confirmed goal"}
        assert snapshot.sources["previous_group_id"] == old_group
