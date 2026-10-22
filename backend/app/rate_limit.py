"""Atomic cross-process model dispatch limits, independent of monetary budgets."""

from dataclasses import dataclass

from redis import Redis

# Redis time avoids clock disagreement between API and worker processes. Active
# permits never expire automatically: a crashed worker must be reconciled before
# its slot can be released, since the provider may still be processing its call.
ACQUIRE = """
local stamp = redis.call('TIME')
local now = tonumber(stamp[1]) * 1000 + math.floor(tonumber(stamp[2]) / 1000)
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now - 60000)
redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', now - 86400000)
if redis.call('SISMEMBER', KEYS[3], ARGV[1]) == 1 or
   redis.call('ZSCORE', KEYS[2], ARGV[1]) then return 'Duplicate' end
if redis.call('SCARD', KEYS[3]) >= tonumber(ARGV[4]) then return 'Concurrency' end
if redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[2]) then return 'Minute' end
if redis.call('ZCARD', KEYS[2]) >= tonumber(ARGV[3]) then return 'Day' end
redis.call('ZADD', KEYS[1], now, ARGV[1])
redis.call('ZADD', KEYS[2], now, ARGV[1])
redis.call('SADD', KEYS[3], ARGV[1])
redis.call('PEXPIRE', KEYS[1], 60001)
redis.call('PEXPIRE', KEYS[2], 86400001)
return 'Granted'
"""


@dataclass(frozen=True)
class DispatchLimits:
    per_minute: int
    per_day: int
    concurrent: int

    def __post_init__(self):
        if any(
            type(value) is not int or value < 1 for value in (self.per_minute, self.per_day, self.concurrent)
        ):
            raise ValueError("Dispatch limits must be positive integers.")


class ModelRateLimiter:
    def __init__(self, redis: Redis, namespace="studio:model"):
        self.redis = redis
        self.keys = [f"{namespace}:minute", f"{namespace}:day", f"{namespace}:active"]
        self.acquire_script = redis.register_script(ACQUIRE)

    def acquire(self, call_id: str, limits: DispatchLimits) -> str:
        if not call_id:
            raise ValueError("A model call identifier is required.")
        result = self.acquire_script(
            keys=self.keys,
            args=[
                call_id,
                limits.per_minute,
                limits.per_day,
                limits.concurrent,
            ],
        )
        return result.decode() if isinstance(result, bytes) else result

    def release(self, call_id: str):
        """Release a completed/reconciled call; retain its rate-limit history."""
        self.redis.srem(self.keys[2], call_id)

    def active_calls(self):
        return sorted(
            value.decode() if isinstance(value, bytes) else value
            for value in self.redis.smembers(self.keys[2])
        )
