"""Reminder generation is idempotent and respects feature switches."""

from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.database import transaction
from app.models import Member, MemberGroup, Notification, Observation, ProfileSnapshot, User, now
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


@pytest.mark.parametrize(
    "kind,status,age_days,expect_reminder",
    [
        ("CycleFeedback", "Confirmed", 1, False),
        ("TrainingFeedback", "Confirmed", 1, False),
        ("NutritionFeedback", "Confirmed", 1, True),
        ("CycleFeedback", "Draft", 1, True),
        ("CycleFeedback", "Confirmed", 8, True),
        ("CycleFeedback", "Confirmed", -1, True),
    ],
)
def test_continuity_uses_confirmed_training_checkins(kind, status, age_days, expect_reminder):
    at = now().replace(microsecond=0)
    with transaction() as db:
        user = User(
            email=f"cycle-notice-{uuid4()}@example.test",
            display_name="Cycle Check-in",
            password_hash="Unused",
        )
        group = MemberGroup(
            name=f"Cycle-notices-{uuid4()}",
            configuration={"capabilities": {"continuity_reminders": True, "weekly_summary": True}},
        )
        db.add_all([user, group])
        db.flush()
        member = Member(user_id=user.id, group_id=group.id, created_at=at - timedelta(days=40))
        db.add(member)
        db.flush()
        db.add(
            Observation(
                member_id=member.id,
                kind=kind,
                status=status,
                content={},
                created_at=at - timedelta(days=age_days),
            )
        )
        user_id = user.id
    generate_reminders(at)
    generate_reminders(at)
    with transaction() as db:
        rows = list(db.scalars(select(Notification).where(Notification.user_id == user_id)))
        assert sum(row.kind == "Continuity" for row in rows) == int(expect_reminder)
        summaries = [row for row in rows if row.kind == "WeeklySummary"]
        assert len(summaries) == 1
        cycle_count = int(kind == "CycleFeedback" and status == "Confirmed" and 0 <= age_days <= 7)
        assert f"{cycle_count} training cycle reviews" in summaries[0].body
        assert "not verified completed sessions" in summaries[0].body
