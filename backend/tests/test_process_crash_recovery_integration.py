"""Real process exit after simulated provider settlement, then durable publication.

Only the provider transport is simulated. MySQL, Redis checkpoints, budget
settlement, worker leases, cached inference and message publication are real.
"""

import subprocess
import sys
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import func, select
from test_admission_integration import owner_client as owner_fixture

from app.database import transaction
from app.models import Message, ModelCall, Request, WorkItem, now
from app.work_queue import recover_expired

owner_client = owner_fixture

CHILD = r"""
import os, sys
from pydantic import SecretStr
from app.agent_contracts import ChatReply
from app.gemini import ProviderResult
from app.settings import get_settings
from app.worker import run_once
import app.model_gateway as gateway
import app.conversation_runtime as runtime
settings=get_settings().model_copy(update={"model_calls_enabled":True,"gemini_api_key":SecretStr("synthetic-not-sent"),"model_account_daily_call_limit":100})
gateway.get_settings=lambda:settings
stage=sys.argv[2]
def transport(*args):
    if stage!="crash":raise AssertionError("Recovery dispatched a second provider call")
    return ProviderResult("synthetic-crash-provider",ChatReply(reply="Saved operational explanation."),dict(total_input_tokens=10,total_output_tokens=10,total_tokens=20,total_thought_tokens=0,total_cached_tokens=0,total_tool_use_tokens=0))
gateway.GeminiTransport.send=transport
original=gateway.generate
def generate(*args,**kwargs):
    result=original(*args,**kwargs)
    if stage=="crash":os._exit(77)
    return result
runtime.generate=generate
assert run_once(sys.argv[1])
"""


def test_process_exit_after_settlement_publishes_once(owner_client):
    for scope in ["global", "role:TechnicalManager"]:
        rows = owner_client.get("/api/owner/budgets").json()
        row = next((x for x in rows if x["scope_key"] == scope), None)
        response = owner_client.post(
            "/api/owner/budgets",
            json=dict(
                scope_key=scope,
                limit_micros=10000000,
                expected_fingerprint=row["fingerprint"] if row else None,
                reason="Isolated synthetic process crash verification.",
            ),
        )
        assert response.status_code == 200, response.text
    session = owner_client.post("/api/conversations").json()
    response = owner_client.post(
        f"/api/conversations/{session['id']}/messages",
        json=dict(
            key=str(uuid4()),
            purpose="Operations",
            operations_ai=True,
            content="Explain the supplied synthetic operations evidence.",
        ),
    )
    assert response.status_code == 202, response.text
    request_id = response.json()["request_id"]
    first = subprocess.run(
        [sys.executable, "-c", CHILD, request_id, "crash"], capture_output=True, text=True, timeout=40
    )
    assert first.returncode == 77, first.stderr
    with transaction() as db:
        calls = list(db.scalars(select(ModelCall).where(ModelCall.request_id == request_id)))
        assert len(calls) == 1 and calls[0].status == "Settled"
        assert db.get(Request, request_id).status == "Running"
        assert (
            db.scalar(
                select(func.count())
                .select_from(Message)
                .where(Message.session_id == session["id"], Message.sender == "TechnicalManager")
            )
            == 0
        )
        work = db.scalar(select(WorkItem).where(WorkItem.request_id == request_id))
        work.lease_until = now() - timedelta(seconds=1)
        call_id = calls[0].id
    recover_expired()
    second = subprocess.run(
        [sys.executable, "-c", CHILD, request_id, "recover"], capture_output=True, text=True, timeout=40
    )
    assert second.returncode == 0, second.stderr
    with transaction() as db:
        request = db.get(Request, request_id)
        assert request.status == "Completed" and request.model_calls == 1
        assert request.result["call_id"] == call_id
        assert (
            db.scalar(select(func.count()).select_from(ModelCall).where(ModelCall.request_id == request_id))
            == 1
        )
        assert (
            db.scalar(
                select(func.count())
                .select_from(Message)
                .where(Message.session_id == session["id"], Message.sender == "TechnicalManager")
            )
            == 1
        )
