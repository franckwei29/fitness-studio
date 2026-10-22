"""A crash after settlement reuses the saved output rather than paying twice."""

from datetime import timedelta

from pydantic import BaseModel, SecretStr
from sqlalchemy import func, select
from test_work_queue_integration import task as task_fixture

from app.admission import fingerprint
from app.database import engine, transaction
from app.gemini import build_payload
from app.model_gateway import generate
from app.models import ModelCall, Request, WorkItem, now
from app.pricing import price_for
from app.settings import get_settings
from app.work_queue import claim_next, recover_expired

task = task_fixture


class SavedResponse(BaseModel):
    explanation: str


def test_crash_after_settlement_reuses_output_after_reclaim(monkeypatch, task):
    request_id, work_id, user_id = task
    settings = get_settings().model_copy(
        update={"model_calls_enabled": True, "gemini_api_key": SecretStr("synthetic-never-sent")}
    )
    monkeypatch.setattr("app.model_gateway.get_settings", lambda: settings)

    def forbidden(*args, **kwargs):
        raise AssertionError("Recovery must not reserve or dispatch a second model call")

    monkeypatch.setattr("app.model_gateway.reserve", forbidden)
    monkeypatch.setattr("app.model_gateway.GeminiTransport.send", forbidden)
    claim = claim_next(request_id=request_id)
    assert claim is not None
    instruction = "Return the saved synthetic explanation."
    context = {"request": "Synthetic recovery test"}
    output = SavedResponse(explanation="A saved response awaiting publication.")
    payload = build_payload(settings.gemini_model, instruction, context, SavedResponse, 2048)
    digest = fingerprint({"work_id": work_id, "payload": payload})
    with transaction() as db:
        request = db.get(Request, request_id)
        request.model_calls = 1
        call = ModelCall(
            request_id=request_id,
            user_id=user_id,
            role="Intake",
            model=settings.gemini_model,
            status="Settled",
            request_fingerprint=digest,
            reserved_micros=0,
            actual_micros=123,
            price_version=price_for(settings.gemini_model).version,
            output=output.model_dump(),
        )
        db.add(call)
        db.flush()
        call_id = call.id
        # Simulate process loss after settlement but before candidate publication.
        db.get(WorkItem, work_id).lease_until = now() - timedelta(seconds=1)
    engine().dispose()
    recover_expired()
    replacement = claim_next(request_id=request_id)
    assert replacement and replacement.fence > claim.fence
    recovered_output, recovered_id = generate(replacement, instruction, context, SavedResponse)
    assert recovered_id == call_id and recovered_output == output
    with transaction() as db:
        assert db.get(Request, request_id).model_calls == 1
        assert db.get(ModelCall, call_id).actual_micros == 123
        assert (
            db.scalar(select(func.count()).select_from(ModelCall).where(ModelCall.request_id == request_id))
            == 1
        )
        # This test stops before business publication and must not leave a live lease.
        db.get(WorkItem, work_id).status = "Cancelled"
        db.get(Request, request_id).status = "Cancelled"
