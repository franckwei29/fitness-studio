"""Preserve recent account usage when upgrading the legacy global-only quota."""

from redis import Redis

from app.database import transaction
from app.models import ModelCall
from app.rate_limit import ModelRateLimiter
from app.settings import get_settings


def seed_account_history(db, gate):
    seconds, micros = gate.redis.time()
    cutoff = seconds * 1000 + micros // 1000 - 86400000
    seeded = 0
    with gate.redis.pipeline() as pipeline:
        for identifier, score in gate.redis.zrangebyscore(
            gate.keys[1], f"({cutoff}", "+inf", withscores=True
        ):
            identifier = identifier.decode() if isinstance(identifier, bytes) else identifier
            call = db.get(ModelCall, identifier)
            if not call:
                continue
            key = gate.account_key(call.user_id)
            pipeline.zadd(key, {identifier: score}, nx=True)
            pipeline.pexpire(key, 86400001)
            model = getattr(call, "model", get_settings().gemini_model)
            provider = getattr(call, "provider", "gemini")
            hourly = gate.model_key(provider, model)
            if score > cutoff + 82800000:
                pipeline.zadd(hourly, {identifier: score}, nx=True)
                pipeline.pexpire(hourly, 3600001)
            seeded += 1
        pipeline.execute()
    return seeded


if __name__ == "__main__":
    with Redis.from_url(get_settings().redis_url) as redis, transaction() as db:
        print({"recent_calls_preserved": seed_account_history(db, ModelRateLimiter(redis))})
