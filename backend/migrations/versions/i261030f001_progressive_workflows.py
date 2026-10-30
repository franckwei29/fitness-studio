"""Progressive plan stages and durable conversation ordering."""

import sqlalchemy as sa
from alembic import op

revision = "i261030f001"
down_revision = "h261030e001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "workflows",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("request_id", sa.String(36), sa.ForeignKey("requests.id"), unique=True, nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("stage", sa.String(40), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        *[sa.Column(name, sa.String(36)) for name in ("training_id", "nutrition_id", "pair_id")],
        *[sa.Column(name, sa.JSON()) for name in ("training_endorsement", "nutrition_endorsement")],
        sa.Column("baseline", sa.JSON(), nullable=False),
        sa.Column("feedback", sa.JSON(), nullable=False),
    )
    op.create_index("ix_workflows_user_id", "workflows", ["user_id"])
    op.create_table(
        "conversation_entries",
        sa.Column("sequence", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("session_id", sa.String(36), sa.ForeignKey("chat_sessions.id"), nullable=False),
        sa.Column("message_id", sa.String(36), sa.ForeignKey("messages.id"), unique=True, nullable=False),
    )
    op.create_index("ix_conversation_entries_session_id", "conversation_entries", ["session_id"])
    op.execute(
        "INSERT INTO conversation_entries (session_id, message_id) SELECT session_id, id FROM messages ORDER BY created_at, id"
    )


def downgrade():
    op.drop_table("conversation_entries")
    op.drop_table("workflows")
