"""Explicit redacted snapshot exports; external failures never alter business work."""

import json
from uuid import NAMESPACE_URL, uuid5

import httpx
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.admission import fingerprint
from app.database import transaction
from app.models import IdempotencyRecord, User, now
from app.security import owner
from app.settings import get_settings
from app.trace_data import request_trace

router = APIRouter(prefix="/api/owner/requests")


@router.get("/{identifier}/trace/export")
def download_trace(identifier: str, user: User = Depends(owner)):
    snapshot = request_trace(identifier)
    return Response(
        json.dumps(snapshot, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="trace-{snapshot["request"]["id"]}.json"'},
    )


class ExportSnapshot(BaseModel):
    fingerprint: str = Field(pattern="^[0-9a-f]{64}$")


@router.post("/{identifier}/trace/langsmith")
def export_langsmith(identifier: str, body: ExportSnapshot, user: User = Depends(owner)):
    settings = get_settings()
    if (
        not settings.studio_langsmith_export_enabled
        or not settings.studio_langsmith_api_key.get_secret_value()
    ):
        raise HTTPException(
            503, "LangSmith export is not configured. Local trace download remains available."
        )
    snapshot = request_trace(identifier)
    if snapshot["fingerprint"] != body.fingerprint:
        raise HTTPException(
            409, "The trace changed. Refresh and review the latest snapshot before exporting."
        )
    export_key = fingerprint(
        {
            "snapshot": body.fingerprint,
            "endpoint": settings.studio_langsmith_endpoint,
            "project": settings.studio_langsmith_project,
            "owner": user.id,
        }
    )
    run_id = str(uuid5(NAMESPACE_URL, f"fitness-studio:{export_key}"))
    query = select(IdempotencyRecord).where(
        IdempotencyRecord.user_id == user.id,
        IdempotencyRecord.operation == "trace-export",
        IdempotencyRecord.key == export_key,
    )
    try:
        with transaction() as db:
            previous = db.scalar(query)
            if previous:
                return previous.result
            db.add(
                IdempotencyRecord(
                    user_id=user.id,
                    operation="trace-export",
                    key=export_key,
                    fingerprint=body.fingerprint,
                    result={
                        "status": "Pending",
                        "run_id": run_id,
                        "message": "Export dispatch was recorded. Its remote outcome is not yet confirmed; it will not be automatically resent.",
                    },
                )
            )
    except IntegrityError:
        with transaction() as db:
            return db.scalar(query).result
    observed_at = now().isoformat() + "Z"
    # This run represents the act of taking a snapshot, not invented execution
    # timings. Actual task/call times remain inside the allowlisted metadata.
    payload = {
        "id": run_id,
        "name": "Fitness Studio operational snapshot",
        "run_type": "chain",
        "session_name": settings.studio_langsmith_project,
        "inputs": {},
        "outputs": {},
        "start_time": observed_at,
        "end_time": observed_at,
        "extra": {"metadata": {"snapshot": snapshot}},
    }
    try:
        headers = {"x-api-key": settings.studio_langsmith_api_key.get_secret_value()}
        if settings.studio_langsmith_workspace_id:
            headers["x-tenant-id"] = str(settings.studio_langsmith_workspace_id)
        with httpx.Client(timeout=10, follow_redirects=False, trust_env=False) as client:
            response = client.post(
                f"{settings.studio_langsmith_endpoint}/runs",
                json=payload,
                headers=headers,
            )
        status = (
            "Accepted"
            if 200 <= response.status_code < 300
            else "Unknown"
            if response.status_code >= 500 or response.status_code == 409
            else "Failed"
        )
        message = (
            "LangSmith accepted the redacted snapshot. Use its run ID to locate it in the configured project."
            if status == "Accepted"
            else "The remote export outcome is unknown. Local evidence is retained; check the run ID before retrying elsewhere."
            if status == "Unknown"
            else "LangSmith rejected the export. Local evidence is retained; no automatic retry was made."
        )
    except httpx.HTTPError:
        status = "Unknown"
        message = "The remote export outcome is unknown. Local evidence is retained; check the run ID before retrying elsewhere."
    result = {"status": status, "run_id": run_id, "message": message}
    with transaction() as db:
        record = db.scalar(query.with_for_update())
        record.result = result
    return result
