"""Retain linked records while removing deleted conversations from the workspace."""

import sqlalchemy as sa
from alembic import op

revision = "h261030e001"
down_revision = "g261030d001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("chat_sessions", sa.Column("deleted_at", sa.DateTime(), nullable=True))


def downgrade():
    op.drop_column("chat_sessions", "deleted_at")
