"""Persistent in-app notifications and deterministic scheduled summaries."""

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select

from app.admission import view
from app.database import transaction
from app.models import AuditEvent, Member, MemberGroup, Notification, Observation, ProfileSnapshot, User, now
from app.security import principal

router = APIRouter(prefix="/api/me/notifications")


@router.get("")
def notifications(user: User = Depends(principal)):
    with transaction() as db:
        return [
            view(row)
            for row in db.scalars(
                select(Notification)
                .where(Notification.user_id == user.id)
                .order_by(Notification.created_at.desc())
                .limit(100)
            )
        ]


@router.post("/{identifier}/read")
def mark_read(identifier: str, user: User = Depends(principal)):
    with transaction() as db:
        row = db.scalar(
            select(Notification)
            .where(Notification.id == identifier, Notification.user_id == user.id)
            .with_for_update()
        )
        if not row:
            raise HTTPException(404, "Notification not found.")
        if row.read_at is None:
            row.read_at = now()
            db.add(
                AuditEvent(actor_id=user.id, subject_id=row.id, event_type="notification.read", details={})
            )
        return view(row)


def deliver(db, member, key, title, body, kind):
    full_key = f"{member.id}:{key}"
    if db.scalar(select(Notification.id).where(Notification.business_key == full_key)):
        return 0
    db.add(Notification(user_id=member.user_id, business_key=full_key, title=title, body=body, kind=kind))
    db.flush()
    return 1


def generate_reminders(at=None):
    at = at or now()
    week = at.isocalendar()
    week_key = f"{week.year}-W{week.week:02d}"
    created = 0
    with transaction() as db:
        members = list(
            db.scalars(
                select(Member)
                .where(Member.status == "Accepted")
                .order_by(Member.id)
                .with_for_update(skip_locked=True)
            )
        )
        for member in members:
            group = db.get(MemberGroup, member.group_id)
            capabilities = group.configuration.get("capabilities", {}) if group else {}
            user = db.get(User, member.user_id)
            if not user or not user.active:
                continue
            snapshot = db.scalar(
                select(ProfileSnapshot).where(
                    ProfileSnapshot.member_id == member.id, ProfileSnapshot.version == member.profile_version
                )
            )
            if (
                snapshot
                and capabilities.get("reassessment_reminders") is True
                and snapshot.created_at <= at - timedelta(days=30)
            ):
                created += deliver(
                    db,
                    member,
                    f"reassessment:{member.profile_version}:{week_key}",
                    "Review your profile",
                    "Your confirmed profile is at least 30 days old. Review your goals, availability, and eligibility if anything has changed.",
                    "Reassessment",
                )
            latest = db.scalar(
                select(func.max(Observation.created_at)).where(
                    Observation.member_id == member.id,
                    Observation.kind.in_(["TrainingFeedback", "CycleFeedback"]),
                    Observation.status == "Confirmed",
                    Observation.created_at <= at,
                )
            )
            last_activity = latest or member.created_at
            if capabilities.get("continuity_reminders") is True and last_activity <= at - timedelta(days=7):
                created += deliver(
                    db,
                    member,
                    f"continuity:{week_key}",
                    "How is your routine going?",
                    "No training feedback has been recorded in the past seven days. Record your experience or ask for a plan review when you are ready.",
                    "Continuity",
                )
            if capabilities.get("weekly_summary") is True:
                counts = dict(
                    db.execute(
                        select(Observation.kind, func.count())
                        .where(
                            Observation.member_id == member.id,
                            Observation.status == "Confirmed",
                            Observation.created_at >= at - timedelta(days=7),
                            Observation.created_at <= at,
                        )
                        .group_by(Observation.kind)
                    ).all()
                )
                created += deliver(
                    db,
                    member,
                    f"weekly:{week_key}",
                    "Your weekly check-in",
                    f"In the past seven days you recorded {counts.get('TrainingFeedback', 0)} training feedback entries, {counts.get('CycleFeedback', 0)} training cycle reviews, and {counts.get('NutritionFeedback', 0)} nutrition feedback entries. These are recorded check-ins, not verified completed sessions.",
                    "WeeklySummary",
                )
    return created


if __name__ == "__main__":
    print(f"Created {generate_reminders()} notifications.")
