"""Close exhausted evaluation batches without fabricating results or retrying calls."""

from sqlalchemy import select

from app.database import transaction
from app.models import AuditEvent, EvaluationResult, EvaluationRun, Request, WorkItem, now

TERMINAL_REQUEST_STATES = ("Completed", "Cancelled", "Stopped")
TERMINAL_TASK_STATES = ("Completed", "Cancelled", "Stopped", "Superseded")


def close_incomplete_evaluations(batch_size=100):
    if not 1 <= batch_size <= 1000:
        raise ValueError("Evaluation reconciliation batch size must be between 1 and 1000.")
    closed = 0
    with transaction() as db:
        runs = list(
            db.scalars(
                select(EvaluationRun)
                .where(
                    EvaluationRun.status == "Running",
                    ~select(WorkItem.id)
                    .where(
                        WorkItem.subject_kind == "Evaluation",
                        WorkItem.subject_id == EvaluationRun.id,
                        WorkItem.status.not_in(TERMINAL_TASK_STATES),
                    )
                    .exists(),
                )
                .order_by(EvaluationRun.created_at, EvaluationRun.id)
                .limit(batch_size)
                .with_for_update(skip_locked=True)
            )
        )
        for run in runs:
            identifiers = (run.summary or {}).get("request_ids", [])
            requests = list(db.scalars(select(Request).where(Request.id.in_(identifiers))))
            tasks = list(db.scalars(select(WorkItem).where(WorkItem.request_id.in_(identifiers))))
            # A live lease or recoverable wait must remain available to its executor.
            if (
                not identifiers
                or len(requests) != len(set(identifiers))
                or any(row.status not in TERMINAL_REQUEST_STATES for row in requests)
                or any(row.status not in TERMINAL_TASK_STATES for row in tasks)
            ):
                continue
            results = list(db.scalars(select(EvaluationResult).where(EvaluationResult.run_id == run.id)))
            # Successful completion is committed by the evaluation executor. This
            # maintenance path cannot approve or manufacture missing evidence.
            run.status = "Incomplete"
            run.completed_at = now()
            run.summary = (run.summary or {}) | {
                "completed_cases": len(results),
                "passed_cases": sum(row.passed for row in results),
                "cancelled_requests": sum(row.status == "Cancelled" for row in requests),
                "stopped_requests": sum(row.status == "Stopped" for row in requests),
                "manual_review_required": False,
                "release_eligible": False,
                "completion_reason": "Execution ended without a complete reviewed comparison. Prepare a new comparison to retry.",
            }
            db.add(
                AuditEvent(
                    actor_id="system",
                    event_type="evaluation.incomplete",
                    subject_id=run.id,
                    details={"completed_cases": len(results), "release_eligible": False},
                )
            )
            closed += 1
    return closed
