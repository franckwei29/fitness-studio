"""Explicit simulated member direction decisions for older execution-focused regressions.

Stage-boundary tests use the real run_once and assert no automatic continuation.
"""

from uuid import uuid4

from app.database import transaction
from app.models import PlanVersion, Request, User
from app.worker import run_once
from app.workflows import FlowAction, find, perform


def endorse_ready(request_id):
    with transaction() as db:
        request = db.get(Request, request_id)
        flow = find(db, request)
        if not flow or request.status != "AwaitingConfirmation":
            return
        if flow.stage == "MealImpact":
            perform(
                db,
                flow,
                request,
                FlowAction(
                    action="review_meals", expected_revision=flow.revision, operation_key=str(uuid4())
                ),
                db.get(User, request.user_id),
            )
        domain = (
            "training"
            if flow.stage == "TrainingDiscussion"
            else "meals"
            if flow.stage == "MealDiscussion"
            else None
        )
        if not domain:
            return
        plan = db.get(PlanVersion, flow.training_id if domain == "training" else flow.nutrition_id)
        perform(
            db,
            flow,
            request,
            FlowAction(
                action="endorse_" + domain,
                expected_revision=flow.revision,
                operation_key=str(uuid4()),
                target_id=plan.id,
                target_fingerprint=plan.fingerprint,
            ),
            db.get(User, request.user_id),
        )


def run_with_member_approvals(request_id):
    endorse_ready(request_id)
    result = run_once(request_id)
    endorse_ready(request_id)
    return result
