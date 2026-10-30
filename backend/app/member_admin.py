"""Owner membership operations without exposing health profile contents."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.call_limits import effective_account, policy_lock
from app.database import transaction
from app.models import AuditEvent, Member, MemberGroup, ProfileSnapshot, Request, RequestEvent, User, WorkItem
from app.security import owner

router = APIRouter(prefix="/api/owner/members")


@router.get("")
def members(user: User = Depends(owner)):
    with transaction() as db:
        rows = db.execute(
            select(Member, User)
            .join(User, User.id == Member.user_id)
            .order_by(Member.created_at.desc())
            .limit(200)
        ).all()
        return [
            {
                "id": member.id,
                "display_name": account.display_name,
                "status": member.status,
                "group_id": member.group_id,
                "profile_version": member.profile_version,
                "created_at": member.created_at,
                "allowance": effective_account(db, account.id),
            }
            for member, account in rows
        ]


@router.get("/{identifier}")
def member_detail(identifier: str, user: User = Depends(owner)):
    with transaction() as db:
        member = db.get(Member, identifier)
        account = db.get(User, member.user_id) if member else None
        if not member or not account:
            raise HTTPException(404, "Member not found.")
        return {
            "id": member.id,
            "display_name": account.display_name,
            "status": member.status,
            "group_id": member.group_id,
            "profile_version": member.profile_version,
            "created_at": member.created_at,
            "allowance": effective_account(db, account.id),
        }


class Assignment(BaseModel):
    group_id: str
    expected_group_id: str
    reason: str = Field(min_length=1, max_length=1000)


@router.post("/{identifier}/group")
def assign_group(identifier: str, body: Assignment, user: User = Depends(owner)):
    with transaction() as db:
        policy_lock(db)
        member = db.scalar(select(Member).where(Member.id == identifier).with_for_update())
        if not member:
            raise HTTPException(404, "Member not found.")
        if member.group_id != body.expected_group_id:
            raise HTTPException(409, "The member group changed. Refresh before editing.")
        group = db.get(MemberGroup, body.group_id)
        if not group:
            raise HTTPException(404, "Group not found.")
        previous = member.group_id
        if previous == group.id:
            return {
                "member_id": member.id,
                "group_id": member.group_id,
                "status": member.status,
                "allowance": effective_account(db, member.user_id),
                "usage_unchanged": True,
            }
        snapshot = db.scalar(
            select(ProfileSnapshot).where(
                ProfileSnapshot.member_id == member.id, ProfileSnapshot.version == member.profile_version
            )
        )
        if not snapshot:
            raise HTTPException(409, "The confirmed profile snapshot is unavailable.")
        member.profile_version += 1
        db.add(
            ProfileSnapshot(
                member_id=member.id,
                version=member.profile_version,
                facts=snapshot.facts,
                sources=snapshot.sources
                | {"context_change": "MemberGroup", "previous_group_id": previous, "group_id": group.id},
                confirmed_by=snapshot.confirmed_by,
            )
        )
        member.group_id = group.id
        db.add(
            AuditEvent(
                actor_id=user.id,
                event_type="member.group_changed",
                subject_id=member.id,
                details={"previous_group_id": previous, "group_id": group.id, "reason": body.reason},
            )
        )
        # Queued tasks must not silently acquire a different policy context.
        requests = list(
            db.scalars(
                select(Request).where(
                    Request.user_id == member.user_id, Request.status.in_(["Queued", "Waiting"])
                )
            )
        )
        for request in requests:
            for item in db.scalars(
                select(WorkItem).where(
                    WorkItem.request_id == request.id, WorkItem.status.in_(["Queued", "Waiting"])
                )
            ):
                item.status = "Waiting"
                item.wait_reason = "PolicyReview"
            request.status = "Waiting"
            db.add(
                RequestEvent(
                    request_id=request.id,
                    event_type="policy_changed",
                    payload={"status": "Waiting", "reason": "Member group changed."},
                )
            )
        return {
            "member_id": member.id,
            "group_id": member.group_id,
            "status": member.status,
            "allowance": effective_account(db, member.user_id),
            "usage_unchanged": True,
        }
