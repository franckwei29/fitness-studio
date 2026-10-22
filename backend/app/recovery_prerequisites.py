"""Bind recovery to exact approved changes and their committed execution evidence."""

from sqlalchemy import select

from app.admission import fingerprint
from app.models import Approval, ChangeRequest
from app.policy import PolicyDenied


def change_reference(change):
    return {
        "id": change.id,
        "kind": change.kind,
        "fingerprint": change.fingerprint,
        "group_id": change.group_id,
    }


def verify_prerequisites(db, references):
    receipts = []
    for reference in references:
        change = db.get(ChangeRequest, reference["id"])
        if not change or change_reference(change) != reference:
            raise PolicyDenied("A required recovery change has changed or is unavailable.")
        digest = fingerprint(
            {"group_id": change.group_id, "baseline": change.baseline, "candidate": change.candidate}
        )
        states = {"Executed"} if change.kind == "GroupConfiguration" else {"Canary", "Expanded", "Observed"}
        approved = db.scalar(
            select(Approval.id).where(
                Approval.subject_id == change.id,
                Approval.subject_kind == change.kind,
                Approval.fingerprint == digest,
                Approval.decision == "Approved",
            )
        )
        if (
            digest != change.fingerprint
            or not approved
            or change.status not in states
            or not change.execution
            or change.execution.get("status") != "Committed"
        ):
            raise PolicyDenied(
                "Every required recovery change needs matching approval and successful execution."
            )
        receipts.append(reference | {"group_version": change.execution["group_version"]})
    return receipts
