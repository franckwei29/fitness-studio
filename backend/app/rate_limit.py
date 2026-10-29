"""Atomic per-model hourly and cross-provider account daily dispatch quotas."""

from dataclasses import dataclass
from hashlib import sha256

from redis import Redis

# Keep the legacy global daily ledger for replay detection and hourly migration.
# Active slots never expire automatically: unknown provider usage needs review.
ACQUIRE = """
local stamp = redis.call('TIME')
local now = tonumber(stamp[1]) * 1000 + math.floor(tonumber(stamp[2]) / 1000)
redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', now - 86400000)
local hourly = KEYS[1]
if ARGV[5] == 'model' then
  hourly = KEYS[5]
else
  redis.call('ZUNIONSTORE', KEYS[1], 2, KEYS[1], KEYS[2], 'AGGREGATE', 'MAX')
end
redis.call('ZREMRANGEBYSCORE', hourly, '-inf', now - 3600000)
redis.call('ZREMRANGEBYSCORE', KEYS[4], '-inf', now - 86400000)
if redis.call('SISMEMBER', KEYS[3], ARGV[1]) == 1 or
   redis.call('ZSCORE', KEYS[2], ARGV[1]) then return 'Duplicate' end
if redis.call('SCARD', KEYS[3]) >= tonumber(ARGV[4]) then return 'Concurrency' end
if redis.call('ZCARD', hourly) >= tonumber(ARGV[2]) then return 'Hour' end
if redis.call('ZCARD', KEYS[4]) >= tonumber(ARGV[3]) then return 'AccountDay' end
redis.call('ZADD', hourly, now, ARGV[1])
redis.call('ZADD', KEYS[2], now, ARGV[1])
redis.call('ZADD', KEYS[4], now, ARGV[1])
redis.call('SADD', KEYS[3], ARGV[1])
redis.call('PEXPIRE', hourly, 3600001)
redis.call('PEXPIRE', KEYS[2], 86400001)
redis.call('PEXPIRE', KEYS[4], 86400001)
return 'Granted'
"""


@dataclass(frozen=True)
class DispatchLimits:
    per_hour: int
    per_account_day: int
    concurrent: int

    def __post_init__(self):
        if any(
            type(value) is not int or value < 1
            for value in (self.per_hour, self.per_account_day, self.concurrent)
        ):
            raise ValueError("Dispatch limits must be positive integers.")


class ModelRateLimiter:
    def __init__(self, redis: Redis, namespace="studio:model"):
        self.redis = redis
        self.namespace = namespace
        self.keys = [f"{namespace}:hour", f"{namespace}:day", f"{namespace}:active"]
        self.acquire_script = redis.register_script(ACQUIRE)

    def account_key(self, account_id: str):
        if not account_id:
            raise ValueError("An account identifier is required.")
        return f"{self.namespace}:account:{account_id}:day"

    def model_key(self, provider, model):
        if not provider or not model:
            raise ValueError("A provider and model are required.")
        digest = sha256(f"{provider}:{model}".encode()).hexdigest()[:32]
        return f"{self.namespace}:model:{digest}:hour"

    def acquire(
        self, call_id: str, limits: DispatchLimits, account_id: str, *, provider=None, model=None
    ) -> str:
        if not call_id:
            raise ValueError("A model call identifier is required.")
        result = self.acquire_script(
            keys=[
                *self.keys,
                self.account_key(account_id),
                self.model_key(provider, model) if provider else self.keys[0],
            ],
            args=[
                call_id,
                limits.per_hour,
                limits.per_account_day,
                limits.concurrent,
                "model" if provider else "legacy",
            ],
        )
        return result.decode() if isinstance(result, bytes) else result

    def release(self, call_id: str):
        """Release a completed/reconciled call; retain quota history."""
        self.redis.srem(self.keys[2], call_id)

    def cancel_undispatched(self, call_id, account_id, *, provider, model):
        self.redis.eval(
            """
            redis.call('SREM', KEYS[1], ARGV[1])
            for i = 2, #KEYS do redis.call('ZREM', KEYS[i], ARGV[1]) end
            """,
            4,
            self.keys[2],
            self.keys[1],
            self.account_key(account_id),
            self.model_key(provider, model),
            call_id,
        )

    def active_calls(self):
        return sorted(
            value.decode() if isinstance(value, bytes) else value
            for value in self.redis.smembers(self.keys[2])
        )
