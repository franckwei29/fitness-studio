"""Continue owner-requested plan revisions inside the original bounded request."""

from fastapi import HTTPException
from sqlalchemy import select

from app.activation import pair_fingerprint, plan_fingerprint
from app.candidate_correction import MAX_CORRECTIONS
from app.models import Approval, Message, PlanPair, PlanVersion, Request, WorkItem, now
from app.policy import PolicyDenied
from app.work_queue import event


def apply_plan_decision(db, approval, plans, decision, comment):
    source = plans[-1]
    request = db.scalar(select(Request).where(Request.id == source.request_id).with_for_update())
    if not request or request.deadline <= now() or request.status in {"Cancelled", "Stopped"}:
        raise HTTPException(409, "The source request has ended or expired.")
    from app.workflows import find

    current_flow = find(db, request)
    if current_flow and current_flow.stage != "OwnerReview":
        raise HTTPException(
            409, "The member must complete the draft directions before this version can be reviewed."
        )
    field = "pair_id" if approval.subject_kind == "PlanPair" else source.kind.lower() + "_id"
    if (request.result or {}).get(field) != approval.subject_id:
        raise HTTPException(409, "This review no longer describes the latest request candidate.")
    if decision == "Approved":
        text = "The owner approved your candidate. Review and confirm it in My plans to make it active."
    elif decision == "Rejected":
        request.status = "Stopped"
        request.result = (request.result or {}) | {"status": "Stopped", "stop_reason": "OwnerRejected"}
        text = "The owner rejected this candidate. Your active plan has not changed."
    else:
        if not comment.strip():
            raise HTTPException(422, "Explain the changes needed before requesting a revision.")
        if request.revisions >= MAX_CORRECTIONS or request.model_calls >= request.max_model_calls:
            request.status = "Stopped"
            request.result = (request.result or {}) | {"status": "Stopped", "stop_reason": "CorrectionLimit"}
            text = "The owner requested changes, but this request reached its revision or call limit. No new model call was queued."
        else:
            if db.scalar(
                select(WorkItem.id).where(
                    WorkItem.request_id == request.id, WorkItem.status.in_(["Queued", "Running", "Waiting"])
                )
            ):
                raise HTTPException(409, "The request still has unfinished work.")
            request.revisions += 1
            request.status = "Queued"
            request.payload = request.payload | {
                "owner_review": {
                    "approval_id": approval.id,
                    "plans": {plan.kind: plan.id for plan in plans},
                }
            }
            role = (
                "Coach"
                if source.kind == "Training" or request.kind in {"Combined", "CycleReview"}
                else "Nutrition"
            )
            training = next((plan for plan in plans if plan.kind == "Training"), None)
            item = WorkItem(
                request_id=request.id,
                role=role,
                subject_kind="TrainingPlan" if role == "Nutrition" and training else "Conversation",
                subject_id=training.id
                if role == "Nutrition" and training
                else request.session_id or source.member_id,
                input_versions={"profile": source.profile_version, "group": source.config_version},
            )
            db.add(item)
            db.flush()
            request.result = (request.result or {}) | {"status": "Queued", "owner_revision_work_id": item.id}
            text = f"The owner requested changes. Revision {request.revisions} of {MAX_CORRECTIONS} is queued within the original request. Your active plan has not changed."
    from app.workflows import find, set_stage

    flow = find(db, request)
    if flow:
        if decision == "Approved":
            set_stage(db, flow, request, "FinalReview", "Nutrition" if flow.nutrition_id else "Coach", text)
        elif decision == "Revise" and request.status == "Queued":
            if role == "Coach":
                flow.training_endorsement = flow.nutrition_endorsement = None
            else:
                flow.nutrition_endorsement = None
            set_stage(
                db, flow, request, "GeneratingTraining" if role == "Coach" else "GeneratingMeals", role, text
            )
    if flow:
        from app.models import Notification

        db.add(
            Notification(
                user_id=request.user_id,
                business_key=f"workflow:{flow.id}:{approval.id}",
                title="Studio review · " + decision,
                body=text,
                kind="Workflow",
            )
        )
    request.result = (request.result or {}) | {"review_decision": decision, "review_message": text}
    if request.session_id:
        db.add(
            Message(
                session_id=request.session_id,
                sender="System",
                content=text + (f" Owner notes: {comment}" if comment else ""),
            )
        )
    event(db, request, "plan_reviewed", status=request.status, decision=decision)


def owner_review_context(db, request, authorized, role):
    reference = request.payload.get("owner_review")
    if not reference:
        return {}
    approval = db.get(Approval, reference["approval_id"])
    if not approval or approval.decision != "Revise":
        raise PolicyDenied("The recorded owner revision decision is unavailable.")
    plans = {kind: db.get(PlanVersion, identifier) for kind, identifier in reference["plans"].items()}
    for plan in plans.values():
        if (
            not plan
            or plan.member_id != authorized["member_id"]
            or plan.fingerprint != plan_fingerprint(plan)
            or plan.profile_version != authorized["versions"]["profile"]
            or plan.config_version != authorized["versions"]["group"]
        ):
            raise PolicyDenied("The reviewed candidate or its context changed.")
    if approval.subject_kind == "PlanPair":
        pair = db.get(PlanPair, approval.subject_id)
        if (
            not pair
            or pair.status != "Revise"
            or set(plans) != {"Training", "Nutrition"}
            or pair.training_id != plans["Training"].id
            or pair.nutrition_id != plans["Nutrition"].id
            or approval.fingerprint != pair_fingerprint(plans["Training"], plans["Nutrition"])
        ):
            raise PolicyDenied("The reviewed plan pair changed.")
    else:
        plan = next(iter(plans.values()))
        if (
            plan.status != "Revise"
            or approval.subject_id != plan.id
            or approval.fingerprint != plan.fingerprint
        ):
            raise PolicyDenied("The reviewed plan changed.")
    plan = plans.get("Training" if role == "Coach" else "Nutrition")
    return {
        "owner_review": {
            "notes": approval.comment,
            "previous_candidate": plan.content if plan else None,
            "instruction": "Address the owner's requested changes within your role and the confirmed constraints. Return a complete new candidate, never claim it is approved or active. All validation and approval checks apply again.",
        }
    }
