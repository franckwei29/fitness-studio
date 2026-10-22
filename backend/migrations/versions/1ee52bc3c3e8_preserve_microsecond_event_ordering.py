"""Preserve microsecond event ordering

Revision ID: 1ee52bc3c3e8
Revises: a41f74d9948c
Create Date: 2026-10-22 23:04:51.362641

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1ee52bc3c3e8'
down_revision: Union[str, Sequence[str], None] = 'a41f74d9948c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TABLES = ['activations', 'agent_versions', 'approvals', 'audit_events', 'budget_accounts', 'budget_reservations', 'change_requests', 'chat_sessions', 'idempotency_records', 'incidents', 'intake_assessments', 'login_sessions', 'member_groups', 'members', 'messages', 'model_calls', 'notifications', 'observations', 'plan_pairs', 'plan_versions', 'profile_snapshots', 'request_events', 'requests', 'users', 'validation_results', 'work_items']


def upgrade() -> None:
    for table in TABLES:
        op.execute(f"ALTER TABLE `{table}` MODIFY COLUMN created_at DATETIME(6) NOT NULL")


def downgrade() -> None:
    for table in TABLES:
        op.execute(f"ALTER TABLE `{table}` MODIFY COLUMN created_at DATETIME NOT NULL")
