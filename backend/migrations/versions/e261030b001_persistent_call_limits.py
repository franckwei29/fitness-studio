"""Add call policies without changing usage or financial ledgers."""

import sqlalchemy as sa
from alembic import op

revision = "e261030b001"
down_revision = "d2401030a001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "call_limits",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("scope_key", sa.String(200), nullable=False, unique=True),
        sa.Column("calls", sa.Integer(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint("calls IS NULL OR calls > 0", name="positive_call_limit"),
    )


def downgrade():
    op.drop_table("call_limits")
