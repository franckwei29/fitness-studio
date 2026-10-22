"""Allowlisted incident facts for optional read-only operations explanations."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.models import Incident, ModelCall, Request, WorkItem
from app.policy import PolicyDenied
from app.trace_data import REASONS, ROLES, STATUSES


def incident_context(db, identifier):
    incident = db.get(Incident, identifier)
    if not incident:
        raise PolicyDenied("The selected incident is unavailable.")
    request = db.get(Request, incident.request_id) if incident.request_id else None
    work = db.get(WorkItem, incident.evidence.get("work_id"))
    if work and work.request_id != incident.request_id:
        raise PolicyDenied("The incident task reference is inconsistent.")
    calls = list(db.scalars(select(ModelCall).where(ModelCall.request_id == request.id))) if request else []
    return {
        "id": incident.id,
        "status": incident.status if incident.status in {"Open", "RecoveryRequested", "Closed"} else "Other",
        "outcome": incident.outcome
        if incident.outcome in {"Recovered", "Unresolved", "Pending"}
        else "Other",
        "reason": incident.evidence.get("reason")
        if incident.evidence.get("reason") in REASONS
        else "TechnicalReview",
        "role": work.role if work and work.role in ROLES else None,
        "task_status": work.status if work and work.status in STATUSES else None,
        "attempts": work.attempts if work else None,
        "request_status": request.status if request and request.status in STATUSES else None,
        "model_calls": request.model_calls if request else None,
        "max_model_calls": request.max_model_calls if request else None,
        "unknown_calls": sum(call.status == "UsageUnknown" for call in calls),
        "settled_cost_micros": sum(call.actual_micros or 0 for call in calls if call.status == "Settled"),
        "instruction": "Explain observed facts separately from possible causes. Suggest checks, not automatic changes. Do not claim recovery, approval, configuration changes or model deployment occurred. The owner must use the explicit reviewed controls. This is a snapshot, not a live recovery verification.",
    }


# Keep diagnosis categories separate from verified actions in the rendered reply.
AnalysisItem = Annotated[str, Field(min_length=1, max_length=600)]


class IncidentAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")
    observed_facts: list[AnalysisItem] = Field(min_length=1, max_length=6)
    possible_causes: list[AnalysisItem] = Field(min_length=1, max_length=6)
    proposed_actions: list[AnalysisItem] = Field(min_length=1, max_length=6)

    def reply_text(self):
        return "\n\n".join(
            heading + "\n" + "\n".join("• " + item for item in values)
            for heading, values in [
                ("Observed facts", self.observed_facts),
                ("Possible causes — not verified", self.possible_causes),
                ("Proposed checks — no changes executed", self.proposed_actions),
            ]
        )
