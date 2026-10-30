"""Persistent policies, inheritance, admission and owner chat use one service."""

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from redis import Redis
from sqlalchemy import select

from app.bootstrap import bootstrap
from app.call_limit_chat import quota_card
from app.call_limits import (
    LimitChange,
    change,
    dispatch_limits,
    effective_account,
    entry,
    initialize,
    window_usage,
)
from app.database import transaction
from app.main import app
from app.models import AuditEvent, CallLimit, Member, MemberGroup, ProfileSnapshot, User
from app.rate_limit import ModelRateLimiter
from app.settings import get_settings


@pytest.fixture
def studio():
    bootstrap()
    with transaction() as db:
        owner = db.scalar(select(User).where(User.email == get_settings().owner_email))
        groups = [MemberGroup(name=f"QuotaGroup{uuid4().hex[:8]}", configuration={}) for _ in range(2)]
        users = [
            User(
                email=f"quota-{uuid4()}@example.test",
                display_name=f"QuotaAlice {uuid4().hex[:8]}",
                role="Member",
                password_hash="unused",
            )
            for _ in range(2)
        ]
        db.add_all(groups + users)
        db.flush()
        members = [Member(user_id=u.id, group_id=groups[0].id) for u in users]
        db.add_all(members)
        db.flush()
        for m in members:
            db.add(ProfileSnapshot(member_id=m.id, version=1, facts={}, sources={}, confirmed_by=m.user_id))
        initialize(db)
        ids = {
            "owner": owner.id,
            "groups": [g.id for g in groups],
            "members": [m.id for m in members],
            "users": [u.id for u in users],
            "name": users[0].display_name,
        }
    with TestClient(app) as client:
        auth = client.post(
            "/api/auth/login",
            json={
                "email": get_settings().owner_email,
                "password": get_settings().owner_password.get_secret_value(),
            },
        )
        client.headers["x-csrf-token"] = auth.json()["csrf_token"]
        yield client, ids


def current(client, scope):
    result = client.get("/api/owner/call-limits/entry", params={"scope": scope})
    assert result.status_code == 200, result.text
    return result.json()


def update(client, scope, calls, rule=None):
    rule = rule or current(client, scope)
    return client.post(
        "/api/owner/call-limits",
        json={
            "scope_key": scope,
            "calls": calls,
            "expected_fingerprint": rule["fingerprint"],
            "reason": "Quota acceptance test",
        },
    )


def test_group_personal_restore_and_search(studio):
    client, ids = studio
    group = f"group:{ids['groups'][0]}"
    person = f"member:{ids['members'][0]}"
    assert update(client, group, 250).status_code == 200
    assert current(client, person)["limit"] == 250
    assert current(client, person)["source"] == "Group default"
    assert update(client, person, 300).status_code == 200
    assert current(client, group)["inheriting_members"] == 1
    response = update(client, group, 400)
    assert response.status_code == 200 and "1 members" in response.json()["message"]
    assert current(client, person)["limit"] == 300
    matches = client.get(
        "/api/owner/call-limits", params={"query": ids["name"].split()[1], "custom_only": True}
    ).json()
    assert [r["member_id"] for r in matches["members"]] == [ids["members"][0]]
    assert update(client, person, None).json()["source"] == "Group default"
    assert current(client, person)["limit"] == 400


def test_stale_edits_restore_aba_and_group_move_keep_usage(studio):
    client, ids = studio
    person = f"member:{ids['members'][0]}"
    old = current(client, person)
    assert update(client, person, 300, old).status_code == 200
    assert update(client, person, None).status_code == 200
    assert update(client, person, 500, old).status_code == 409
    redis = Redis.from_url(get_settings().redis_url)
    gate = ModelRateLimiter(redis)
    sec, micro = redis.time()
    key = gate.account_key(ids["users"][0])
    redis.zadd(key, {"quota-history": sec * 1000 + micro // 1000})
    try:
        assert update(client, f"group:{ids['groups'][1]}", 450).status_code == 200
        snapshot = current(client, person)
        result = client.post(
            f"/api/owner/members/{ids['members'][0]}/group",
            json={
                "group_id": ids["groups"][1],
                "expected_group_id": ids["groups"][0],
                "reason": "Move without resetting usage",
            },
        )
        assert result.status_code == 200, result.text
        assert result.json()["allowance"]["limit"] == 450
        assert redis.zcard(key) == 1
        assert update(client, person, 500, snapshot).status_code == 409
        assert update(client, person, 700).status_code == 200
        result = client.post(
            f"/api/owner/members/{ids['members'][0]}/group",
            json={
                "group_id": ids["groups"][0],
                "expected_group_id": ids["groups"][1],
                "reason": "Keep personal allowance",
            },
        )
        assert result.json()["allowance"]["limit"] == 700
        assert result.json()["allowance"]["source"] == "Custom allowance"
        assert redis.zcard(key) == 1
    finally:
        redis.delete(key)
        redis.close()


def test_initial_owner_and_group_are_separate_persistent_rules(studio, monkeypatch):
    client, ids = studio
    scope = f"group:{ids['groups'][0]}"
    assert update(client, scope, 275).status_code == 200
    settings = get_settings()
    monkeypatch.setattr(settings, "model_account_daily_call_limit", 999)
    with transaction() as db:
        initialize(db)
        assert entry(db, scope)["limit"] == 275
        assert entry(db, f"owner:{ids['owner']}")["limit"] == 200
        assert entry(db, "initial")["limit"] == 200
        new = User(email=f"pre-{uuid4()}@example.test", display_name="Before membership", password_hash="x")
        db.add(new)
        db.flush()
        assert effective_account(db, new.id)["source"] == "Initial account allowance"
        assert effective_account(db, new.id)["limit"] == 200


def test_limits_immediate_model_independent_and_lowering_preserves_counters(studio):
    client, ids = studio
    scope = "model:openai:gpt-6-luna"
    before = current(client, scope)
    redis = Redis.from_url(get_settings().redis_url)
    gate = ModelRateLimiter(redis, f"quota-test:{uuid4()}")

    def attempt(model="gpt-6-luna", provider="openai"):
        call = str(uuid4())
        with transaction() as db:
            limits = dispatch_limits(db, ids["users"][0], provider, model)
            result = gate.acquire(call, limits, ids["users"][0], provider=provider, model=model)
        if result == "Granted":
            gate.release(call)
        return result

    try:
        assert update(client, scope, 2).status_code == 200
        assert [attempt() for _ in range(3)] == ["Granted", "Granted", "Hour"]
        assert update(client, scope, 5).status_code == 200
        assert attempt() == "Granted"
        assert update(client, scope, 1).status_code == 200
        assert attempt() == "Hour"
        stats = window_usage(redis, gate.model_key("openai", "gpt-6-luna"), 3600, 1)
        assert stats["used"] == 3 and stats["next_capacity_at"]
        assert attempt("gemini-3.5-flash-lite", "gemini") == "Granted"
        assert redis.zcard(gate.account_key(ids["users"][0])) == 4
        assert update(client, f"member:{ids['members'][0]}", 4).status_code == 200
        assert attempt("gemini-3.5-flash-lite", "gemini") == "AccountDay"
        assert redis.zcard(gate.account_key(ids["users"][0])) == 4
    finally:
        update(client, scope, before["limit"])
        keys = list(redis.scan_iter(gate.namespace + ":*"))
        if keys:
            redis.delete(*keys)
        redis.close()


def test_atomic_conflicting_policy_edits(studio):
    _, ids = studio
    scope = f"group:{ids['groups'][0]}"
    with transaction() as db:
        original = entry(db, scope)

    def edit(value):
        from fastapi import HTTPException

        try:
            with transaction() as db:
                actor = db.get(User, ids["owner"])
                change(
                    db,
                    LimitChange(
                        scope_key=scope,
                        calls=value,
                        expected_fingerprint=original["fingerprint"],
                        reason="Concurrent edit",
                    ),
                    actor,
                )
            return 200
        except HTTPException as exc:
            return exc.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(edit, [333, 444]))
    assert sorted(results) == [200, 409]


@pytest.mark.parametrize("calls", [0, -1, 1.5, True, 1000001])
def test_invalid_calls_rejected(studio, calls):
    client, ids = studio
    assert update(client, f"group:{ids['groups'][0]}", calls).status_code == 422


def test_permissions_and_unknown_scope(studio):
    client, ids = studio
    with TestClient(app) as anonymous:
        assert anonymous.get("/api/owner/call-limits").status_code == 401
        response = anonymous.post(
            "/api/auth/register",
            json={
                "email": f"permission-{uuid4()}@example.test",
                "password": "Acceptance-test-123!",
                "display_name": "Allowance permission test",
            },
        )
        anonymous.headers["x-csrf-token"] = response.json()["csrf_token"]
        assert anonymous.get("/api/owner/call-limits").status_code == 403
        assert (
            anonymous.post(
                "/api/owner/call-limits",
                json={
                    "scope_key": "initial",
                    "calls": 500,
                    "expected_fingerprint": "a" * 64,
                    "reason": "Not owner",
                },
            ).status_code
            == 403
        )
    assert client.get("/api/owner/call-limits/entry", params={"scope": "group:missing"}).status_code == 404
    assert update(client, f"group:{ids['groups'][0]}", None).status_code == 422


def test_chat_preview_confirm_audit_receipt_and_shared_page_state(studio):
    client, ids = studio
    scope = f"group:{ids['groups'][0]}"
    with transaction() as db:
        name = db.get(MemberGroup, ids["groups"][0]).name
    session = client.post("/api/conversations").json()["id"]
    response = client.post(
        f"/api/conversations/{session}/messages",
        json={
            "key": str(uuid4()),
            "purpose": "Operations",
            "content": f"Set {name} to 222 calls per member per day.",
        },
    )
    assert response.status_code == 202, response.text
    history = client.get(f"/api/conversations/{session}").json()
    card = history["messages"][-1]["cards"][0]
    assert card["type"] == "OwnerQuota" and card["entries"][0]["proposed_calls"] == 222
    rule = card["entries"][0]["rule"]
    assert current(client, scope)["limit"] != 222
    assert update(client, scope, 222, rule).status_code == 200
    payload = {"key": str(uuid4()), "target": {"page": "budgets", "id": scope, "section": "call-limit"}}
    receipt = client.post(f"/api/owner/conversations/{session}/receipts", json=payload)
    assert receipt.status_code == 200, receipt.text
    assert "222" in receipt.text
    assert current(client, scope)["limit"] == 222
    with transaction() as db:
        audits = list(
            db.scalars(
                select(AuditEvent).where(
                    AuditEvent.event_type == "call_limit.changed",
                    AuditEvent.subject_id
                    == db.scalar(select(CallLimit.id).where(CallLimit.scope_key == scope)),
                )
            )
        )
        assert audits[-1].details["calls"] == 222
        actor = db.get(User, ids["owner"])
        assert quota_card(db, "Increase the limit to 500.", actor)["entries"] == []
        parsed = quota_card(db, f"Give {ids['name']} 300 calls per day.", actor)
        assert parsed["entries"][0]["rule"]["scope_key"] == f"member:{ids['members'][0]}"
        assert parsed["entries"][0]["proposed_calls"] == 300
        restore = quota_card(db, f"Restore {ids['name']}'s group allowance.", actor)
        assert restore["entries"][0]["proposed_calls"] is None
        for text in ["Set Luna hourly limit to -1 calls.", "Set Luna daily limit to 500 calls."]:
            assert quota_card(db, text, actor)["entries"] == []
        assert (
            quota_card(db, "Set GPT-6 Luna hourly limit to 500 calls.", actor)["entries"][0]["proposed_calls"]
            == 500
        )


def test_gateway_applies_latest_saved_limit_before_synthetic_transport(studio, monkeypatch):
    from pydantic import SecretStr

    from app.agent_contracts import ChatReply
    from app.gemini import ProviderResult
    from app.worker import run_once

    client, ids = studio
    owner_scope = f"owner:{ids['owner']}"
    original = current(client, owner_scope)
    settings = get_settings().model_copy(
        update={"model_calls_enabled": True, "gemini_api_key": SecretStr("synthetic-not-sent")}
    )
    monkeypatch.setattr("app.model_gateway.get_settings", lambda: settings)
    connection = Redis.from_url(settings.redis_url)
    gate = ModelRateLimiter(connection, f"quota-gateway:{uuid4()}")
    monkeypatch.setattr("app.model_gateway.ModelRateLimiter", lambda _: gate)
    sent = []

    def transport(*args):
        sent.append(True)
        return ProviderResult(
            "synthetic-quota",
            ChatReply(reply="Saved operational explanation."),
            dict(
                total_input_tokens=10,
                total_output_tokens=10,
                total_tokens=20,
                total_thought_tokens=0,
                total_cached_tokens=0,
                total_tool_use_tokens=0,
            ),
        )

    monkeypatch.setattr("app.model_gateway.GeminiTransport.send", transport)
    budgets = []
    for scope in ["global", "role:TechnicalManager"]:
        rows = client.get("/api/owner/budgets").json()
        row = next((r for r in rows if r["scope_key"] == scope), None)
        budgets.append((scope, row["limit_micros"] if row else 0))
        assert (
            client.post(
                "/api/owner/budgets",
                json={
                    "scope_key": scope,
                    "limit_micros": 10000000,
                    "expected_fingerprint": row["fingerprint"] if row else None,
                    "reason": "Synthetic dispatch acceptance",
                },
            ).status_code
            == 200
        )
    session = client.post("/api/conversations").json()["id"]

    def submit():
        response = client.post(
            f"/api/conversations/{session}/messages",
            json={
                "key": str(uuid4()),
                "purpose": "Operations",
                "operations_ai": True,
                "content": "Explain the supplied synthetic operations evidence.",
            },
        )
        assert response.status_code == 202, response.text
        identifier = response.json()["request_id"]
        assert run_once(identifier)
        from app.models import Request

        with transaction() as db:
            return db.get(Request, identifier).status

    try:
        assert update(client, owner_scope, 2).status_code == 200
        assert submit() == "Completed"
        assert update(client, owner_scope, 1).status_code == 200
        assert submit() == "Waiting"
        assert len(sent) == 1
        assert connection.zcard(gate.account_key(ids["owner"])) == 1
        assert update(client, owner_scope, 3).status_code == 200
        assert submit() == "Completed"
        assert len(sent) == 2
    finally:
        update(client, owner_scope, original["limit"])
        for scope, amount in budgets:
            row = next(r for r in client.get("/api/owner/budgets").json() if r["scope_key"] == scope)
            client.post(
                "/api/owner/budgets",
                json={
                    "scope_key": scope,
                    "limit_micros": amount,
                    "expected_fingerprint": row["fingerprint"],
                    "reason": "Restore synthetic acceptance setting",
                },
            )
        keys = list(connection.scan_iter(gate.namespace + ":*"))
        if keys:
            connection.delete(*keys)
        connection.close()
