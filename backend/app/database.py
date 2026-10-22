"""Short-lived database units of work."""

from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session

from app.settings import get_settings


class Base(DeclarativeBase):
    pass


@lru_cache
def engine():
    return create_engine(
        get_settings().database_url.get_secret_value(),
        pool_pre_ping=True,
        pool_recycle=1800,
        isolation_level="READ COMMITTED",
    )


@contextmanager
def transaction():
    with Session(engine(), expire_on_commit=False) as session, session.begin():
        yield session
