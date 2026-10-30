"""Default caps are ready for use and cannot reset owner decisions or usage."""

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from sqlalchemy import select
from test_admission_integration import facts, member, submit  # noqa: F401

from app.budget_defaults import ensure_budget, initialize_budgets
from app.database import transaction
from app.models import BudgetAccount


def test_new_member_gets_budget_and_profile_updates_preserve_it(member):  # noqa: F811
    submit(member, facts())
    identifier = member.get("/api/me/profile").json()["member"]["id"]
    scope = f"member:{identifier}"
    with transaction() as db:
        row = db.scalar(select(BudgetAccount).where(BudgetAccount.scope_key == scope))
        assert row.limit_micros == 10_000_000
        row.limit_micros = 0
        row.spent_micros = 123
        row.reserved_micros = 456
    submit(member, facts(available_days=4))
    with transaction() as db:
        initialize_budgets(db)
        row = db.scalar(select(BudgetAccount).where(BudgetAccount.scope_key == scope))
        assert (row.limit_micros, row.spent_micros, row.reserved_micros) == (0, 123, 456)


def test_missing_defaults_and_repeated_initialization():
    # Test a clean scope without touching any existing production-like ledger.
    scope = f"test:default:{uuid4()}"
    with transaction() as db:
        ensure_budget(db, scope, 20_000_000)
        ensure_budget(db, scope, 10_000_000)
        row = db.scalar(select(BudgetAccount).where(BudgetAccount.scope_key == scope))
        assert row.limit_micros == 20_000_000
        assert row.spent_micros == row.reserved_micros == 0


def test_concurrent_default_creation_is_idempotent():
    scope = f"test:default:{uuid4()}"
    def create():
        with transaction() as db:
            ensure_budget(db, scope)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: create(), range(2)))
    with transaction() as db:
        rows = list(db.scalars(select(BudgetAccount).where(BudgetAccount.scope_key == scope)))
        assert len(rows) == 1
        assert rows[0].limit_micros == 10_000_000


def test_bootstrap_backfills_group_without_overwriting_saved_limits():
    from app.models import MemberGroup

    with transaction() as db:
        group = MemberGroup(name=f"Budget defaults {uuid4()}", configuration={})
        db.add(group)
        db.flush()
        scope = f"group:{group.id}"
        initialize_budgets(db)
        row = db.scalar(select(BudgetAccount).where(BudgetAccount.scope_key == scope))
        assert row.limit_micros == 10_000_000
        row.limit_micros = 2_000_000
        db.flush()
        initialize_budgets(db)
        assert row.limit_micros == 2_000_000
