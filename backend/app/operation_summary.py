"""One deterministic metric definition for the overview and Markdown brief."""

from datetime import date, datetime, time, timedelta

from sqlalchemy import func, select

from app.models import Activation, Incident, Member, ModelCall, PlanVersion, User, now


def reporting_period(start: date | None, end: date | None):
    end = end or now().date()
    start = start or end - timedelta(days=6)
    if start > end or (end - start).days > 365:
        raise ValueError("Choose an ordered date range of at most 366 days.")
    if end > now().date():
        raise ValueError("The reporting period cannot end in the future.")
    return datetime.combine(start, time.min), datetime.combine(end + timedelta(days=1), time.min)


def summarize(db, start, end):
    """Use a half-open UTC creation-time window; current state is labelled separately."""

    def window(model):
        return (model.created_at >= start, model.created_at < end)

    def count(model):
        return db.scalar(select(func.count()).select_from(model).where(*window(model)))

    calls = list(db.scalars(select(ModelCall).where(*window(ModelCall))))
    by_role, by_member = {}, {}
    members = dict(db.execute(select(Member.user_id, Member.id)).all())
    for call in calls:
        keys = [(by_role, call.role), (by_member, members.get(call.user_id, "No member profile"))]
        for buckets, key in keys:
            entry = buckets.setdefault(
                key, {"calls": 0, "settled_micros": 0, "held_micros": 0, "unknown_calls": 0}
            )
            entry["calls"] += 1
            if call.status == "Settled" and call.actual_micros is not None:
                entry["settled_micros"] += call.actual_micros
            if call.status in {"Reserved", "Dispatching", "UsageUnknown"}:
                entry["held_micros"] += call.reserved_micros
            if call.status == "UsageUnknown":
                entry["unknown_calls"] += 1
    return {
        "start": start.date().isoformat(),
        "end": (end - timedelta(days=1)).date().isoformat(),
        "timezone": "UTC",
        "generated_at": now().isoformat() + "Z",
        "new_members": count(Member),
        "plan_versions_created": count(PlanVersion),
        "successful_activations": count(Activation),
        "active_members_now": db.scalar(
            select(func.count())
            .select_from(Member)
            .join(User, Member.user_id == User.id)
            .where(Member.status == "Accepted", User.active.is_(True))
        ),
        "unresolved_incidents_now": db.scalar(
            select(func.count()).select_from(Incident).where(Incident.outcome != "Resolved")
        ),
        "settled_micros": sum(row["settled_micros"] for row in by_role.values()),
        "held_micros": sum(row["held_micros"] for row in by_role.values()),
        "unknown_calls": sum(row["unknown_calls"] for row in by_role.values()),
        "cost_by_role": [{"role": key, **value} for key, value in sorted(by_role.items())],
        "cost_by_member": [{"member_id": key, **value} for key, value in sorted(by_member.items())],
        "definitions": [
            "Period counts use record creation time within the inclusive UTC dates shown.",
            "New members counts membership records created, including members whose admission later changed.",
            "Active members and unresolved incidents describe current state, not historical state at the period end.",
            "Closed incidents without a verified Resolved outcome remain unresolved in this metric.",
            "Plan versions created and successful activations are separate measures; a candidate is not a delivery.",
            "Costs cover calls created during the period, using their current settlement state. Held amounts are reservations, not known charges.",
            "Role and member cost breakdowns are alternative views of the same calls; do not add their totals together.",
        ],
    }


def markdown_summary(summary):
    def money(value):
        return f"USD {value / 1_000_000:.6f}"

    lines = [
        "# Fitness Studio operations brief",
        "",
        f"Period: {summary['start']} through {summary['end']} (UTC)",
        f"Generated: {summary['generated_at']}",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
        f"| New membership records | {summary['new_members']} |",
        f"| Plan versions created | {summary['plan_versions_created']} |",
        f"| Successful activations | {summary['successful_activations']} |",
        f"| Active members now | {summary['active_members_now']} |",
        f"| Unresolved incidents now | {summary['unresolved_incidents_now']} |",
        f"| Settled model cost | {money(summary['settled_micros'])} |",
        f"| Held model reservations | {money(summary['held_micros'])} |",
        f"| Calls with unknown usage | {summary['unknown_calls']} |",
        "",
    ]
    for label, key, identity in (
        ("Cost by role", "cost_by_role", "role"),
        ("Cost by member reference", "cost_by_member", "member_id"),
    ):
        lines.extend(
            [
                f"## {label}",
                "",
                "| Reference | Calls | Settled | Held | Unknown |",
                "| --- | ---: | ---: | ---: | ---: |",
            ]
        )
        for row in summary[key]:
            lines.append(
                f"| {row[identity]} | {row['calls']} | {money(row['settled_micros'])} | {money(row['held_micros'])} | {row['unknown_calls']} |"
            )
        lines.append("")
    lines.extend(
        [
            "## Metric definitions",
            "",
            *(f"- {item}" for item in summary["definitions"]),
            "",
            "This deterministic brief makes no model calls and contains no member health fields or conversation text.",
            "",
        ]
    )
    return "\n".join(lines)
