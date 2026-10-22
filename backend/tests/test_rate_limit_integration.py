"""Rate limits are shared atomically across independent Redis clients."""

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
    connection.delete(*gate.keys)
    connection.close()


def test_shared_concurrency_limit_and_replay(limiter):
    limits = DispatchLimits(20, 30, 1)
    identifiers = [str(uuid4()) for _ in range(8)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda identifier: limiter.acquire(identifier, limits), identifiers))
    assert results.count("Granted") == 1
    granted = identifiers[results.index("Granted")]
    assert limiter.active_calls() == [granted]
    assert limiter.acquire(granted, limits) == "Duplicate"
    limiter.release(granted)
    limiter.release(granted)
    assert limiter.active_calls() == []
    assert limiter.acquire(granted, limits) == "Duplicate"
    assert limiter.acquire(str(uuid4()), limits) == "Granted"


def test_minute_history_survives_release(limiter):
    limits = DispatchLimits(2, 10, 1)
    for _ in range(2):
        identifier = str(uuid4())
        assert limiter.acquire(identifier, limits) == "Granted"
        limiter.release(identifier)
    assert limiter.acquire(str(uuid4()), limits) == "Minute"


def test_daily_history_is_independent_of_minute_window(limiter):
    limits = DispatchLimits(5, 2, 1)
    for _ in range(2):
        identifier = str(uuid4())
        assert limiter.acquire(identifier, limits) == "Granted"
        limiter.release(identifier)
    limiter.redis.delete(limiter.keys[0])
    assert limiter.acquire(str(uuid4()), limits) == "Day"


def test_active_slot_survives_client_recreation(limiter):
    identifier = str(uuid4())
    limits = DispatchLimits(5, 10, 1)
    assert limiter.acquire(identifier, limits) == "Granted"
    connection = Redis.from_url(get_settings().redis_url)
    try:
        replacement = ModelRateLimiter(connection, limiter.keys[0].removesuffix(":minute"))
        assert replacement.active_calls() == [identifier]
        assert replacement.acquire(str(uuid4()), limits) == "Concurrency"
    finally:
        connection.close()
