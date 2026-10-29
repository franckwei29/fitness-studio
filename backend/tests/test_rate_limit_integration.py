"""Hourly and account quotas are shared atomically across Redis clients."""

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from redis import Redis

from app.rate_limit import DispatchLimits, ModelRateLimiter
from app.settings import get_settings


@pytest.fixture
def limiter():
    connection = Redis.from_url(get_settings().redis_url)
    gate = ModelRateLimiter(connection, f"test:model:{uuid4()}")
    yield gate
    keys = list(connection.scan_iter(gate.namespace + ":*"))
    if keys:
        connection.delete(*keys)
    connection.close()


def test_shared_concurrency_limit_and_replay(limiter):
    limits = DispatchLimits(20, 100, 1)
    identifiers = [str(uuid4()) for _ in range(8)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda identifier: limiter.acquire(identifier, limits, "a"), identifiers))
    assert results.count("Granted") == 1
    granted = identifiers[results.index("Granted")]
    assert limiter.active_calls() == [granted]
    assert limiter.acquire(granted, limits, "a") == "Duplicate"
    limiter.release(granted)
    limiter.release(granted)
    assert limiter.active_calls() == []
    assert limiter.acquire(granted, limits, "b") == "Duplicate"
    assert limiter.acquire(str(uuid4()), limits, "b") == "Granted"


def test_global_ten_per_rolling_hour_across_accounts(limiter):
    limits = DispatchLimits(10, 100, 1)
    for index in range(10):
        identifier = str(uuid4())
        assert limiter.acquire(identifier, limits, "a" if index % 2 else "b") == "Granted"
        limiter.release(identifier)
    assert limiter.acquire(str(uuid4()), limits, "c") == "Hour"
    stamp = limiter.redis.time()
    old = (stamp[0] * 1000 + stamp[1] // 1000) - 3600001
    for key in limiter.keys[:2]:
        for identifier in limiter.redis.zrange(key, 0, -1):
            limiter.redis.zadd(key, {identifier: old})
    assert limiter.acquire(str(uuid4()), limits, "c") == "Granted"


def test_account_day_is_independent_and_hour_expiry_does_not_reset_it(limiter):
    limits = DispatchLimits(1000, 100, 1)
    for _ in range(100):
        identifier = str(uuid4())
        assert limiter.acquire(identifier, limits, "a") == "Granted"
        limiter.release(identifier)
    limiter.redis.delete(limiter.keys[0])
    assert limiter.acquire(str(uuid4()), limits, "a") == "AccountDay"
    other = str(uuid4())
    assert limiter.acquire(other, limits, "b") == "Granted"
    limiter.release(other)
    stamp = limiter.redis.time()
    old = (stamp[0] * 1000 + stamp[1] // 1000) - 86400001
    for identifier in limiter.redis.zrange(limiter.account_key("a"), 0, -1):
        limiter.redis.zadd(limiter.account_key("a"), {identifier: old})
    assert limiter.acquire(str(uuid4()), limits, "a") == "Granted"


def test_concurrent_account_limit_does_not_overshoot(limiter):
    limits = DispatchLimits(1000, 100, 200)

    def attempt(_):
        identifier = str(uuid4())
        result = limiter.acquire(identifier, limits, "a")
        if result == "Granted":
            limiter.release(identifier)
        return result

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(attempt, range(120)))
    assert results.count("Granted") == 100 and results.count("AccountDay") == 20


def test_legacy_history_and_active_slots_survive_recreation(limiter):
    stamp = limiter.redis.time()
    score = stamp[0] * 1000 + stamp[1] // 1000
    limiter.redis.zadd(limiter.keys[1], {"legacy": score})
    assert limiter.acquire("legacy", DispatchLimits(10, 100, 1), "a") == "Duplicate"
    assert limiter.acquire("new", DispatchLimits(1, 100, 1), "a") == "Hour"
    identifier = str(uuid4())
    limits = DispatchLimits(10, 100, 1)
    assert limiter.acquire(identifier, limits, "a") == "Granted"
    connection = Redis.from_url(get_settings().redis_url)
    try:
        replacement = ModelRateLimiter(connection, limiter.namespace)
        assert replacement.active_calls() == [identifier]
        assert replacement.acquire(str(uuid4()), limits, "b") == "Concurrency"
    finally:
        connection.close()


def test_upgrade_preserves_account_history_idempotently(limiter):
    from types import SimpleNamespace

    from app.quota_migration import seed_account_history

    stamp = limiter.redis.time()
    score = stamp[0] * 1000 + stamp[1] // 1000
    limiter.redis.zadd(limiter.keys[1], {"old-a": score, "old-b": score, "expired": score - 86400001})

    class Database:
        def get(self, model, identifier):
            return SimpleNamespace(user_id="a" if identifier == "old-a" else "b")

    assert seed_account_history(Database(), limiter) == 2
    assert seed_account_history(Database(), limiter) == 2
    assert limiter.redis.zcard(limiter.account_key("a")) == 1
    assert limiter.redis.zcard(limiter.account_key("b")) == 1
    assert limiter.acquire("new-a", DispatchLimits(10, 1, 1), "a") == "AccountDay"


def test_models_have_independent_hundred_call_hour_but_share_account_day(limiter):
    limits = DispatchLimits(100, 200, 1)
    for model in ("gpt-6-luna", "gemini-3.5-flash-lite"):
        provider = "openai" if model.startswith("gpt") else "gemini"
        for _ in range(100):
            identifier = str(uuid4())
            assert limiter.acquire(identifier, limits, "a", provider=provider, model=model) == "Granted"
            limiter.release(identifier)
        assert limiter.acquire(str(uuid4()), limits, "b", provider=provider, model=model) == "Hour"
    # Hour expiry must not reset the shared account ledger.
    limiter.redis.delete(limiter.model_key("openai", "gpt-6-luna"))
    assert limiter.acquire(str(uuid4()), limits, "a", provider="openai", model="gpt-6-luna") == "AccountDay"
    assert limiter.acquire(str(uuid4()), limits, "b", provider="openai", model="gpt-6-luna") == "Granted"


def test_proven_undispatched_calls_return_call_quota(limiter):
    limits = DispatchLimits(1, 1, 1)
    options = {"provider": "openai", "model": "gpt-6-luna"}
    assert limiter.acquire("never-sent", limits, "a", **options) == "Granted"
    limiter.cancel_undispatched("never-sent", "a", **options)
    assert limiter.acquire("replacement", limits, "a", **options) == "Granted"
    limiter.release("replacement")
    assert limiter.acquire("third", limits, "a", **options) == "Hour"
