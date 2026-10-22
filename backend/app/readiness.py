"""Verify required persistence services and the exact installed schema revision."""

from pathlib import Path

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from redis import Redis

from app.database import engine
from app.settings import get_settings


def check_readiness():
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    expected = set(ScriptDirectory.from_config(config).get_heads())
    with engine().connect() as connection:
        actual = set(MigrationContext.configure(connection).get_current_heads())
        if not expected or actual != expected:
            raise RuntimeError("Database migrations are not current.")
    with Redis.from_url(get_settings().redis_url, socket_connect_timeout=2, socket_timeout=2) as client:
        if not client.ping():
            raise RuntimeError("The queue and checkpoint service is unavailable.")
