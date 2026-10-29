"""Preserve Gemini history while adding explicit providers and generation settings."""

import sqlalchemy as sa
from alembic import op

revision = "d2401030a001"
down_revision = "c6c12f415f80"
branch_labels = None
depends_on = None


def upgrade():
    for table in ("agent_versions", "model_calls"):
        op.add_column(table, sa.Column("provider", sa.String(20), nullable=False, server_default="gemini"))
    op.add_column("agent_versions", sa.Column("max_output_tokens", sa.Integer(), nullable=True))
    op.add_column("agent_versions", sa.Column("reasoning_effort", sa.String(20), nullable=True))


def downgrade():
    op.drop_column("agent_versions", "reasoning_effort")
    op.drop_column("agent_versions", "max_output_tokens")
    for table in ("agent_versions", "model_calls"):
        op.drop_column(table, "provider")
