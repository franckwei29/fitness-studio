"""SQL fences for conversational comparisons; provider outputs are not simulated as live evidence."""

import json
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from test_owner_agent_integration import clients as clients
from test_owner_agent_integration import task

from app import version_admin
from app.bootstrap import DEFAULT_CONFIGURATION
from app.database import transaction
from app.models import AgentVersion, EvaluationRun, MemberGroup, Request, WorkItem
from app.owner_tools import ToolInput, budget_baseline, validate_action
from app.settings import get_settings


@pytest.fixture
def comparison(clients, monkeypatch):
    settings = get_settings()
    from pydantic import SecretStr

    monkeypatch.setattr(settings, "openai_api_key", SecretStr("isolated-test-only"))
    monkeypatch.setattr(settings, "gemini_api_key", SecretStr("isolated-test-only"))
    instruction = version_admin.base_instruction("Coach") + f"\nIsolated comparison {uuid4()}"
    monkeypatch.setattr(version_admin, "base_instruction", lambda role: instruction)
    with transaction() as db:
        group = MemberGroup(name=f"Compare-{uuid4()}", configuration=DEFAULT_CONFIGURATION)
        db.add(group)
        db.flush()
        group_id = group.id
    other = "openai" if settings.default_model.startswith("gemini") else "gemini"
    action = ToolInput(
        name="comparison.run_current",
        arguments=json.dumps({"group_id": group_id, "role": "Coach", "model": other}),
    )
    return clients[0], action, instruction


def saved(owner, action):
    action = validate_action(action)
    snapshot = budget_baseline(action, [])
    identifier, digest, session = task(owner, [action.model_dump()])
    with transaction() as db:
        row = db.get(Request, identifier)
        state = row.result["owner_task"] | {"baselines": [snapshot], "comparisons": [snapshot]}
        row.result = row.result | {"owner_task": state}
    return identifier, {"fingerprint": digest, "decision": "Execute"}, snapshot


def test_fresh_preview_one_confirmation_and_retry(comparison):
    owner, action, instruction = comparison
    identifier, decision, snapshot = saved(owner, action)
    with transaction() as db:
        assert not db.scalar(select(AgentVersion).where(AgentVersion.prompt == instruction))
    assert snapshot["planned_calls"] == 6
    response = owner.post(f"/api/owner/tasks/{identifier}/decision", json=decision)
    assert response.status_code == 200, response.text
    receipt = response.json()["receipts"][0]
    assert receipt["status"] == "Running"
    with transaction() as db:
        run = db.get(EvaluationRun, receipt["id"])
        assert (
            db.get(AgentVersion, run.baseline_id).prompt
            == db.get(AgentVersion, run.candidate_id).prompt
            == instruction
        )
        assert db.get(MemberGroup, snapshot["group_id"]).configuration == DEFAULT_CONFIGURATION
        assert len(run.summary["request_ids"]) == 6
        assert (
            db.scalar(
                select(func.count())
                .select_from(WorkItem)
                .where(WorkItem.request_id.in_(run.summary["request_ids"]))
            )
            == 6
        )
    assert owner.post(f"/api/owner/tasks/{identifier}/decision", json=decision).json()["receipts"] == [
        receipt
    ]
    assert owner.get(f"/api/owner/tasks/{identifier}").json()["status"] == "Executed"
    # Existing candidates are reused in a subsequent comparison.
    second, decision2, _ = saved(owner, action)
    result = owner.post(f"/api/owner/tasks/{second}/decision", json=decision2).json()
    assert result["receipts"][0]["target"]["id"] == receipt["target"]["id"]


def test_stale_preview_cannot_confirm(comparison):
    owner, action, _ = comparison
    identifier, decision, snapshot = saved(owner, action)
    with transaction() as db:
        db.get(MemberGroup, snapshot["group_id"]).version += 1
    assert owner.get(f"/api/owner/tasks/{identifier}").json()["status"] == "NeedsCorrection"
    response = owner.post(f"/api/owner/tasks/{identifier}/decision", json=decision)
    assert response.status_code == 409
    with transaction() as db:
        assert db.get(Request, identifier).result["owner_task"]["receipts"] == []


def test_group_id_as_version_rejected_before_confirmation(comparison):
    owner, action, _ = comparison
    group_id = json.loads(action.arguments)["group_id"]
    legacy = ToolInput(
        name="comparison.prepare", target=group_id, arguments=json.dumps({"baseline_id": group_id})
    )
    with pytest.raises(ValueError, match="not group IDs"):
        validate_action(legacy)
    identifier, digest, _ = task(owner, [legacy.model_dump()])
    assert owner.get(f"/api/owner/tasks/{identifier}").json()["status"] == "NeedsCorrection"
    assert (
        owner.post(
            f"/api/owner/tasks/{identifier}/decision", json={"fingerprint": digest, "decision": "Execute"}
        ).status_code
        == 422
    )


@pytest.mark.parametrize(
    "change,match",
    [
        ({"group_id": "missing"}, "Group not found"),
        ({"model": "unknown-model"}, "unknown or ambiguous"),
        ({"model": None}, "already uses"),
    ],
)
def test_invalid_intent_rejected(comparison, change, match):
    _, action, _ = comparison
    args = json.loads(action.arguments)
    args.update({k: (get_settings().default_model if v is None else v) for k, v in change.items()})
    with pytest.raises(ValueError, match=match):
        validate_action(action.model_copy(update={"arguments": json.dumps(args)}))


def test_start_failure_rolls_back_candidates_and_run(comparison, monkeypatch):
    owner, action, instruction = comparison
    identifier, decision, _ = saved(owner, action)

    def fail(*args):
        raise ValueError("test start failure")

    monkeypatch.setattr(version_admin, "start_comparison", fail)
    assert owner.post(f"/api/owner/tasks/{identifier}/decision", json=decision).status_code == 422
    with transaction() as db:
        assert not db.scalar(select(AgentVersion).where(AgentVersion.prompt == instruction))
        assert db.get(Request, identifier).result["owner_task"]["status"] == "Ready"


def test_suite_change_and_missing_credentials(comparison, monkeypatch):
    owner, action, _ = comparison
    identifier, decision, _ = saved(owner, action)
    monkeypatch.setattr(version_admin, "SUITE_VERSION", "changed-suite")
    assert owner.post(f"/api/owner/tasks/{identifier}/decision", json=decision).status_code == 409
    from pydantic import SecretStr

    monkeypatch.setattr(get_settings(), "openai_api_key", SecretStr(""))
    with pytest.raises(ValueError, match="credentials"):
        validate_action(action)


def test_explicit_group_version_and_cancel(comparison):
    owner, action, _ = comparison
    identifier, decision, snapshot = saved(owner, action)
    response = owner.post(f"/api/owner/tasks/{identifier}/decision", json=decision).json()
    with transaction() as db:
        run = db.get(EvaluationRun, response["receipts"][0]["id"])
        group = db.get(MemberGroup, snapshot["group_id"])
        group.configuration = group.configuration | {"agent_versions": {"Coach": run.candidate_id}}
    args = json.loads(action.arguments) | {"model": get_settings().default_model}
    reverse = action.model_copy(update={"arguments": json.dumps(args)})
    identifier, decision, snapshot = saved(owner, reverse)
    assert snapshot["baseline_model"] != get_settings().default_model
    response = owner.post(f"/api/owner/tasks/{identifier}/decision", json=decision | {"decision": "Dismiss"})
    assert response.json()["status"] == "Dismissed"
    assert owner.post(f"/api/owner/tasks/{identifier}/decision", json=decision).status_code == 409


def test_invalid_generated_id_is_repaired_before_publishing(comparison, monkeypatch):
    from app.models import ModelCall
    from app.owner_agent import OwnerTurn
    from app.worker import run_once

    owner, valid, _ = comparison
    monkeypatch.setattr(get_settings(), "model_calls_enabled", True)
    contexts = []
    group_id = json.loads(valid.arguments)["group_id"]

    def synthetic(claim, instruction, context, output_type, max_output_tokens):
        contexts.append(json.loads(json.dumps(context)))
        action = (
            valid
            if len(contexts) > 1
            else ToolInput(
                name="comparison.prepare", target=group_id, arguments=json.dumps({"baseline_id": group_id})
            )
        )
        output = OwnerTurn(reply="Review the comparison.", goal="Compare models", actions=[action])
        with transaction() as db:
            request = db.get(Request, claim.request_id)
            call = ModelCall(
                request_id=request.id,
                user_id=request.user_id,
                role=claim.role,
                model="synthetic-test",
                status="Settled",
                reserved_micros=0,
                actual_micros=0,
                price_version="synthetic-test",
            )
            db.add(call)
            db.flush()
            return output, call.id

    monkeypatch.setattr("app.owner_agent.generate", synthetic)
    session = owner.post("/api/conversations").json()["id"]
    response = owner.post(
        f"/api/conversations/{session}/messages",
        json={
            "key": str(uuid4()),
            "purpose": "Operations",
            "owner_agent": True,
            "content": "Compare the current Coach with the other model.",
        },
    )
    assert response.status_code == 202
    assert run_once(response.json()["request_id"])
    result = owner.get(f"/api/owner/tasks/{response.json()['request_id']}").json()
    assert len(contexts) == 2
    assert "not group IDs" in contexts[1]["action_errors"][0]
    assert result["status"] == "Ready"
    assert result["actions"][0]["name"] == "comparison.run_current"
    assert result["comparisons"][0]["planned_calls"] == 6


def test_ambiguous_provider_alias_needs_clarification(comparison, monkeypatch):
    from app import owner_comparison

    _, action, _ = comparison
    alias = json.loads(action.arguments)["model"]
    monkeypatch.setattr(
        owner_comparison,
        "catalog",
        lambda settings: [
            {"id": "model-a", "label": "Model A", "provider": alias, "configured": True},
            {"id": "model-b", "label": "Model B", "provider": alias, "configured": True},
        ],
    )
    with pytest.raises(ValueError, match="ambiguous"):
        validate_action(action)


def test_unreviewed_snapshot_and_cross_role_versions_cannot_execute(comparison):
    owner, action, _ = comparison
    identifier, digest, _ = task(owner, [validate_action(action).model_dump()])
    assert owner.get(f"/api/owner/tasks/{identifier}").json()["status"] == "NeedsCorrection"
    assert (
        owner.post(
            f"/api/owner/tasks/{identifier}/decision", json={"fingerprint": digest, "decision": "Execute"}
        ).status_code
        == 409
    )
    ids = []
    for role in ["Coach", "Nutrition"]:
        response = owner.post(
            "/api/owner/agent-versions",
            json={
                "role": role,
                "label": "Isolated cross-role test",
                "model": get_settings().default_model,
                "instructions": str(uuid4()),
            },
        )
        assert response.status_code == 201
        ids.append(response.json()["id"])
    legacy = ToolInput(
        name="comparison.prepare", target=ids[1], arguments=json.dumps({"baseline_id": ids[0]})
    )
    with pytest.raises(ValueError, match="same role"):
        validate_action(legacy)


@pytest.mark.parametrize("failures", [1, 4])
def test_owner_schema_failure_settles_known_usage_and_retries_with_bound(comparison, monkeypatch, failures):
    from test_openai_provider import usage

    from app import model_gateway
    from app.gemini import ProviderFailure, ProviderResult
    from app.models import ModelCall
    from app.owner_agent import OwnerTurn
    from app.worker import run_once

    owner, action, _ = comparison
    settings = get_settings()
    monkeypatch.setattr(settings, "model_provider", "openai")
    monkeypatch.setattr(settings, "model_calls_enabled", True)
    args = json.loads(action.arguments) | {"model": "gemini"}
    action = action.model_copy(update={"arguments": json.dumps(args)})
    attempts = []

    def send(self, payload, output_type):
        attempts.append(payload)
        if len(attempts) <= failures:
            raise ProviderFailure(
                "Invalid schema", usage=usage(), provider_id="synthetic-invalid-" + str(len(attempts))
            )
        return ProviderResult(
            "synthetic-valid",
            OwnerTurn(reply="Review the comparison.", goal="Compare models", actions=[action]),
            usage(),
        )

    monkeypatch.setattr(model_gateway.OpenAITransport, "send", send)
    monkeypatch.setattr(model_gateway.OpenAITransport, "count_input_tokens", lambda self, payload: 1000)
    session = owner.post("/api/conversations").json()["id"]
    response = owner.post(
        f"/api/conversations/{session}/messages",
        json={
            "key": str(uuid4()),
            "purpose": "Operations",
            "owner_agent": True,
            "content": "Compare current Coach with Gemini.",
        },
    )
    request_id = response.json()["request_id"]
    assert run_once(request_id)
    result = owner.get(f"/api/owner/tasks/{request_id}").json()
    assert len(attempts) == min(failures + 1, 4)
    assert result["status"] == ("Ready" if failures == 1 else "Explained")
    with transaction() as db:
        calls = list(db.scalars(select(ModelCall).where(ModelCall.request_id == request_id)))
        assert len(calls) == len(attempts)
        assert all(call.status == "Settled" and call.actual_micros > 0 for call in calls)
        assert sum(call.output is None for call in calls) == failures


def test_owner_unknown_usage_does_not_retry_or_invent_charge(comparison, monkeypatch):
    from redis import Redis

    from app import model_gateway
    from app.gemini import ProviderFailure
    from app.models import ModelCall
    from app.rate_limit import ModelRateLimiter
    from app.worker import run_once

    owner, _, _ = comparison
    settings = get_settings()
    monkeypatch.setattr(settings, "model_provider", "openai")
    monkeypatch.setattr(settings, "model_calls_enabled", True)
    attempts = []

    def send(self, payload, output_type):
        attempts.append(payload)
        raise ProviderFailure("No accounting receipt")

    monkeypatch.setattr(model_gateway.OpenAITransport, "send", send)
    monkeypatch.setattr(model_gateway.OpenAITransport, "count_input_tokens", lambda self, payload: 1000)
    session = owner.post("/api/conversations").json()["id"]
    response = owner.post(
        f"/api/conversations/{session}/messages",
        json={
            "key": str(uuid4()),
            "purpose": "Operations",
            "owner_agent": True,
            "content": "Compare Canary Coach with Gemini.",
        },
    )
    request_id = response.json()["request_id"]
    assert run_once(request_id)
    with transaction() as db:
        call = db.scalar(select(ModelCall).where(ModelCall.request_id == request_id))
        assert len(attempts) == 1 and call.status == "UsageUnknown" and call.actual_micros is None
        work = db.scalar(select(WorkItem).where(WorkItem.request_id == request_id))
        assert work.status == "Waiting" and work.wait_reason == "UsageReconciliation"
        call_id = call.id
    # Only remove this synthetic test's execution slot, never manufacture settled usage.
    owner.post(f"/api/requests/{request_id}/cancel")
    with Redis.from_url(settings.redis_url) as connection:
        ModelRateLimiter(connection).release(call_id)


def test_comparison_context_survives_superseded_and_explained_turns(comparison, monkeypatch):
    from app.models import ModelCall
    from app.owner_agent import OwnerTurn
    from app.worker import run_once

    owner, action, _ = comparison
    old, _, _ = saved(owner, action)
    with transaction() as db:
        row = db.get(Request, old)
        session = row.session_id
        row.result = row.result | {"owner_task": row.result["owner_task"] | {"status": "Superseded"}}
    explained, _, _ = task(owner, [], session_id=session)
    with transaction() as db:
        row = db.get(Request, explained)
        row.result = row.result | {"owner_task": row.result["owner_task"] | {"status": "Explained"}}
    monkeypatch.setattr(get_settings(), "model_calls_enabled", True)
    contexts = []

    def synthetic(claim, instruction, context, output_type, max_output_tokens):
        contexts.append(context)
        with transaction() as db:
            request = db.get(Request, claim.request_id)
            call = ModelCall(
                request_id=request.id,
                user_id=request.user_id,
                role=claim.role,
                model="synthetic-test",
                status="Settled",
                reserved_micros=0,
                actual_micros=0,
                price_version="synthetic-test",
            )
            db.add(call)
            db.flush()
            return OwnerTurn(
                reply="New comparison preview.", goal="Restore comparison target", actions=[action]
            ), call.id

    monkeypatch.setattr("app.owner_agent.generate", synthetic)
    response = owner.post(
        f"/api/conversations/{session}/messages",
        json={
            "key": str(uuid4()),
            "purpose": "Operations",
            "owner_agent": True,
            "content": "Change it back to the previous model and wait for confirmation.",
        },
    )
    assert run_once(response.json()["request_id"])
    assert contexts[0]["comparison_context"]["source_request_id"] == old
    assert contexts[0]["comparison_context"]["proposal_status"] == "Superseded"
    assert contexts[0]["comparison_context"]["role"] == "Coach"
    assert contexts[0]["comparison_context"]["group_id"] == json.loads(action.arguments)["group_id"]
    assert owner.get(f"/api/owner/tasks/{response.json()['request_id']}").json()["status"] == "Ready"


def test_invalid_target_revision_retires_old_preview_without_model_supplied_id(comparison, monkeypatch):
    from app.models import ModelCall
    from app.owner_agent import OwnerTurn
    from app.worker import run_once

    owner, action, _ = comparison
    old, _, _ = saved(owner, action)
    with transaction() as db:
        session = db.get(Request, old).session_id
    monkeypatch.setattr(get_settings(), "model_calls_enabled", True)

    def synthetic(claim, instruction, context, output_type, max_output_tokens):
        with transaction() as db:
            request = db.get(Request, claim.request_id)
            call = ModelCall(
                request_id=request.id,
                user_id=request.user_id,
                role=claim.role,
                model="synthetic-test",
                status="Settled",
                reserved_micros=0,
                actual_micros=0,
                price_version="synthetic-test",
            )
            db.add(call)
            db.flush()
            return OwnerTurn(
                reply="That is the current model. Choose a different model.",
                goal="Change comparison target",
                revises_comparison=True,
            ), call.id

    monkeypatch.setattr("app.owner_agent.generate", synthetic)
    response = owner.post(
        f"/api/conversations/{session}/messages",
        json={
            "key": str(uuid4()),
            "purpose": "Operations",
            "owner_agent": True,
            "content": "Change the target to the current model.",
        },
    )
    assert run_once(response.json()["request_id"])
    assert owner.get(f"/api/owner/tasks/{old}").json()["status"] == "Superseded"
    assert owner.get(f"/api/owner/tasks/{response.json()['request_id']}").json()["status"] == "Explained"
