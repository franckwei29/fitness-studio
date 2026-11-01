"""Searchable local food and exercise catalogues."""

import sqlalchemy as sa
from alembic import op

revision = "j261101a001"
down_revision = "i261030f001"
branch_labels = None
depends_on = None


def upgrade():
    for table in ("food_catalogue", "exercise_catalogue"):
        op.create_table(
            table,
            sa.Column("id", sa.String(80), primary_key=True),
            sa.Column("lookup_key", sa.String(64), nullable=False, unique=True),
            sa.Column("search_text", sa.Text(), nullable=False),
            sa.Column("data", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )


def downgrade():
    op.drop_table("exercise_catalogue")
    op.drop_table("food_catalogue")
