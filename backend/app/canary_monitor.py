"""Observe scoped canary executions and apply preapproved rollback conditions."""

from datetime import datetime, timedelta

from sqlalchemy import select

from app.agent_release import rollback_configuration
from app.database import transaction
from app.models import AuditEvent, ChangeRequest, ModelCall, WorkItem, now
from app.policy import PolicyDenied
from app.pricing import UsageUncertain, price_for


def observation(db, change, at):
    started = datetime.fromisoformat(change.execution["started_at"])
    tasks = list(
        db.scalars(
            select(WorkItem).where(WorkItem.role == change.candidate["role"], WorkItem.created_at >= started)
        )
    )
    matching = [
        task
        for task in tasks
        if (
            task.input_versions.get("group_id") == change.group_id
            and task.input_versions.get("group") == change.execution["group_version"]
            and task.input_versions.get("agent_version") == change.candidate["agent_version_id"]
        )
    ]
    completed, failures, hard_failures, recorded = [], [], [], []
    for task in matching:
        calls = list(
            db.scalars(
                select(ModelCall).where(ModelCall.request_id == task.request_id, ModelCall.role == task.role)
            )
        )
        supported = []
        for call in calls:
            try:
                price = price_for(call.model)
            except UsageUncertain:
                continue
            if (
                call.request_fingerprint
                and call.price_version == price.version
                and call.status in {"Settled", "Dispatching", "UsageUnknown"}
            ):
                supported.append(call)
        if not supported:
            continue
        recorded.append(task.id)
        if task.status == "Completed" and any(
            call.status == "Settled" and call.output is not None for call in supported
        ):
            completed.append(task.id)
        if task.wait_reason == "CandidateValidation":
            hard_failures.append(task.id)
        elif task.wait_reason in {"TechnicalReview", "UsageReconciliation"}:
            failures.append(task.id)
    return {
        "observed_at": at.isoformat(),
        "recorded_tasks": recorded,
        "completed_tasks": completed,
        "execution_failures": failures,
        "hard_rule_failures": hard_failures,
        "window_elapsed": at >= started + timedelta(minutes=change.candidate["observation_minutes"]),
        "minimum_runs_met": len(completed) >= change.candidate["minimum_runs"],
    }


def monitor_canaries(at=None):
    at = at or now()
    updated = 0
    with transaction() as db:
        releases = list(
            db.scalars(
                select(ChangeRequest)
                .where(
                    ChangeRequest.kind == "AgentRelease",
                    ChangeRequest.status.in_(["Canary", "Expanded", "Observed"]),
                )
                .with_for_update(skip_locked=True)
            )
        )
        for change in releases:
            if not change.execution or not change.execution.get("started_at"):
                continue
            evidence = observation(db, change, at)
            change.execution = change.execution | {"observation": evidence}
            if (
                evidence["hard_rule_failures"]
                and change.candidate.get("rollback_on_hard_rule_failure") is True
            ) or len(evidence["execution_failures"]) >= change.candidate["error_threshold"]:
                try:
                    rollback_configuration(
                        db, change, "system", "Preapproved canary failure threshold reached."
                    )
                except (PolicyDenied, UsageUncertain) as exc:
                    # The release must remain visibly unresolved, never claim a failed rollback succeeded.
                    change.status = "RollbackBlocked"
                    change.execution = change.execution | {"rollback_blocked_reason": str(exc)}
                    db.add(
                        AuditEvent(
                            actor_id="system",
                            event_type="release.rollback_blocked",
                            subject_id=change.id,
                            details={"reason": str(exc)},
                        )
                    )
                updated += 1
            elif (
                change.status in {"Canary", "Expanded"}
                and evidence["window_elapsed"]
                and evidence["minimum_runs_met"]
            ):
                change.status = "Observed"
                db.add(
                    AuditEvent(
                        actor_id="system",
                        event_type="release.observation_completed",
                        subject_id=change.id,
                        details=evidence,
                    )
                )
                updated += 1
    return updated
