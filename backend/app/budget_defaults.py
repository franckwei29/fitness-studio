"""Initial cumulative USD caps; never reset existing limits or accounting."""

from sqlalchemy import select
from sqlalchemy.dialects.mysql import insert

from app.models import BudgetAccount, Member, MemberGroup

STUDIO_LIMIT_MICROS = 20_000_000
DEFAULT_LIMIT_MICROS = 10_000_000


def ensure_budget(db, scope_key, limit_micros=DEFAULT_LIMIT_MICROS):
    statement = insert(BudgetAccount).values(scope_key=scope_key, limit_micros=limit_micros)
    # A duplicate is a no-op, including deliberately disabled (zero) budgets.
    db.execute(statement.on_duplicate_key_update(scope_key=statement.inserted.scope_key))


def initialize_budgets(db):
    scopes = {"global": STUDIO_LIMIT_MICROS}
    scopes.update({f"role:{role}": DEFAULT_LIMIT_MICROS for role in ("Intake", "Coach", "Nutrition", "TechnicalManager")})
    scopes.update({f"group:{identifier}": DEFAULT_LIMIT_MICROS for identifier in db.scalars(select(MemberGroup.id))})
    scopes.update({f"member:{identifier}": DEFAULT_LIMIT_MICROS for identifier in db.scalars(select(Member.id))})
    existing = set(db.scalars(select(BudgetAccount.scope_key)))
    for scope, limit in scopes.items():
        if scope not in existing:
            ensure_budget(db, scope, limit)
