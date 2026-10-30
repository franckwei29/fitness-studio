"""Short-lived database units of work."""

import logging
from contextlib import contextmanager
from contextvars import ContextVar
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


_joined = ContextVar("owner_business_transaction", default=None)


@contextmanager
def atomic_business_transaction():
    """Join existing domain handlers into one owner action + receipt commit."""
    with transaction() as session:
        token = _joined.set(session)
        try:
            yield session
        finally:
            _joined.reset(token)


@contextmanager
def transaction():
    shared = _joined.get()
    if shared is not None:
        yield shared
        return
    with Session(engine(), expire_on_commit=False) as session, session.begin():
        yield session
        resume = session.info.get("resume_waiting", False)
    if resume:
        from app.wait_recovery import resume_waiting

        try:
            from app.intent_router import resume_admission_goals

            resume_admission_goals()
            resume_waiting(force=True)
        except Exception as exc:
            # The setting is already committed. Scheduled maintenance retries recovery.
            logging.getLogger(__name__).warning("Task recovery deferred (%s).", type(exc).__name__)
