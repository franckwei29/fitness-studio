"""Reminder generation is idempotent and respects feature switches."""

from datetime import timedelta
from uuid import uuid4

from sqlalchemy import select

from app.database import transaction
from app.models import Member, MemberGroup, Notification, ProfileSnapshot, User, now
from app.notifications import generate_reminders


def test_reminder_batch_is_deduplicated_and_group_scoped():
    at = now()
    with transaction() as db:
        user = User(
            email=f"notice-{uuid4()}@example.test", display_name="Notice Test", password_hash="Unused"
        )
        group = MemberGroup(
            name=f"Notices-{uuid4()}",
            configuration={
                "capabilities": {
                    "reassessment_reminders": True,
                    "continuity_reminders": True,
                    "weekly_summary": False,
                }
            },
        )
        db.add_all([user, group])
        db.flush()
        member = Member(user_id=user.id, group_id=group.id, created_at=at - timedelta(days=40))
        db.add(member)
        db.flush()
        db.add(
            ProfileSnapshot(
                member_id=member.id,
                version=1,
                facts={},
                sources={},
                confirmed_by=user.id,
                created_at=at - timedelta(days=40),
            )
        )
        user_id = user.id
    generate_reminders(at)
    generate_reminders(at)
    with transaction() as db:
        rows = list(db.scalars(select(Notification).where(Notification.user_id == user_id)))
        assert sorted(row.kind for row in rows) == ["Continuity", "Reassessment"]
        assert all(row.read_at is None for row in rows)
