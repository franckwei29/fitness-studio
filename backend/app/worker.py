"""Bounded task execution. Run with python -m app.worker --once or --request ID."""

import argparse
import logging
import time

from app.budget import BudgetDenied
from app.coach_runtime import run_coach
from app.conversation_runtime import run_conversation
from app.database import transaction
from app.evaluation_runtime import run_evaluation
from app.gemini import ProviderFailure
from app.incidents import record_incident
from app.intake_runtime import run_intake
from app.model_gateway import ModelUnavailable
from app.models import AuditEvent, Request
from app.nutrition_runtime import run_nutrition
from app.policy import PolicyDenied
from app.pricing import UsageUncertain
from app.work_queue import StaleClaim, claim_next, lock_claim, recover_expired, wait_for

logger = logging.getLogger("fitness_studio.worker")


def record_failure(claim, reason, context=None):
    try:
        wait_for(claim, reason, context)
        record_incident(claim, reason)
    except StaleClaim:
        logger.info("Task lease no longer permits a status update: %s", claim.work_id)


def execute(claim):
    try:
        with transaction() as db:
            request_kind = db.get(Request, claim.request_id).kind
        if request_kind == "Auto":
            from app.intent_router import run_auto

            run_auto(claim)
        elif request_kind == "Evaluation":
            run_evaluation(claim)
        elif claim.role == "TechnicalManager" or (request_kind == "Chat" and claim.role != "Intake"):
            run_conversation(claim)
        elif claim.role == "Intake":
            run_intake(claim)
        elif claim.role == "Coach":
            run_coach(claim)
        elif claim.role == "Nutrition":
            run_nutrition(claim)
        else:
            record_failure(claim, "ExecutorUnavailable")
    except BudgetDenied as exc:
        record_failure(claim, exc.wait_reason, exc.context)
    except ModelUnavailable as exc:
        record_failure(claim, exc.wait_reason, exc.context)
    except PolicyDenied:
        record_failure(claim, "PolicyReview")
    except (ProviderFailure, UsageUncertain):
        record_failure(claim, "UsageReconciliation")
    except StaleClaim:
        logger.info("Expired task will be handled by recovery: %s", claim.work_id)
    except Exception as exc:
        # Do not log raw provider responses, model inputs, keys, or health data.
        logger.error("Task execution failed: %s (%s)", claim.work_id, type(exc).__name__)
        try:
            with transaction() as db:
                _, request = lock_claim(db, claim)
                db.add(
                    AuditEvent(
                        actor_id="system",
                        request_id=request.id,
                        event_type="task.execution_failed",
                        subject_id=claim.work_id,
                        details={"error_type": type(exc).__name__},
                    )
                )
        except StaleClaim:
            return
        record_failure(claim, "TechnicalReview")


def run_once(request_id=None):
    recover_expired()
    claim = claim_next(request_id=request_id)
    if not claim:
        return False
    execute(claim)
    return True


def main():
    parser = argparse.ArgumentParser(description="Run durable Fitness Studio tasks.")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--request", help="Execute only the specified queued request.")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    if args.once or args.request:
        run_once(args.request)
        return
    try:
        while True:
            if not run_once():
                time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Worker stopped.")


if __name__ == "__main__":
    main()
