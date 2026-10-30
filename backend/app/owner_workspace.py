"""Authenticated local owner work cards and audit-backed conversation receipts.

These projections never enter a model prompt. Cards identify records; mutations
continue to use the existing service handlers and their exact-version checks.
"""

import re
from datetime import timedelta
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from app.admission import approval_basis, fingerprint
from app.budget_admin import budget_view
from app.database import transaction
from app.models import (
    AgentVersion,
    Approval,
    Assessment,
    AuditEvent,
    BudgetAccount,
    ChangeRequest,
    ChatSession,
    EvaluationRun,
    IdempotencyRecord,
    Incident,
    Member,
    MemberGroup,
    Message,
    ModelCall,
    Notification,
    PlanPair,
    PlanVersion,
    Request,
    RequestEvent,
    User,
    now,
)
from app.security import owner

router = APIRouter(prefix="/api/owner")
Area = Literal[
    "attention",
    "approvals",
    "members",
    "budgets",
    "groups",
    "incidents",
    "versions",
    "releases",
    "dashboard",
    "requests",
    "notifications",
    "usage",
]
TITLES = {
    "attention": "Your attention inbox",
    "approvals": "Review queue",
    "members": "Members",
    "budgets": "Budget limits",
    "groups": "Member groups",
    "incidents": "Incidents",
    "versions": "Agent versions and comparisons",
    "releases": "Agent releases",
    "dashboard": "Studio overview",
    "requests": "Your activity",
    "notifications": "Updates",
    "usage": "AI usage",
}
SUMMARIES = {
    "attention": "The same decisions appear in Updates. Open a card to see the exact next step.",
    "approvals": "Review the submitted evidence before recording a decision. Approval and applying a change are separate steps.",
    "members": "Choose a member to inspect their status or preview a group change.",
    "budgets": "Review limits, spend and reserved funds. Changing a limit does not change the model-call allowances.",
    "groups": "Open a group to edit its settings, or an exact proposal to review or apply it.",
    "incidents": "Open an incident for evidence, recovery prerequisites and its valid next actions.",
    "versions": "Saving instructions is separate from live comparison, human review and release.",
    "releases": "Open the exact release for evidence, approval, execution, observation or recovery.",
    "dashboard": "Review studio activity or open an exact request for its redacted operational evidence.",
    "requests": "These are your own activities. Member confirmations cannot be completed by the owner.",
    "notifications": "Your updates link to the corresponding workspace. Owner reviews are in the attention inbox.",
    "usage": "The per-model hourly and account rolling 24-hour allowances are separate from monetary budgets. Reserved funds remain held when usage is uncertain.",
}


def card(identifier, category, title, status, description, page, *, section=None, action=None, **extra):
    return {
        "id": identifier,
        "category": category,
        "title": title,
        "status": status,
        "description": description,
        "target": {"page": page, "id": identifier, **({"section": section} if section else {})},
        **({"action": action} if action else {}),
        **extra,
    }


def account_name(db, user_id):
    account = db.get(User, user_id) if user_id else None
    return account.display_name if account else "Member"


def group_name(db, group_id):
    group = db.get(MemberGroup, group_id) if group_id else None
    return group.name if group else "Unavailable group"


def approval_title(db, row):
    labels = {
        "IntakeAssessment": "Membership application",
        "TrainingPlan": "Training plan",
        "NutritionPlan": "Nutrition plan",
        "PlanPair": "Training and meal plans",
        "GroupConfiguration": "Group configuration",
        "AgentRelease": "Agent release",
    }
    name = None
    if row.subject_kind == "IntakeAssessment":
        assessment = db.get(Assessment, row.subject_id)
        name = account_name(db, assessment.user_id) if assessment else None
    elif row.subject_kind in {"TrainingPlan", "NutritionPlan", "PlanPair"}:
        subject = db.get(PlanPair if row.subject_kind == "PlanPair" else PlanVersion, row.subject_id)
        member = db.get(Member, subject.member_id) if subject else None
        name = account_name(db, member.user_id) if member else None
    elif row.subject_kind in {"GroupConfiguration", "AgentRelease"}:
        change = db.get(ChangeRequest, row.subject_id)
        name = group_name(db, change.group_id) if change else None
    return labels.get(row.subject_kind, "Review") + (f" · {name}" if name else "")


def approval_current(db, row):
    """A read projection of the service prerequisites, never an authorization token."""
    if row.expires_at <= now():
        return False
    if row.subject_kind == "IntakeAssessment":
        assessment = db.get(Assessment, row.subject_id)
        return bool(
            assessment
            and assessment.status == "PendingOwnerReview"
            and approval_basis(assessment) == row.fingerprint
        )
    if row.subject_kind in {"TrainingPlan", "NutritionPlan", "PlanPair"}:
        from app.activation import pair_fingerprint, plan_fingerprint

        if row.subject_kind == "PlanPair":
            pair = db.get(PlanPair, row.subject_id)
            if not pair or pair.status != "Candidate":
                return False
            plans = [db.get(PlanVersion, pair.training_id), db.get(PlanVersion, pair.nutrition_id)]
            if not all(plans) or pair_fingerprint(*plans) != row.fingerprint:
                return False
        else:
            plan = db.get(PlanVersion, row.subject_id)
            if not plan or plan.status != "Validated" or plan_fingerprint(plan) != row.fingerprint:
                return False
            plans = [plan]
        member = db.get(Member, plans[0].member_id)
        group = db.get(MemberGroup, member.group_id) if member else None
        return bool(
            member
            and group
            and member.status == "Accepted"
            and all(
                p.profile_version == member.profile_version and p.config_version == group.version
                for p in plans
            )
        )
    if row.subject_kind in {"GroupConfiguration", "AgentRelease"}:
        change = db.get(ChangeRequest, row.subject_id)
        group = db.get(MemberGroup, change.group_id) if change else None
        if not change or not group or change.status not in {"PendingApproval", "Approved"}:
            return False
        digest = fingerprint(
            {"group_id": change.group_id, "baseline": change.baseline, "candidate": change.candidate}
        )
        if (
            digest != row.fingerprint
            or change.fingerprint != row.fingerprint
            or group.version != change.baseline.get("version")
            or group.configuration != change.baseline.get("configuration")
        ):
            return False
        if row.subject_kind == "AgentRelease":
            from app.agent_release import check_proposal
            from app.policy import PolicyDenied
            from app.pricing import UsageUncertain

            try:
                check_proposal(db, change)
            except (PolicyDenied, UsageUncertain, KeyError):
                return False
        return True
    return False


def approval_item(db, row):
    current = approval_current(db, row) if row.decision == "Pending" else False
    status = row.decision
    if status == "Pending":
        status = (
            "Needs your review" if current else "Expired" if row.expires_at <= now() else "Needs refreshing"
        )
    return card(
        row.id,
        "Membership" if row.subject_kind == "IntakeAssessment" else "Review",
        approval_title(db, row),
        status,
        "Open the submitted evidence and record your decision."
        if current
        else "View the recorded review and its current status.",
        "approvals",
        action="approval" if current else None,
        subject_kind=row.subject_kind,
        subject_id=row.subject_id,
        fingerprint=row.fingerprint,
        created_at=row.created_at,
        responsibility="owner_review" if current else "history",
    )


def change_item(db, row):
    release = row.kind == "AgentRelease"
    eligible = False
    if row.status == "Approved":
        approval = db.scalar(
            select(Approval).where(
                Approval.subject_id == row.id,
                Approval.decision == "Approved",
                Approval.fingerprint == row.fingerprint,
                Approval.expires_at > now(),
            )
        )
        eligible = bool(approval and approval_current(db, approval))
    return card(
        row.id,
        "Release" if release else "Configuration",
        f"{'Agent release' if release else 'Configuration change'} · {group_name(db, row.group_id)}",
        "Ready to apply" if eligible else "Needs refreshing" if row.status == "Approved" else row.status,
        row.reason,
        "releases" if release else "groups",
        section=None if release else "change",
        action="apply-configuration" if eligible and not release else None,
        created_at=row.created_at,
        responsibility="ready_to_apply" if eligible else "history",
    )


def incident_item(row):
    return card(
        row.id,
        "Recovery",
        row.title,
        row.status,
        "Review recovery steps and evidence. Closing an unresolved incident does not restore service.",
        "incidents",
        created_at=row.created_at,
        responsibility="needs_attention",
    )


def usage_items(db):
    from app.usage_admin import release_allowed

    return [
        card(
            row.id,
            "Usage",
            f"Uncertain {row.role} usage",
            "Cost unconfirmed",
            "The provider cost is unconfirmed. Releasing a stopped local slot keeps the reserved funds.",
            "budgets",
            section="unknown-usage",
            action="release-slot" if release_allowed(db, row) else None,
            reserved_micros=row.reserved_micros,
            request_id=row.request_id,
            created_at=row.created_at,
            responsibility="needs_attention",
        )
        for row in db.scalars(
            select(ModelCall).where(ModelCall.status == "UsageUnknown").order_by(ModelCall.created_at)
        )
    ]


def request_item(db, row, user, *, member_waiting=False):
    names = {
        "Combined": "Training and meal update",
        "Training": "Training update",
        "Nutrition": "Meal update",
        "Operations": "Studio analysis",
        "Chat": "Conversation",
        "CycleReview": "Routine review",
        "Intake": "Profile review",
    }
    title = names.get(row.kind, "Activity")
    if (row.payload or {}).get("local_lookup"):
        title = "Workspace lookup"
    if row.user_id != user.id:
        title += f" · {account_name(db, row.user_id)}"
    can_cancel = row.user_id == user.id and row.status in {
        "Queued",
        "Running",
        "Waiting",
        "AwaitingConfirmation",
    }
    return card(
        row.id,
        "Member confirmation" if member_waiting else "Activity",
        title,
        "Waiting for member" if member_waiting else row.status,
        "The member must review and confirm this update. No owner approval is needed here."
        if member_waiting
        else "Open this exact activity for its recorded status and operational evidence.",
        "requests" if row.user_id == user.id else "dashboard",
        section="trace" if row.user_id != user.id else None,
        action="request-cancel" if can_cancel else None,
        created_at=row.created_at,
        responsibility="waiting_for_member" if member_waiting else "in_progress",
        can_cancel=can_cancel,
    )


def entry_item(identifier, category, title, description, page, section):
    item = card(identifier, category, title, "Available", description, page, section=section)
    item["target"].pop("id")
    return item


def workspace_items(db, area, user):
    if area == "notifications":
        return workspace_items(db, "attention", user)
    if area == "attention":
        items = [
            approval_item(db, row)
            for row in db.scalars(
                select(Approval)
                .where(Approval.decision == "Pending", Approval.expires_at > now())
                .order_by(Approval.created_at)
            )
            if approval_current(db, row)
        ]
        for row in db.scalars(select(ChangeRequest).where(ChangeRequest.status == "Approved")):
            approval = db.scalar(
                select(Approval).where(
                    Approval.subject_id == row.id,
                    Approval.decision == "Approved",
                    Approval.expires_at > now(),
                    Approval.fingerprint == row.fingerprint,
                )
            )
            if approval and approval_current(db, approval):
                items.append(change_item(db, row))
        items += [
            incident_item(row)
            for row in db.scalars(
                select(Incident).where(Incident.status != "Closed").order_by(Incident.created_at)
            )
            if row.evidence.get("reason")
            not in {"BudgetRequired", "HourlyCallLimit", "AccountDailyCallLimit", "ModelDispatchUnavailable"}
        ]
        for run in db.scalars(
            select(EvaluationRun).where(
                EvaluationRun.status.in_(["AwaitingReview", "FailedChecks", "Reviewed", "Incomplete"])
            )
        ):
            items.append(
                card(
                    run.id,
                    "Evaluation",
                    f"{run.role} evaluation · {run.status}",
                    run.status,
                    "Open the comparison evidence and human quality ratings before considering a release.",
                    "versions",
                    section=f"evaluation:{run.id}",
                    responsibility="owner_review" if run.status == "AwaitingReview" else "waiting_for_member",
                    created_at=run.created_at,
                )
            )
            items[-1]["target"]["id"] = run.candidate_id
        items += usage_items(db)
        from app.owner_updates import blocking_items

        items += blocking_items(db)
        pending = db.scalars(
            select(Request)
            .join(User, Request.user_id == User.id)
            .where(User.role == "Member", Request.status == "AwaitingConfirmation")
            .order_by(Request.created_at)
        )
        from app.member_experience import activity_view

        for row in pending:
            # AwaitingConfirmation alone also occurs while a plan is waiting for
            # studio review. Preserve the member projection's actual prerequisites.
            experience = activity_view(db, row)
            if row.deadline > now() and experience["status"] not in {
                "Studio review",
                "Needs revision",
                "Expired",
                "Needs refreshing",
                "In use",
                "Previously used",
            }:
                items.append(request_item(db, row, user, member_waiting=True))
        return items
    if area == "approvals":
        return [
            approval_item(db, row)
            for row in db.scalars(select(Approval).order_by(Approval.created_at.desc()))
        ]
    if area == "members":
        return [
            card(
                member.id,
                "Membership",
                account.display_name,
                member.status,
                f"Current group: {group_name(db, member.group_id)}. Review the destination before confirming a group change.",
                "members",
                action="member-group",
                group_id=member.group_id,
                profile_version=member.profile_version,
                created_at=member.created_at,
            )
            for member, account in db.execute(
                select(Member, User).join(User, User.id == Member.user_id).order_by(Member.created_at.desc())
            )
        ]
    if area == "budgets":
        items = []
        for row in db.scalars(select(BudgetAccount).order_by(BudgetAccount.scope_key)):
            scope = row.scope_key
            label = "Studio budget" if scope == "global" else scope
            if scope.startswith("group:"):
                label = f"Group budget · {group_name(db, scope[6:])}"
            elif scope.startswith("member:"):
                member = db.get(Member, scope[7:])
                label = (
                    f"Member budget · {account_name(db, member.user_id) if member else 'Unavailable member'}"
                )
            elif scope.startswith("role:"):
                label = f"Assistant budget · {scope[5:]}"
            items.append(
                card(
                    row.id,
                    "Budget",
                    label,
                    "Configured",
                    f"USD {row.limit_micros / 1_000_000:,.2f} limit. Reserved funds are not settled spending.",
                    "budgets",
                    action="budget",
                    **{k: v for k, v in budget_view(row).items() if k != "id"},
                )
            )
        return items
    if area == "groups":
        return [
            card(
                row.id,
                "Configuration",
                row.name,
                f"Version {row.version}",
                "Review capabilities, profile-field access and plan-review settings.",
                "groups",
            )
            for row in db.scalars(select(MemberGroup).order_by(MemberGroup.name))
        ] + [
            change_item(db, row)
            for row in db.scalars(
                select(ChangeRequest)
                .where(ChangeRequest.kind == "GroupConfiguration")
                .order_by(ChangeRequest.created_at.desc())
            )
        ]
    if area == "incidents":
        return [
            incident_item(row) for row in db.scalars(select(Incident).order_by(Incident.created_at.desc()))
        ]
    if area == "versions":
        items = [
            card(
                row.id,
                "Agent instructions",
                f"{row.role} · {row.label}",
                "Saved version",
                "Open instructions and comparisons. Saving a candidate does not release it.",
                "versions",
                created_at=row.created_at,
            )
            for row in db.scalars(select(AgentVersion).order_by(AgentVersion.created_at.desc()))
        ]
        for run in db.scalars(select(EvaluationRun).order_by(EvaluationRun.created_at.desc())):
            item = card(
                run.candidate_id,
                "Comparison",
                f"{run.role} comparison",
                run.status,
                "Inspect results and human quality ratings. Starting a live comparison uses model allowance.",
                "versions",
                section=f"evaluation:{run.id}",
                created_at=run.created_at,
            )
            item["id"] = run.id
            items.append(item)
        return [
            entry_item(
                "new-candidate",
                "Agent instructions",
                "Create an agent candidate",
                "Draft and save instructions, then prepare a comparison before any release.",
                "versions",
                "create",
            )
        ] + items
    if area == "releases":
        return [
            entry_item(
                "new-release",
                "Release",
                "Propose a release",
                "Choose reviewed live comparison evidence and a destination group.",
                "releases",
                "create",
            )
        ] + [
            change_item(db, row)
            for row in db.scalars(
                select(ChangeRequest)
                .where(ChangeRequest.kind == "AgentRelease")
                .order_by(ChangeRequest.created_at.desc())
            )
        ]
    if area in {"requests", "dashboard"}:
        statement = select(Request).order_by(Request.created_at.desc())
        if area == "requests":
            statement = statement.where(Request.user_id == user.id)
        entries = [request_item(db, row, user) for row in db.scalars(statement)]
        if area == "dashboard":
            entries.insert(
                0,
                entry_item(
                    "operations-report",
                    "Report",
                    "Operations report",
                    "Choose a reporting date range, inspect the breakdown and download the report.",
                    "dashboard",
                    "report",
                ),
            )
        return entries
    if area == "notifications":
        return [
            card(
                row.id,
                "Update",
                row.title,
                "Read" if row.read_at else "Unread",
                row.body,
                "notifications",
                action="notification-read" if not row.read_at else None,
            )
            for row in db.scalars(
                select(Notification)
                .where(Notification.user_id == user.id)
                .order_by(Notification.created_at.desc())
            )
        ]
    if area == "usage":
        return usage_items(db)
    return []


@router.get("/workspace")
def workspace(
    area: Area = "attention",
    query: str = Query(default="", max_length=200),
    target_id: str | None = Query(default=None, max_length=100),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=8, ge=1, le=50),
    user: User = Depends(owner),
):
    with transaction() as db:
        items = workspace_items(db, area, user)
        if target_id:
            items = [
                item for item in items if item["id"] == target_id or item["target"].get("id") == target_id
            ]
        needle = query.strip().casefold()
        if needle:
            items = [
                item
                for item in items
                if needle
                in " ".join(
                    str(item.get(key, ""))
                    for key in ("id", "title", "status", "description", "subject_id", "scope_key")
                ).casefold()
            ]
        counts = {
            key: sum(item.get("responsibility") == key for item in items)
            for key in (
                "owner_review",
                "ready_to_apply",
                "needs_attention",
                "waiting_for_member",
                "in_progress",
            )
        }
        result = {
            "area": area,
            "title": TITLES[area],
            "summary": SUMMARIES[area],
            "items": items[offset : offset + limit],
            "total": len(items),
            "offset": offset,
            "limit": limit,
            "has_more": offset + limit < len(items),
            "counts": counts,
            "checked_at": now(),
        }
        if area == "dashboard":
            member_counts = dict(
                db.execute(select(Member.status, func.count()).group_by(Member.status)).all()
            )
            request_counts = dict(
                db.execute(select(Request.status, func.count()).group_by(Request.status)).all()
            )
            pending_reviews = sum(
                approval_current(db, row)
                for row in db.scalars(
                    select(Approval).where(Approval.decision == "Pending", Approval.expires_at > now())
                )
            )
            unknown_costs = db.scalar(
                select(func.count()).select_from(ModelCall).where(ModelCall.status == "UsageUnknown")
            )
            result["metrics"] = {
                "members": member_counts,
                "requests": request_counts,
                "total_members": sum(member_counts.values()),
                "active_members": member_counts.get("Accepted", 0),
                "pending_reviews": pending_reviews,
                "unknown_costs": unknown_costs,
            }
            active = sum(request_counts.get(state, 0) for state in ("Queued", "Running"))
            result["summary"] = (
                f"Your studio has {sum(member_counts.values())} members, including "
                f"{member_counts.get('Accepted', 0)} with active access. {pending_reviews} reviews need your decision, "
                f"{active} activities are queued or running, and {unknown_costs} provider costs remain unconfirmed. "
                "Open the report to inspect a specific period."
            )
        return result


def classify_intent(content):
    text = content.casefold()
    if re.search(
        r"(?:\bmove\b|\bassign\b|\breassign\b).*(?:\bmember\b|\bto\b)|会员.*(?:调组|转组|移到|改到|分组)",
        text,
    ):
        return "members"
    if re.search(
        r"blocked.*spending|spending.*blocked|approved changes.*(apply|applied)|pending member reviews|preventing.*request|需要.*决策|预算.*阻塞",
        text,
    ):
        return "attention"
    patterns = [
        ("usage", r"uncertain|unknown usage|slot|allowance|quota|用量|额度|配额|未知用量|释放"),
        ("incidents", r"incident|recover|blocked|failure|troubleshoot|故障|恢复|异常|修复"),
        ("budgets", r"budget|spend|cost|\busd\b|\$|预算|金额|花费|费用"),
        ("releases", r"release|canary|roll\s?back|deploy|发布|灰度|回滚|部署"),
        (
            "versions",
            r"agent version|candidate|comparison|compare|evaluation|instruction|prompt|quality|版本|比较|对比|评估|提示词|指令|质量",
        ),
        (
            "groups",
            r"permission|capabilit|group setting|configuration|field access|权限|组配置|分组设置|服务设置|能力",
        ),
        (
            "attention",
            r"blocked.*spending|spending.*blocked|approved changes|pending member reviews|awaiting.?confirmation|waiting.?for.?member|needs?.*attention|what.*(handle|do)|待办|待处理|需要.*处理|等待.*确认|待确认",
        ),
        ("approvals", r"approv|\breviews?\b|application|decline|reject|审核|审批|申请|同意|拒绝"),
        ("members", r"member|assign|move|group|会员|分组|调组|转组"),
        ("requests", r"my activity|my requests|cancel|取消|我的任务|活动记录"),
        ("notifications", r"notification|updates|unread|通知|消息|更新"),
        (
            "dashboard",
            r"overview|report|summari|summary|trace|export|studio activity|报告|报表|概况|汇总|概览|导出|追踪",
        ),
    ]
    for area, pattern in patterns:
        if re.search(pattern, text):
            return area
    return None


def submit_owner_message(db, session, body, user, digest):
    """Finish a local lookup durably, retaining the ordinary chat idempotency contract."""
    from app.call_limit_chat import quota_card

    quota = quota_card(db, body.content, user)
    area = classify_intent(body.content)
    areas = (
        [area]
        if area
        else ["attention", "members", "budgets", "groups", "incidents", "versions", "releases", "dashboard"]
    )
    cards = []
    identifier = re.search(r"\b[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}\b", body.content, re.I)
    for selected in areas:
        item = {"type": "OwnerWorkspace", "area": selected}
        if identifier:
            item["target_id"] = identifier.group()
        # Resolve literal account/group labels locally; duplicate names remain a
        # list requiring a deliberate selection. Nothing is guessed by a model.
        if selected in {"members", "groups", "budgets", "approvals", "releases"}:
            labels = []
            if selected in {"members", "approvals", "budgets"}:
                labels = [
                    name
                    for name in db.scalars(select(User.display_name).where(User.role == "Member"))
                    if len(name.strip()) >= 3
                ]
            if selected != "members":
                labels += [name for name in db.scalars(select(MemberGroup.name)) if len(name.strip()) >= 3]
            matches = set(name for name in labels if name.casefold() in body.content.casefold())
            if len(matches) == 1:
                item["query"] = next(iter(matches))
            else:
                tokens = {token for label in labels for token in re.findall(r"[\w-]{3,}", label)}
                tokens -= {"Member", "User", "Default", "Group", "Owner"}
                partials = [
                    token
                    for token in tokens
                    if re.search(
                        r"(?<!\w)" + re.escape(token.casefold()) + r"(?!\w)", body.content.casefold()
                    )
                ]
                if partials and not matches:
                    item["query"] = max(partials, key=len)
        if selected == "members":
            destination = re.search(
                r"(?:\bto\s+(?:the\s+)?|改到|调到|转到|移到|调整为|改为)\s*(.+)", body.content, re.I
            )
            if destination:
                text = destination.group(1).casefold()
                groups = [
                    group
                    for group in db.scalars(select(MemberGroup))
                    if text.startswith(group.name.casefold())
                    and (
                        len(text) == len(group.name)
                        or not text[len(group.name)].isalnum()
                        or text[len(group.name)] in "组分"
                    )
                ]
                if groups:
                    # A complete longer label takes precedence over its prefix
                    # (for example Canary Service over Canary). Alternatives
                    # remain unresolved and must be selected in the preview.
                    longest = max(len(group.name) for group in groups)
                    exact = [group for group in groups if len(group.name) == longest]
                    tail = text[longest:]
                    if len(exact) == 1 and not re.search(r"\b(?:or|and)\b|或者|还是|或", tail):
                        item["proposal"] = {"group_id": exact[0].id}
        if selected == "budgets" and re.search(
            r"\b(set|change|raise|lower|increase|decrease)\b|设置|改为|调整|提高|降低", body.content, re.I
        ):
            amount = re.search(
                r"(?:\bto\b|\bat\b|为|到|成)\s*(?:USD\s*|\$\s*)?((?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]{1,6})?)(?![0-9.,eE])",
                body.content,
                re.I,
            )
            if amount and Decimal(amount.group(1).replace(",", "")) <= 1000:
                item["proposal"] = {"amount_usd": amount.group(1).replace(",", "")}
        cards.append(item)
    content = (
        (
            "I found the relevant workspace below. Choose the exact record to review its current details. "
            "Any change still needs the labelled confirmation action."
        )
        if area
        else (
            "I can help with reviews, members, settings, budgets, incidents, comparisons and releases. "
            "Choose a workspace below or tell me which record you want to work on."
        )
    )
    if quota:
        cards = [quota]
        content = "Here are your AI call allowances." if quota["entries"] else quota["message"]
    incoming = Message(session_id=session.id, sender="User", content=body.content)
    reply = Message(session_id=session.id, sender="TechnicalManager", content=content, cards=cards)
    request = Request(
        user_id=user.id,
        session_id=session.id,
        kind="Operations",
        status="Completed",
        deadline=now() + timedelta(hours=24),
        payload={
            "content": body.content,
            "role": "TechnicalManager",
            "local_lookup": True,
            "operations_group_id": body.operations_group_id,
        },
        result={"status": "Completed", "local_lookup": True, "area": area},
    )
    db.add_all([incoming, reply, request])
    db.flush()
    db.add(
        RequestEvent(
            request_id=request.id,
            event_type="completed",
            payload={"status": "Completed", "local_lookup": True},
        )
    )
    session.active_role = "TechnicalManager"
    result = {"request_id": request.id, "message_id": incoming.id, "status": "Completed"}
    db.add(
        IdempotencyRecord(
            user_id=user.id, operation=f"chat:{session.id}", key=body.key, fingerprint=digest, result=result
        )
    )
    return result


class ReceiptTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page: Literal[
        "approvals",
        "members",
        "budgets",
        "groups",
        "incidents",
        "versions",
        "releases",
        "requests",
        "notifications",
        "dashboard",
    ]
    id: str = Field(min_length=1, max_length=100)
    section: str | None = Field(default=None, max_length=100)


class ReceiptSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(min_length=8, max_length=128)
    target: ReceiptTarget


RECEIPT_EVENTS = {
    "call_limit.changed": "AI call allowance updated",
    "approval.decided": "Review decision recorded",
    "training.reviewed": "Training review recorded",
    "nutrition.reviewed": "Nutrition review recorded",
    "plan_pair.reviewed": "Training and meal review recorded",
    "configuration.reviewed": "Configuration review recorded",
    "configuration.proposed": "Configuration proposal saved",
    "configuration.executed": "Approved configuration applied",
    "release.proposed": "Release proposal saved",
    "release.reviewed": "Release review recorded",
    "release.canary_started": "Canary release started",
    "release.expansion_started": "Release expansion started",
    "release.rolled_back": "Retained release restored",
    "release.recovery_proposed": "Release recovery proposed",
    "release.recovery_configuration_restored": "Release configuration restored",
    "budget.limit_changed": "Budget limit updated",
    "member.group_changed": "Member group updated",
    "model.local_slot_released": "Local slot released; reserved funds retained",
    "agent_version.candidate_saved": "Agent candidate saved",
    "evaluation.prepared": "Comparison prepared",
    "evaluation.started": "Live comparison started",
    "evaluation.reviewed": "Comparison review recorded",
    "incident.closed_unresolved": "Incident closed without confirmed recovery",
    "incident.recovery_requested": "Recovery requested",
    "incident.prerequisites_recorded": "Recovery prerequisites saved",
    "incident.recovery_verified": "Recovery verified",
    "incident.release_recovery_verified": "Release recovery verified",
    "request.cancelled": "Activity cancelled",
    "notification.read": "Update marked as read",
    "trace.exported": "Redacted trace export recorded",
}


def receipt_subjects(db, target, user):
    """Resolve a target to its audit subjects without trusting client outcome text."""
    page, identifier = target.page, target.id
    if page == "approvals":
        row = db.get(Approval, identifier)
        if row:
            return [identifier, row.subject_id], approval_title(db, row)
    elif page == "members":
        row = db.get(Member, identifier)
        if row:
            return [row.id], account_name(db, row.user_id)
    elif page == "budgets":
        if target.section == "call-limit":
            from app.call_limits import entry, row_for

            rule = entry(db, identifier)
            return [row_for(db, identifier).id], rule["label"]
        row = db.get(ModelCall if target.section == "unknown-usage" else BudgetAccount, identifier)
        if not row and target.section != "unknown-usage":
            row = db.scalar(select(BudgetAccount).where(BudgetAccount.scope_key == identifier))
        if row:
            return [row.id], "Uncertain provider usage" if isinstance(row, ModelCall) else row.scope_key
    elif page in {"groups", "releases"}:
        change = db.get(ChangeRequest, identifier)
        if change and (page == "groups" or change.kind == "AgentRelease"):
            return [change.id], f"{change.kind} · {group_name(db, change.group_id)}"
        group = db.get(MemberGroup, identifier) if page == "groups" else None
        if group:
            return [group.id], group.name
    elif page == "incidents":
        row = db.get(Incident, identifier)
        if row:
            return [row.id], row.title
    elif page == "versions":
        evaluation_id = (
            target.section.removeprefix("evaluation:")
            if target.section and target.section.startswith("evaluation:")
            else None
        )
        run = db.get(EvaluationRun, evaluation_id or identifier)
        if run and (not evaluation_id or run.candidate_id == identifier):
            return [run.id], f"{run.role} comparison"
        if evaluation_id:
            raise HTTPException(404, "The comparison receipt target is not available.")
        row = db.get(AgentVersion, identifier)
        if row:
            return [row.id], f"{row.role} · {row.label}"
    elif page in {"requests", "dashboard"}:
        row = db.get(Request, identifier)
        if row and (page == "dashboard" or row.user_id == user.id):
            return [row.id], "Activity"
    elif page == "notifications":
        row = db.get(Notification, identifier)
        if row and row.user_id == user.id:
            return [row.id], row.title
    raise HTTPException(404, "The receipt target is not available.")


@router.post("/conversations/{identifier}/receipts")
def record_receipt(identifier: str, body: ReceiptSubmission, user: User = Depends(owner)):
    with transaction() as db:
        session = db.scalar(
            select(ChatSession)
            .where(ChatSession.id == identifier, ChatSession.user_id == user.id)
            .with_for_update()
        )
        if not session:
            raise HTTPException(404, "Conversation not found.")
        target = body.target.model_dump(exclude_none=True)
        digest = fingerprint(target)
        operation = f"owner-receipt:{identifier}"
        previous = db.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.user_id == user.id,
                IdempotencyRecord.operation == operation,
                IdempotencyRecord.key == body.key,
            )
        )
        if previous:
            if previous.fingerprint != digest:
                raise HTTPException(409, "This receipt key belongs to a different target.")
            return previous.result
        subjects, title = receipt_subjects(db, body.target, user)
        statement = select(AuditEvent)
        if body.target.page == "approvals":
            # Several approval versions can refer to one plan or change. An
            # audit for a different fingerprint must never become this review's
            # receipt; applying the change is also a separate business action.
            approval = db.get(Approval, body.target.id)
            statement = statement.where(
                AuditEvent.event_type.in_(
                    {
                        "approval.decided",
                        "training.reviewed",
                        "nutrition.reviewed",
                        "plan_pair.reviewed",
                        "configuration.reviewed",
                        "release.reviewed",
                    }
                ),
                AuditEvent.details["fingerprint"].as_string() == approval.fingerprint,
            )
        audit = db.scalar(
            statement.where(
                AuditEvent.actor_id == user.id,
                AuditEvent.subject_id.in_(subjects),
                AuditEvent.created_at >= session.created_at,
                AuditEvent.event_type.in_(RECEIPT_EVENTS),
            )
            .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
            .limit(1)
        )
        if not audit:
            raise HTTPException(
                409,
                "No completed action by your account is recorded for this target. Refresh its details before continuing.",
            )
        source_operation = f"owner-source:{identifier}"
        source = db.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.user_id == user.id,
                IdempotencyRecord.operation == source_operation,
                IdempotencyRecord.key == audit.id,
            )
        )
        if source:
            result = source.result
        else:
            label = RECEIPT_EVENTS[audit.event_type]
            detail = audit.details
            outcome = detail.get("decision")
            if audit.event_type == "budget.limit_changed":
                outcome = f"USD {detail['previous_limit_micros'] / 1_000_000:,.2f} → USD {detail['limit_micros'] / 1_000_000:,.2f}"
            elif audit.event_type == "call_limit.changed":
                outcome = f"{detail['previous_calls']} → {detail['calls']} calls · {detail['source']}"
            elif audit.event_type == "member.group_changed":
                outcome = f"{group_name(db, detail.get('previous_group_id'))} → {group_name(db, detail.get('group_id'))}"
            elif audit.event_type == "trace.exported":
                outcome = detail.get("status", "Recorded")
            receipt = {
                "type": "OwnerReceipt",
                "title": label,
                "subject": title,
                "target": target,
                "audit_id": audit.id,
                "status": outcome or "Recorded",
                "recorded_at": audit.created_at.isoformat(),
            }
            message = Message(
                session_id=session.id,
                sender="TechnicalManager",
                content=f"{label}: {title}." + (f" {outcome}." if outcome else ""),
                cards=[receipt],
            )
            db.add(message)
            db.flush()
            result = {"message_id": message.id, "status": "Recorded", "receipt": receipt}
            db.add(
                IdempotencyRecord(
                    user_id=user.id,
                    operation=source_operation,
                    key=audit.id,
                    fingerprint=fingerprint({"audit_id": audit.id}),
                    result=result,
                )
            )
        db.add(
            IdempotencyRecord(
                user_id=user.id, operation=operation, key=body.key, fingerprint=digest, result=result
            )
        )
        return result
