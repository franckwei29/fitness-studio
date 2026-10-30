"""Durable owner attention inbox."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.mysql import DATETIME

revision = "g261030d001"
down_revision = "f261030c001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "owner_updates",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", DATETIME(fsp=6), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("business_key", sa.String(200), nullable=False),
        sa.Column("item", sa.JSON(), nullable=False),
        sa.Column("state", sa.String(30), nullable=False),
        sa.Column("read_at", sa.DateTime(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("user_id", "business_key"),
    )


def downgrade():
    op.drop_table("owner_updates")
