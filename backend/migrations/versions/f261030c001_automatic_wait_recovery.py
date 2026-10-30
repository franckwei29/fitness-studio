"""Persist bounded automatic recovery without changing financial evidence."""

import sqlalchemy as sa
from alembic import op

revision = "f261030c001"
down_revision = "e261030b001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("work_items", sa.Column("wait_context", sa.JSON(), nullable=True))
    op.add_column("work_items", sa.Column("resume_after", sa.DateTime(), nullable=True))
    op.add_column("work_items", sa.Column("resume_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("work_items", sa.Column("capacity_waits", sa.Integer(), nullable=False, server_default="0"))
    op.create_index("ix_work_items_resume_after", "work_items", ["resume_after"])


def downgrade():
    op.drop_index("ix_work_items_resume_after", table_name="work_items")
    for name in ("capacity_waits", "resume_count", "resume_after", "wait_context"):
        op.drop_column("work_items", name)
